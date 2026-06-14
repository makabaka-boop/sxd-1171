from datetime import datetime, timedelta
from typing import Optional, List
import json
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from models import (
    Stool, BorrowRecord, StoolStatus, LoadLevel,
    InspectionTask, InspectionTaskStatus, InspectionResult,
    InspectionTaskLog
)
from schemas import (
    StoolCreate, StoolUpdate,
    IssueStoolRequest, ReturnStoolRequest,
    CleanStoolRequest, ReviewStoolRequest, DetainStoolRequest,
    GenerateInspectionTasksRequest, SubmitInspectionResultRequest,
    ReviewInspectionTaskRequest, RestoreInspectedStoolRequest
)
from exceptions import ValidationException, StatusConflictException, NotFoundException


def validate_stool_create(db: Session, data: StoolCreate):
    if not data.stool_number or not data.stool_number.strip():
        raise ValidationException("座凳编号不能为空", "stool_number")
    existing = db.query(Stool).filter(Stool.stool_number == data.stool_number).first()
    if existing:
        raise ValidationException(f"座凳编号已存在: {data.stool_number}", "stool_number")
    if data.inspection_cycle_days <= 0:
        raise ValidationException("巡查周期必须大于0天", "inspection_cycle_days")


def create_stool(db: Session, data: StoolCreate) -> Stool:
    validate_stool_create(db, data)
    stool = Stool(
        stool_number=data.stool_number.strip(),
        storage_area=data.storage_area.strip(),
        load_level=data.load_level,
        inspection_cycle_days=data.inspection_cycle_days,
        responsible_person=data.responsible_person.strip(),
        status=StoolStatus.PENDING_ISSUE
    )
    db.add(stool)
    db.commit()
    db.refresh(stool)
    return stool


def get_stool(db: Session, stool_id: int) -> Optional[Stool]:
    return db.query(Stool).filter(Stool.id == stool_id).first()


def get_stool_by_number(db: Session, number: str) -> Optional[Stool]:
    return db.query(Stool).filter(Stool.stool_number == number).first()


def list_stools(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    storage_area: Optional[str] = None,
    load_level: Optional[LoadLevel] = None,
    responsible_person: Optional[str] = None,
    status: Optional[StoolStatus] = None,
    is_abnormal: Optional[bool] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
):
    query = db.query(Stool)

    if storage_area:
        query = query.filter(Stool.storage_area.like(f"%{storage_area}%"))
    if load_level:
        query = query.filter(Stool.load_level == load_level)
    if responsible_person:
        query = query.filter(Stool.responsible_person.like(f"%{responsible_person}%"))
    if status:
        query = query.filter(Stool.status == status)
    if is_abnormal is not None:
        if is_abnormal:
            query = query.filter(Stool.status == StoolStatus.DETAINED)
        else:
            query = query.filter(Stool.status != StoolStatus.DETAINED)
    if date_from:
        query = query.filter(Stool.created_at >= date_from)
    if date_to:
        query = query.filter(Stool.created_at <= date_to)

    total = query.count()
    items = query.order_by(Stool.updated_at.desc()).offset(skip).limit(limit).all()
    return total, items


def update_stool(db: Session, stool_id: int, data: StoolUpdate) -> Stool:
    stool = get_stool(db, stool_id)
    if not stool:
        raise NotFoundException("座凳", str(stool_id))

    update_data = data.model_dump(exclude_unset=True)

    if "status" in update_data and update_data["status"] is not None:
        new_status = update_data["status"]
        current_status = stool.status

        if current_status == StoolStatus.BORROWED:
            raise StatusConflictException(
                f"座凳正处于「{current_status.value}」状态，存在活跃借出记录，不可直接修改状态",
                current_status=current_status.value,
                expected_status="通过正常借还流程更新状态"
            )

        if new_status == StoolStatus.RESTORED:
            latest_record = db.query(BorrowRecord).filter(
                BorrowRecord.stool_id == stool_id
            ).order_by(BorrowRecord.created_at.desc()).first()
            if latest_record and latest_record.returned_at and not latest_record.reviewed_at:
                raise StatusConflictException(
                    "座凳存在未完成复核的借阅记录，不可直接标记为恢复可用",
                    current_status=current_status.value,
                    expected_status="通过复核流程完成后自动更新"
                )

        valid_direct_changes = {
            (StoolStatus.PENDING_ISSUE, StoolStatus.RESTORED): True,
            (StoolStatus.RESTORED, StoolStatus.PENDING_ISSUE): True,
        }
        if (current_status, new_status) not in valid_direct_changes and new_status != current_status:
            raise StatusConflictException(
                f"不允许直接从「{current_status.value}」修改为「{new_status.value}」，请通过业务流程操作",
                current_status=current_status.value,
                expected_status="使用发放/回收/清洁/复核等流程接口更新状态"
            )

    for key, value in update_data.items():
        if value is not None:
            if isinstance(value, str) and hasattr(data, key):
                value = value.strip() if value else value
            setattr(stool, key, value)

    stool.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(stool)
    return stool


def delete_stool(db: Session, stool_id: int):
    stool = get_stool(db, stool_id)
    if not stool:
        raise NotFoundException("座凳", str(stool_id))
    active_record = db.query(BorrowRecord).filter(
        BorrowRecord.stool_id == stool_id,
        BorrowRecord.returned_at.is_(None),
        BorrowRecord.is_detained == 0
    ).first()
    if active_record:
        raise StatusConflictException(
            "座凳存在活跃借出记录，无法删除",
            current_status=stool.status.value,
            expected_status="无活跃借出"
        )
    db.delete(stool)
    db.commit()


def issue_stool(db: Session, data: IssueStoolRequest) -> BorrowRecord:
    stool = get_stool(db, data.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(data.stool_id))

    if stool.status == StoolStatus.DETAINED:
        raise StatusConflictException(
            "座凳已被异常留置，不能发放",
            current_status=stool.status.value,
            expected_status=f"{StoolStatus.PENDING_ISSUE.value} 或 {StoolStatus.RESTORED.value}"
        )

    if stool.status == StoolStatus.BORROWED:
        active = db.query(BorrowRecord).filter(
            BorrowRecord.stool_id == data.stool_id,
            BorrowRecord.returned_at.is_(None)
        ).first()
        if active:
            raise StatusConflictException(
                f"座凳 {stool.stool_number} 正处于借出状态，不可重复发放",
                current_status=stool.status.value,
                expected_status=f"{StoolStatus.PENDING_ISSUE.value} 或 {StoolStatus.RESTORED.value}"
            )

    if stool.status not in [StoolStatus.PENDING_ISSUE, StoolStatus.RESTORED]:
        raise StatusConflictException(
            f"座凳当前状态为 {stool.status.value}，不可发放",
            current_status=stool.status.value,
            expected_status=f"{StoolStatus.PENDING_ISSUE.value} 或 {StoolStatus.RESTORED.value}"
        )

    latest_record = db.query(BorrowRecord).filter(
        BorrowRecord.stool_id == data.stool_id
    ).order_by(BorrowRecord.created_at.desc()).first()

    if latest_record and latest_record.cleaned_at and not latest_record.reviewed_at \
            and latest_record.is_detained == 0:
        raise StatusConflictException(
            f"座凳 {stool.stool_number} 清洁后尚未复核，不能重新发放",
            current_status=stool.status.value,
            expected_status="已复核完成"
        )

    record = BorrowRecord(
        stool_id=data.stool_id,
        borrower=data.borrower.strip() if data.borrower else None,
        borrower_contact=data.borrower_contact.strip() if data.borrower_contact else None,
        issued_at=datetime.utcnow(),
        issued_by=data.issued_by.strip()
    )
    db.add(record)

    stool.status = StoolStatus.BORROWED
    stool.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(record)
    return record


def get_borrow_record(db: Session, record_id: int) -> Optional[BorrowRecord]:
    return db.query(BorrowRecord).filter(BorrowRecord.id == record_id).first()


def return_stool(db: Session, data: ReturnStoolRequest) -> BorrowRecord:
    record = get_borrow_record(db, data.record_id)
    if not record:
        raise NotFoundException("借阅记录", str(data.record_id))

    if record.is_detained == 1:
        raise StatusConflictException(
            "该借阅记录已被标记为异常留置，不能继续回收入库",
            current_status=StoolStatus.DETAINED.value,
            expected_status="非留置状态"
        )

    if record.returned_at:
        stool = get_stool(db, record.stool_id)
        raise StatusConflictException(
            "该借阅记录已完成回收，不可重复操作",
            current_status=stool.status.value if stool else "已回收",
            expected_status=StoolStatus.BORROWED.value
        )

    stool = get_stool(db, record.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(record.stool_id))

    if stool.status != StoolStatus.BORROWED:
        raise StatusConflictException(
            f"座凳当前状态为 {stool.status.value}，不能执行回收",
            current_status=stool.status.value,
            expected_status=StoolStatus.BORROWED.value
        )

    if not record.issued_at:
        raise StatusConflictException(
            "借阅记录未完成发放，无法回收",
            current_status="未发放",
            expected_status="已发放"
        )

    now = datetime.utcnow()
    record.returned_at = now
    record.returned_by = data.returned_by.strip()
    if data.appearance_issue:
        record.appearance_issue = data.appearance_issue.strip()
    if data.appearance_issue_level:
        record.appearance_issue_level = data.appearance_issue_level.strip()

    if record.issued_at:
        delta = now - record.issued_at
        record.turnover_hours = round(delta.total_seconds() / 3600, 2)

    stool.status = StoolStatus.PENDING_CLEANING
    stool.updated_at = now

    db.commit()
    db.refresh(record)
    return record


def clean_stool(db: Session, data: CleanStoolRequest) -> BorrowRecord:
    record = get_borrow_record(db, data.record_id)
    if not record:
        raise NotFoundException("借阅记录", str(data.record_id))

    if record.is_detained == 1:
        raise StatusConflictException(
            "该记录已被异常留置，不能执行清洁操作",
            current_status=StoolStatus.DETAINED.value,
            expected_status="非留置状态"
        )

    if not record.returned_at:
        raise StatusConflictException(
            "借阅记录尚未完成回收，无法清洁",
            current_status="未回收",
            expected_status="已回收"
        )

    if record.cleaned_at:
        raise StatusConflictException(
            "该记录已完成清洁，不可重复操作",
            current_status="已清洁",
            expected_status=StoolStatus.PENDING_CLEANING.value
        )

    stool = get_stool(db, record.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(record.stool_id))

    if stool.status != StoolStatus.PENDING_CLEANING:
        raise StatusConflictException(
            f"座凳当前状态为 {stool.status.value}，不能执行清洁",
            current_status=stool.status.value,
            expected_status=StoolStatus.PENDING_CLEANING.value
        )

    valid_results = ["合格", "不合格", "需复核"]
    if data.cleaning_result not in valid_results:
        raise ValidationException(
            f"清洁结果必须为 {valid_results} 之一",
            "cleaning_result"
        )

    now = datetime.utcnow()
    record.cleaned_at = now
    record.cleaned_by = data.cleaned_by.strip()
    record.cleaning_result = data.cleaning_result.strip()
    if data.cleaning_note:
        record.cleaning_note = data.cleaning_note.strip()

    stool.status = StoolStatus.PENDING_REVIEW
    stool.updated_at = now

    db.commit()
    db.refresh(record)
    return record


def review_stool(db: Session, data: ReviewStoolRequest) -> BorrowRecord:
    record = get_borrow_record(db, data.record_id)
    if not record:
        raise NotFoundException("借阅记录", str(data.record_id))

    if record.is_detained == 1:
        raise StatusConflictException(
            "该记录已被异常留置，不能执行复核操作",
            current_status=StoolStatus.DETAINED.value,
            expected_status="非留置状态"
        )

    if not record.cleaned_at:
        raise StatusConflictException(
            "借阅记录尚未完成清洁，无法复核",
            current_status="未清洁",
            expected_status="已清洁"
        )

    if record.reviewed_at:
        raise StatusConflictException(
            "该记录已完成复核，不可重复操作",
            current_status="已复核",
            expected_status=StoolStatus.PENDING_REVIEW.value
        )

    stool = get_stool(db, record.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(record.stool_id))

    if stool.status != StoolStatus.PENDING_REVIEW:
        raise StatusConflictException(
            f"座凳当前状态为 {stool.status.value}，不能执行复核",
            current_status=stool.status.value,
            expected_status=StoolStatus.PENDING_REVIEW.value
        )

    valid_results = ["恢复可用", "需留置", "再次清洁"]
    if data.review_result not in valid_results:
        raise ValidationException(
            f"复核结果必须为 {valid_results} 之一",
            "review_result"
        )

    now = datetime.utcnow()
    record.reviewed_at = now
    record.reviewed_by = data.reviewed_by.strip()
    record.review_result = data.review_result.strip()
    if data.review_note:
        record.review_note = data.review_note.strip()

    if data.review_result == "恢复可用":
        stool.status = StoolStatus.RESTORED
    elif data.review_result == "需留置":
        stool.status = StoolStatus.DETAINED
        record.is_detained = 1
        record.detained_at = now
        record.detained_by = data.reviewed_by.strip()
        record.detained_reason = f"复核留置: {data.review_note or '复核未通过'}"
    elif data.review_result == "再次清洁":
        stool.status = StoolStatus.PENDING_CLEANING
        record.cleaned_at = None
        record.cleaned_by = None
        record.cleaning_result = None
        record.cleaning_note = None
        record.reviewed_at = None
        record.reviewed_by = None
        record.review_result = None
        record.review_note = None

    stool.updated_at = now

    db.commit()
    db.refresh(record)
    return record


def detain_stool(db: Session, data: DetainStoolRequest) -> BorrowRecord:
    record = get_borrow_record(db, data.record_id)
    if not record:
        raise NotFoundException("借阅记录", str(data.record_id))

    if record.is_detained == 1:
        raise StatusConflictException(
            "该记录已被留置，不可重复操作",
            current_status=StoolStatus.DETAINED.value,
            expected_status="非留置状态"
        )

    stool = get_stool(db, record.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(record.stool_id))

    if stool.status == StoolStatus.PENDING_ISSUE or stool.status == StoolStatus.RESTORED:
        raise StatusConflictException(
            f"座凳当前状态为 {stool.status.value}，无需留置",
            current_status=stool.status.value,
            expected_status="借出/清洁/复核中状态"
        )

    now = datetime.utcnow()
    record.is_detained = 1
    record.detained_at = now
    record.detained_by = data.detained_by.strip()
    record.detained_reason = data.detained_reason.strip()

    stool.status = StoolStatus.DETAINED
    stool.updated_at = now

    db.commit()
    db.refresh(record)
    return record


def list_borrow_records(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    stool_id: Optional[int] = None,
    storage_area: Optional[str] = None,
    load_level: Optional[LoadLevel] = None,
    responsible_person: Optional[str] = None,
    status: Optional[StoolStatus] = None,
    is_abnormal: Optional[bool] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
):
    query = db.query(BorrowRecord).join(Stool, BorrowRecord.stool_id == Stool.id)

    if stool_id:
        query = query.filter(BorrowRecord.stool_id == stool_id)
    if storage_area:
        query = query.filter(Stool.storage_area.like(f"%{storage_area}%"))
    if load_level:
        query = query.filter(Stool.load_level == load_level)
    if responsible_person:
        query = query.filter(Stool.responsible_person.like(f"%{responsible_person}%"))
    if status:
        query = query.filter(Stool.status == status)
    if is_abnormal is not None:
        query = query.filter(BorrowRecord.is_detained == (1 if is_abnormal else 0))
    if date_from:
        query = query.filter(BorrowRecord.created_at >= date_from)
    if date_to:
        query = query.filter(BorrowRecord.created_at <= date_to)

    total = query.count()
    items = query.order_by(BorrowRecord.created_at.desc()).offset(skip).limit(limit).all()
    return total, items


def enrich_record(record: BorrowRecord, db: Session) -> dict:
    data = record.__dict__.copy()
    stool = get_stool(db, record.stool_id)
    if stool:
        data["stool_number"] = stool.stool_number
    return data


def enrich_records(records: List[BorrowRecord], db: Session) -> List[dict]:
    result = []
    for r in records:
        enriched = r.__dict__.copy()
        if "_sa_instance_state" in enriched:
            del enriched["_sa_instance_state"]
        stool = get_stool(db, r.stool_id)
        if stool:
            enriched["stool_number"] = stool.stool_number
        result.append(enriched)
    return result


def add_inspection_log(
    db: Session,
    task_id: int,
    action_type: str,
    action_by: str,
    description: str = None,
    from_status: str = None,
    to_status: str = None,
    details: dict = None
) -> InspectionTaskLog:
    log = InspectionTaskLog(
        task_id=task_id,
        action_type=action_type,
        action_by=action_by.strip(),
        description=description,
        from_status=from_status,
        to_status=to_status,
        details=json.dumps(details, ensure_ascii=False) if details else None
    )
    db.add(log)
    db.flush()
    return log


def get_last_inspection_time(db: Session, stool_id: int) -> Optional[datetime]:
    last_task = db.query(InspectionTask).filter(
        InspectionTask.stool_id == stool_id,
        InspectionTask.inspected_at.isnot(None),
        InspectionTask.task_status.in_([
            InspectionTaskStatus.COMPLETED_NORMAL,
            InspectionTaskStatus.COMPLETED_ABNORMAL,
            InspectionTaskStatus.ABNORMAL_DETAINED,
            InspectionTaskStatus.ABNORMAL_PENDING_REVIEW,
            InspectionTaskStatus.CLOSED
        ])
    ).order_by(InspectionTask.inspected_at.desc()).first()
    return last_task.inspected_at if last_task else None


def generate_inspection_tasks(db: Session, source_type: str = "周期巡检") -> List[InspectionTask]:
    now = datetime.utcnow()
    created_tasks = []

    stools = db.query(Stool).filter(
        Stool.status.in_([
            StoolStatus.PENDING_ISSUE,
            StoolStatus.RESTORED
        ])
    ).all()

    for stool in stools:
        last_inspected_at = get_last_inspection_time(db, stool.id)
        inspection_base_time = last_inspected_at or stool.created_at
        next_inspection_date = inspection_base_time + timedelta(days=stool.inspection_cycle_days)
        if next_inspection_date > now:
            continue

        pending_task = db.query(InspectionTask).filter(
            InspectionTask.stool_id == stool.id,
            InspectionTask.task_status.in_([
                InspectionTaskStatus.PENDING,
                InspectionTaskStatus.IN_PROGRESS
            ])
        ).first()
        if pending_task:
            continue

        task = InspectionTask(
            stool_id=stool.id,
            stool_number=stool.stool_number,
            storage_area=stool.storage_area,
            load_level=stool.load_level,
            responsible_person=stool.responsible_person,
            task_status=InspectionTaskStatus.PENDING,
            inspection_cycle_days=stool.inspection_cycle_days,
            last_inspection_at=last_inspected_at,
            scheduled_at=now,
            source_type=source_type
        )
        db.add(task)
        db.flush()

        add_inspection_log(
            db, task.id, "任务生成", "system",
            description=f"系统自动生成巡检任务，来源：{source_type}",
            from_status=None,
            to_status=InspectionTaskStatus.PENDING.value,
            details={"stool_number": stool.stool_number, "source_type": source_type}
        )

        created_tasks.append(task)

    db.commit()
    for task in created_tasks:
        db.refresh(task)
    return created_tasks


def get_inspection_task(db: Session, task_id: int) -> Optional[InspectionTask]:
    return db.query(InspectionTask).filter(InspectionTask.id == task_id).first()


def list_inspection_tasks(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    storage_area: Optional[str] = None,
    responsible_person: Optional[str] = None,
    task_status: Optional[InspectionTaskStatus] = None,
    load_level: Optional[LoadLevel] = None,
    source_type: Optional[str] = None,
    is_abnormal: Optional[bool] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
):
    query = db.query(InspectionTask)

    if storage_area:
        query = query.filter(InspectionTask.storage_area.like(f"%{storage_area}%"))
    if responsible_person:
        query = query.filter(InspectionTask.responsible_person.like(f"%{responsible_person}%"))
    if task_status:
        query = query.filter(InspectionTask.task_status == task_status)
    if load_level:
        query = query.filter(InspectionTask.load_level == load_level)
    if source_type:
        query = query.filter(InspectionTask.source_type == source_type)
    if is_abnormal is not None:
        if is_abnormal:
            query = query.filter(InspectionTask.inspection_result == InspectionResult.ABNORMAL)
        else:
            query = query.filter(
                or_(
                    InspectionTask.inspection_result == InspectionResult.NORMAL,
                    InspectionTask.inspection_result.is_(None)
                )
            )
    if date_from:
        query = query.filter(InspectionTask.created_at >= date_from)
    if date_to:
        query = query.filter(InspectionTask.created_at <= date_to)

    total = query.count()
    items = query.order_by(InspectionTask.created_at.desc()).offset(skip).limit(limit).all()
    return total, items


def submit_inspection_result(db: Session, data: SubmitInspectionResultRequest) -> InspectionTask:
    task = get_inspection_task(db, data.task_id)
    if not task:
        raise NotFoundException("巡检任务", str(data.task_id))

    if task.task_status not in [InspectionTaskStatus.PENDING, InspectionTaskStatus.IN_PROGRESS]:
        raise StatusConflictException(
            f"巡检任务当前状态为「{task.task_status.value}」，不可提交巡检结果",
            current_status=task.task_status.value,
            expected_status=f"{InspectionTaskStatus.PENDING.value} 或 {InspectionTaskStatus.IN_PROGRESS.value}"
        )

    stool = get_stool(db, task.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(task.stool_id))

    now = datetime.utcnow()
    old_status = task.task_status

    task.inspection_result = data.inspection_result
    task.inspected_by = data.inspected_by.strip()
    task.inspected_at = now

    if data.appearance_issue:
        task.appearance_issue = data.appearance_issue.strip()
    if data.appearance_issue_level:
        task.appearance_issue_level = data.appearance_issue_level.strip()
    if data.handling_suggestion:
        task.handling_suggestion = data.handling_suggestion.strip()

    if data.inspection_result == InspectionResult.NORMAL:
        task.task_status = InspectionTaskStatus.COMPLETED_NORMAL
        stool.status = StoolStatus.RESTORED

        add_inspection_log(
            db, task.id, "提交巡检结果", data.inspected_by,
            description="巡检结果正常，座凳恢复可用状态",
            from_status=old_status.value,
            to_status=InspectionTaskStatus.COMPLETED_NORMAL.value,
            details={"inspection_result": "正常"}
        )
    else:
        abnormal_action = data.abnormal_action or "待复核"
        issue_text = data.appearance_issue.strip() if data.appearance_issue else "巡检发现外观异常"
        inspection_record = BorrowRecord(
            stool_id=task.stool_id,
            returned_at=now,
            returned_by=data.inspected_by.strip(),
            appearance_issue=issue_text,
            appearance_issue_level=data.appearance_issue_level.strip() if data.appearance_issue_level else None,
            source_type="巡检异常",
            source_task_id=task.id,
            created_at=now
        )
        db.add(inspection_record)

        if abnormal_action == "留置":
            task.task_status = InspectionTaskStatus.ABNORMAL_DETAINED
            task.is_detained = 1
            task.detained_at = now
            task.detained_by = data.inspected_by.strip()
            task.detained_reason = f"巡检异常留置: {issue_text}"
            inspection_record.is_detained = 1
            inspection_record.detained_at = now
            inspection_record.detained_by = data.inspected_by.strip()
            inspection_record.detained_reason = task.detained_reason
            stool.status = StoolStatus.DETAINED

            add_inspection_log(
                db, task.id, "提交巡检结果", data.inspected_by,
                description="巡检发现异常，座凳已异常留置",
                from_status=old_status.value,
                to_status=InspectionTaskStatus.ABNORMAL_DETAINED.value,
                details={
                    "inspection_result": "异常",
                    "abnormal_action": "留置",
                    "appearance_issue": data.appearance_issue,
                    "appearance_issue_level": data.appearance_issue_level
                }
            )
        else:
            task.task_status = InspectionTaskStatus.ABNORMAL_PENDING_REVIEW
            stool.status = StoolStatus.PENDING_INSPECTION_REVIEW

            add_inspection_log(
                db, task.id, "提交巡检结果", data.inspected_by,
                description="巡检发现异常，进入待复核流程",
                from_status=old_status.value,
                to_status=InspectionTaskStatus.ABNORMAL_PENDING_REVIEW.value,
                details={
                    "inspection_result": "异常",
                    "abnormal_action": "待复核",
                    "appearance_issue": data.appearance_issue,
                    "appearance_issue_level": data.appearance_issue_level,
                    "handling_suggestion": data.handling_suggestion
                }
            )

    task.updated_at = now
    stool.updated_at = now

    db.commit()
    db.refresh(task)
    return task


def review_inspection_task(db: Session, data: ReviewInspectionTaskRequest) -> InspectionTask:
    task = get_inspection_task(db, data.task_id)
    if not task:
        raise NotFoundException("巡检任务", str(data.task_id))

    if task.task_status != InspectionTaskStatus.ABNORMAL_PENDING_REVIEW:
        raise StatusConflictException(
            f"巡检任务当前状态为「{task.task_status.value}」，不可执行复核",
            current_status=task.task_status.value,
            expected_status=InspectionTaskStatus.ABNORMAL_PENDING_REVIEW.value
        )

    stool = get_stool(db, task.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(task.stool_id))

    valid_results = ["恢复可用", "需留置", "再次巡检"]
    if data.review_result not in valid_results:
        raise ValidationException(
            f"复核结果必须为 {valid_results} 之一",
            "review_result"
        )

    now = datetime.utcnow()
    old_status = task.task_status

    task.reviewed_by = data.reviewed_by.strip()
    task.reviewed_at = now
    task.review_result = data.review_result.strip()
    if data.review_note:
        task.review_note = data.review_note.strip()

    if data.review_result == "恢复可用":
        task.task_status = InspectionTaskStatus.COMPLETED_ABNORMAL
        stool.status = StoolStatus.RESTORED

        add_inspection_log(
            db, task.id, "复核完成", data.reviewed_by,
            description="复核通过，座凳恢复可用",
            from_status=old_status.value,
            to_status=InspectionTaskStatus.COMPLETED_ABNORMAL.value,
            details={"review_result": "恢复可用", "review_note": data.review_note}
        )
    elif data.review_result == "需留置":
        task.task_status = InspectionTaskStatus.ABNORMAL_DETAINED
        task.is_detained = 1
        task.detained_at = now
        task.detained_by = data.reviewed_by.strip()
        task.detained_reason = f"复核留置: {data.review_note or '复核未通过'}"
        stool.status = StoolStatus.DETAINED

        add_inspection_log(
            db, task.id, "复核完成", data.reviewed_by,
            description="复核未通过，座凳异常留置",
            from_status=old_status.value,
            to_status=InspectionTaskStatus.ABNORMAL_DETAINED.value,
            details={"review_result": "需留置", "review_note": data.review_note}
        )
    elif data.review_result == "再次巡检":
        task.task_status = InspectionTaskStatus.IN_PROGRESS
        task.inspected_at = None
        task.inspected_by = None
        task.inspection_result = None
        task.appearance_issue = None
        task.appearance_issue_level = None
        task.handling_suggestion = None
        task.reviewed_at = None
        task.reviewed_by = None
        task.review_result = None
        task.review_note = None
        stool.status = StoolStatus.PENDING_INSPECTION

        add_inspection_log(
            db, task.id, "复核完成", data.reviewed_by,
            description="复核要求再次巡检",
            from_status=old_status.value,
            to_status=InspectionTaskStatus.IN_PROGRESS.value,
            details={"review_result": "再次巡检", "review_note": data.review_note}
        )

    task.updated_at = now
    stool.updated_at = now

    db.commit()
    db.refresh(task)
    return task


def restore_inspected_stool(db: Session, data: RestoreInspectedStoolRequest) -> InspectionTask:
    task = get_inspection_task(db, data.task_id)
    if not task:
        raise NotFoundException("巡检任务", str(data.task_id))

    if task.task_status != InspectionTaskStatus.ABNORMAL_DETAINED:
        raise StatusConflictException(
            f"巡检任务当前状态为「{task.task_status.value}」，不可恢复",
            current_status=task.task_status.value,
            expected_status=InspectionTaskStatus.ABNORMAL_DETAINED.value
        )

    stool = get_stool(db, task.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(task.stool_id))

    if stool.status != StoolStatus.DETAINED:
        raise StatusConflictException(
            f"座凳当前状态为「{stool.status.value}」，不可恢复",
            current_status=stool.status.value,
            expected_status=StoolStatus.DETAINED.value
        )

    now = datetime.utcnow()
    old_status = task.task_status

    task.task_status = InspectionTaskStatus.CLOSED
    task.updated_at = now

    stool.status = StoolStatus.RESTORED
    stool.updated_at = now

    add_inspection_log(
        db, task.id, "恢复可用", data.restored_by,
        description=f"座凳恢复可用状态，备注：{data.restore_note or '无'}",
        from_status=old_status.value,
        to_status=InspectionTaskStatus.CLOSED.value,
        details={"restore_note": data.restore_note}
    )

    db.commit()
    db.refresh(task)
    return task


def get_inspection_task_detail(db: Session, task_id: int) -> Optional[InspectionTask]:
    task = db.query(InspectionTask).filter(InspectionTask.id == task_id).first()
    if task:
        _ = task.logs
    return task


def get_inspection_task_logs(db: Session, task_id: int) -> List[InspectionTaskLog]:
    return db.query(InspectionTaskLog).filter(
        InspectionTaskLog.task_id == task_id
    ).order_by(InspectionTaskLog.action_at.asc()).all()


def get_inspection_tasks_summary(db: Session) -> dict:
    summary = {
        "total_tasks": db.query(InspectionTask).count(),
        "pending_count": db.query(InspectionTask).filter(
            InspectionTask.task_status == InspectionTaskStatus.PENDING
        ).count(),
        "in_progress_count": db.query(InspectionTask).filter(
            InspectionTask.task_status == InspectionTaskStatus.IN_PROGRESS
        ).count(),
        "completed_normal_count": db.query(InspectionTask).filter(
            InspectionTask.task_status == InspectionTaskStatus.COMPLETED_NORMAL
        ).count(),
        "completed_abnormal_count": db.query(InspectionTask).filter(
            InspectionTask.task_status == InspectionTaskStatus.COMPLETED_ABNORMAL
        ).count(),
        "abnormal_detained_count": db.query(InspectionTask).filter(
            InspectionTask.task_status == InspectionTaskStatus.ABNORMAL_DETAINED
        ).count(),
        "abnormal_pending_review_count": db.query(InspectionTask).filter(
            InspectionTask.task_status == InspectionTaskStatus.ABNORMAL_PENDING_REVIEW
        ).count(),
        "closed_count": db.query(InspectionTask).filter(
            InspectionTask.task_status == InspectionTaskStatus.CLOSED
        ).count(),
    }
    return summary
