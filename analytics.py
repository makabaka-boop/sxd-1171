from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from models import Stool, BorrowRecord, StoolStatus, LoadLevel, InspectionTask, InspectionTaskStatus, InspectionResult
from schemas import AlertItem, TurnoverDistributionItem, AbnormalAreaItem


LONG_BORROW_HOURS = 72
HIGH_LOAD_LEVELS = [LoadLevel.HEAVY, LoadLevel.EXTRA_HEAVY]
CONSECUTIVE_ISSUE_THRESHOLD = 3
UNCLEANED_REISSUE_WINDOW_HOURS = 24


def detect_long_borrow_alerts(db: Session) -> List[AlertItem]:
    alerts = []
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=LONG_BORROW_HOURS)

    records = db.query(BorrowRecord).join(Stool).filter(
        BorrowRecord.issued_at <= cutoff,
        BorrowRecord.returned_at.is_(None),
        BorrowRecord.is_detained == 0
    ).all()

    for rec in records:
        hours = round((now - rec.issued_at).total_seconds() / 3600, 1)
        severity = "high" if hours >= 168 else "medium" if hours >= 120 else "low"
        alerts.append(AlertItem(
            alert_type="借出时长过长",
            severity=severity,
            message=f"座凳 {rec.stool.stool_number} 已借出 {hours} 小时（阈值 {LONG_BORROW_HOURS}h）",
            related_id=rec.id,
            related_stool_number=rec.stool.stool_number,
            details={
                "borrow_hours": hours,
                "threshold_hours": LONG_BORROW_HOURS,
                "issued_at": rec.issued_at.isoformat() if rec.issued_at else None,
                "borrower": rec.borrower
            }
        ))
    return alerts


def detect_high_load_wear_alerts(db: Session) -> List[AlertItem]:
    alerts = []

    result = db.query(
        Stool.id,
        Stool.stool_number,
        Stool.load_level,
        Stool.storage_area,
        func.count(BorrowRecord.id).label("borrow_count"),
        func.sum(func.iif(BorrowRecord.appearance_issue.isnot(None), 1, 0)).label("issue_count")
    ).outerjoin(BorrowRecord, Stool.id == BorrowRecord.stool_id).filter(
        Stool.load_level.in_(HIGH_LOAD_LEVELS)
    ).group_by(Stool.id).all()

    for row in result:
        borrow_count = row.borrow_count or 0
        issue_count = row.issue_count or 0
        if borrow_count >= 5 and issue_count >= 2:
            issue_rate = round(issue_count / borrow_count * 100, 1)
            severity = "high" if issue_rate >= 50 else "medium" if issue_rate >= 30 else "low"
            alerts.append(AlertItem(
                alert_type="高承载座凳损耗集中",
                severity=severity,
                message=f"{row.load_level.value}座凳 {row.stool_number} 周转 {borrow_count} 次，出现 {issue_count} 次外观问题（问题率 {issue_rate}%）",
                related_id=row.id,
                related_stool_number=row.stool_number,
                details={
                    "load_level": row.load_level.value,
                    "borrow_count": borrow_count,
                    "issue_count": issue_count,
                    "issue_rate_percent": issue_rate,
                    "storage_area": row.storage_area
                }
            ))
    return alerts


def detect_area_consecutive_issues(db: Session) -> List[AlertItem]:
    alerts = []

    areas = db.query(Stool.storage_area).distinct().all()
    now = datetime.utcnow()
    window = now - timedelta(days=30)

    for (area,) in areas:
        area_issue_records = db.query(BorrowRecord).join(Stool).filter(
            Stool.storage_area == area,
            BorrowRecord.appearance_issue.isnot(None),
            BorrowRecord.created_at >= window
        ).order_by(BorrowRecord.created_at.desc()).limit(10).all()

        issue_stools = set()
        for rec in area_issue_records:
            issue_stools.add(rec.stool_id)

        recent_issues = len(area_issue_records)
        distinct_stools = len(issue_stools)

        if recent_issues >= CONSECUTIVE_ISSUE_THRESHOLD and distinct_stools >= 2:
            severity = "high" if recent_issues >= 6 else "medium" if recent_issues >= 4 else "low"
            alerts.append(AlertItem(
                alert_type="存放区域连续外观问题",
                severity=severity,
                message=f"区域「{area}」近30天出现 {recent_issues} 次外观问题，涉及 {distinct_stools} 个座凳",
                details={
                    "storage_area": area,
                    "recent_issue_count": recent_issues,
                    "affected_stool_count": distinct_stools,
                    "window_days": 30,
                    "threshold": CONSECUTIVE_ISSUE_THRESHOLD
                }
            ))
    return alerts


def detect_unreviewed_reissue_risks(db: Session) -> List[AlertItem]:
    alerts = []

    pending_review_stools = db.query(Stool).filter(
        Stool.status == StoolStatus.PENDING_REVIEW
    ).all()

    for stool in pending_review_stools:
        latest_record = db.query(BorrowRecord).filter(
            BorrowRecord.stool_id == stool.id
        ).order_by(BorrowRecord.created_at.desc()).first()

        if latest_record and latest_record.cleaned_at and not latest_record.reviewed_at:
            hours_since_clean = round(
                (datetime.utcnow() - latest_record.cleaned_at).total_seconds() / 3600, 1
            )
            severity = "high" if hours_since_clean >= 72 else "medium" if hours_since_clean >= 24 else "low"
            alerts.append(AlertItem(
                alert_type="清洁后未复核风险",
                severity=severity,
                message=f"座凳 {stool.stool_number} 清洁后已 {hours_since_clean} 小时未复核，存在提前发放风险",
                related_id=stool.id,
                related_stool_number=stool.stool_number,
                details={
                    "hours_since_clean": hours_since_clean,
                    "cleaned_at": latest_record.cleaned_at.isoformat() if latest_record.cleaned_at else None,
                    "cleaned_by": latest_record.cleaned_by,
                    "cleaning_result": latest_record.cleaning_result
                }
            ))
    return alerts


def detect_inspection_overdue_alerts(db: Session) -> List[AlertItem]:
    alerts = []
    now = datetime.utcnow()
    threshold_24h = now - timedelta(hours=24)
    threshold_3d = now - timedelta(days=3)
    threshold_7d = now - timedelta(days=7)

    pending_tasks = db.query(InspectionTask).filter(
        InspectionTask.task_status.in_([
            InspectionTaskStatus.PENDING,
            InspectionTaskStatus.IN_PROGRESS
        ])
    ).all()

    for task in pending_tasks:
        hours_overdue = round((now - task.scheduled_at).total_seconds() / 3600, 1)
        if hours_overdue <= 0:
            continue

        if task.scheduled_at <= threshold_7d:
            severity = "high"
        elif task.scheduled_at <= threshold_3d:
            severity = "medium"
        elif task.scheduled_at <= threshold_24h:
            severity = "low"
        else:
            continue

        alerts.append(AlertItem(
            alert_type="巡检任务超期",
            severity=severity,
            message=f"座凳 {task.stool_number} 的巡检任务已超期 {hours_overdue} 小时未处理",
            related_id=task.id,
            related_stool_number=task.stool_number,
            details={
                "task_id": task.id,
                "storage_area": task.storage_area,
                "responsible_person": task.responsible_person,
                "scheduled_at": task.scheduled_at.isoformat() if task.scheduled_at else None,
                "hours_overdue": hours_overdue,
                "source_type": task.source_type
            }
        ))
    return alerts


def detect_inspection_pending_review_alerts(db: Session) -> List[AlertItem]:
    alerts = []
    now = datetime.utcnow()
    threshold_24h = now - timedelta(hours=24)
    threshold_3d = now - timedelta(days=3)

    pending_review_tasks = db.query(InspectionTask).filter(
        InspectionTask.task_status == InspectionTaskStatus.ABNORMAL_PENDING_REVIEW
    ).all()

    for task in pending_review_tasks:
        if not task.inspected_at:
            continue
        hours_pending = round((now - task.inspected_at).total_seconds() / 3600, 1)

        if task.inspected_at <= threshold_3d:
            severity = "high"
        elif task.inspected_at <= threshold_24h:
            severity = "medium"
        else:
            severity = "low"

        alerts.append(AlertItem(
            alert_type="巡检异常待复核",
            severity=severity,
            message=f"座凳 {task.stool_number} 巡检异常已待复核 {hours_pending} 小时",
            related_id=task.id,
            related_stool_number=task.stool_number,
            details={
                "task_id": task.id,
                "storage_area": task.storage_area,
                "responsible_person": task.responsible_person,
                "inspected_at": task.inspected_at.isoformat() if task.inspected_at else None,
                "appearance_issue": task.appearance_issue,
                "appearance_issue_level": task.appearance_issue_level,
                "hours_pending": hours_pending
            }
        ))
    return alerts


def detect_inspection_detained_alerts(db: Session) -> List[AlertItem]:
    alerts = []
    now = datetime.utcnow()
    threshold_7d = now - timedelta(days=7)
    threshold_14d = now - timedelta(days=14)

    detained_tasks = db.query(InspectionTask).filter(
        InspectionTask.task_status == InspectionTaskStatus.ABNORMAL_DETAINED
    ).all()

    for task in detained_tasks:
        if not task.detained_at:
            continue
        days_detained = round((now - task.detained_at).total_seconds() / 86400, 1)

        if task.detained_at <= threshold_14d:
            severity = "high"
        elif task.detained_at <= threshold_7d:
            severity = "medium"
        else:
            severity = "low"

        alerts.append(AlertItem(
            alert_type="巡检异常留置超时",
            severity=severity,
            message=f"座凳 {task.stool_number} 因巡检异常已留置 {days_detained} 天",
            related_id=task.id,
            related_stool_number=task.stool_number,
            details={
                "task_id": task.id,
                "storage_area": task.storage_area,
                "responsible_person": task.responsible_person,
                "detained_at": task.detained_at.isoformat() if task.detained_at else None,
                "detained_reason": task.detained_reason,
                "days_detained": days_detained
            }
        ))
    return alerts


def get_all_alerts(db: Session, alert_type: Optional[str] = None, severity: Optional[str] = None) -> List[AlertItem]:
    all_alerts = []
    all_alerts.extend(detect_long_borrow_alerts(db))
    all_alerts.extend(detect_high_load_wear_alerts(db))
    all_alerts.extend(detect_area_consecutive_issues(db))
    all_alerts.extend(detect_unreviewed_reissue_risks(db))
    all_alerts.extend(detect_inspection_overdue_alerts(db))
    all_alerts.extend(detect_inspection_pending_review_alerts(db))
    all_alerts.extend(detect_inspection_detained_alerts(db))

    if alert_type:
        all_alerts = [a for a in all_alerts if a.alert_type == alert_type]
    if severity:
        all_alerts = [a for a in all_alerts if a.severity == severity]

    all_alerts.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.severity, 3))
    return all_alerts


def get_turnover_distribution(
    db: Session,
    storage_area: Optional[str] = None,
    load_level: Optional[LoadLevel] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> Tuple[int, Optional[float], List[TurnoverDistributionItem]]:
    query = db.query(BorrowRecord).join(Stool).filter(
        BorrowRecord.turnover_hours.isnot(None)
    )

    if storage_area:
        query = query.filter(Stool.storage_area.like(f"%{storage_area}%"))
    if load_level:
        query = query.filter(Stool.load_level == load_level)
    if date_from:
        query = query.filter(BorrowRecord.returned_at >= date_from)
    if date_to:
        query = query.filter(BorrowRecord.returned_at <= date_to)

    records = query.all()
    total = len(records)

    ranges_def = [
        ("0-4小时", 0, 4),
        ("4-12小时", 4, 12),
        ("12-24小时", 12, 24),
        ("24-72小时", 24, 72),
        ("72小时以上", 72, float("inf")),
    ]

    distribution = []
    total_hours = 0.0
    count_data = []

    for rec in records:
        h = rec.turnover_hours or 0
        total_hours += h

    for label, min_h, max_h in ranges_def:
        count = 0
        for rec in records:
            h = rec.turnover_hours or 0
            if min_h <= h < max_h:
                count += 1
        count_data.append((label, count))

    avg_hours = round(total_hours / total, 2) if total > 0 else None

    for label, count in count_data:
        distribution.append(TurnoverDistributionItem(
            range=label,
            count=count,
            percentage=round(count / total * 100, 2) if total > 0 else 0.0
        ))

    return total, avg_hours, distribution


def get_abnormal_areas(
    db: Session,
    min_abnormal_rate: float = 5.0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> Tuple[int, List[AbnormalAreaItem]]:
    base_query = db.query(BorrowRecord).join(Stool)
    recent_window = datetime.utcnow() - timedelta(days=7)

    if date_from:
        base_query = base_query.filter(BorrowRecord.created_at >= date_from)
    if date_to:
        base_query = base_query.filter(BorrowRecord.created_at <= date_to)

    all_records = base_query.all()

    inspection_query = db.query(InspectionTask)
    if date_from:
        inspection_query = inspection_query.filter(InspectionTask.created_at >= date_from)
    if date_to:
        inspection_query = inspection_query.filter(InspectionTask.created_at <= date_to)
    all_inspection_tasks = inspection_query.all()

    area_stats = {}
    for rec in all_records:
        area = rec.stool.storage_area
        if area not in area_stats:
            area_stats[area] = {
                "total": 0,
                "abnormal": 0,
                "recent_abnormal": 0,
                "borrow_total": 0,
                "inspection_total": 0,
                "inspection_abnormal": 0
            }
        area_stats[area]["total"] += 1
        area_stats[area]["borrow_total"] += 1
        is_abnormal = (
            rec.is_detained == 1
            or (rec.appearance_issue_level in ["严重"])
            or (rec.review_result == "需留置")
        )
        if is_abnormal:
            area_stats[area]["abnormal"] += 1
            if rec.created_at >= recent_window:
                area_stats[area]["recent_abnormal"] += 1

    for task in all_inspection_tasks:
        area = task.storage_area
        if area not in area_stats:
            area_stats[area] = {
                "total": 0,
                "abnormal": 0,
                "recent_abnormal": 0,
                "borrow_total": 0,
                "inspection_total": 0,
                "inspection_abnormal": 0
            }
        area_stats[area]["total"] += 1
        area_stats[area]["inspection_total"] += 1
        is_abnormal = (
            task.inspection_result == InspectionResult.ABNORMAL
            or task.is_detained == 1
            or task.task_status == InspectionTaskStatus.ABNORMAL_DETAINED
        )
        if is_abnormal:
            area_stats[area]["abnormal"] += 1
            area_stats[area]["inspection_abnormal"] += 1
            if task.created_at >= recent_window:
                area_stats[area]["recent_abnormal"] += 1

    items = []
    for area, stats in area_stats.items():
        if stats["total"] == 0:
            continue
        rate = round(stats["abnormal"] / stats["total"] * 100, 2)
        if rate >= min_abnormal_rate:
            items.append(AbnormalAreaItem(
                storage_area=area,
                total_records=stats["total"],
                abnormal_count=stats["abnormal"],
                abnormal_rate=rate,
                recent_abnormal_count=stats["recent_abnormal"]
            ))

    items.sort(key=lambda x: (-x.abnormal_rate, -x.abnormal_count))
    return len(items), items


def get_pending_review_records(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    storage_area: Optional[str] = None
) -> dict:
    now = datetime.utcnow()
    threshold_24h = now - timedelta(hours=24)
    threshold_3d = now - timedelta(days=3)

    query = db.query(BorrowRecord).join(Stool).filter(
        BorrowRecord.cleaned_at.isnot(None),
        BorrowRecord.reviewed_at.is_(None),
        BorrowRecord.is_detained == 0
    )

    if storage_area:
        query = query.filter(Stool.storage_area.like(f"%{storage_area}%"))

    all_pending = query.all()
    total = len(all_pending)

    over_24h = sum(1 for r in all_pending if r.cleaned_at and r.cleaned_at <= threshold_24h)
    over_3d = sum(1 for r in all_pending if r.cleaned_at and r.cleaned_at <= threshold_3d)

    items = all_pending[skip:skip + limit]

    return {
        "total_pending": total,
        "over_24h_count": over_24h,
        "over_3d_count": over_3d,
        "items": items
    }
