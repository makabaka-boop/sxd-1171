from datetime import datetime, timedelta
from typing import Optional, List
import json
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from models import (
    Stool, BorrowRecord, StoolStatus, LoadLevel,
    InspectionTask, InspectionTaskStatus, InspectionResult,
    InspectionTaskLog, Reservation, ReservationStatus, ReservationLog,
    ReservationExpireRule, ReservationExpireStatus
)
from schemas import (
    StoolCreate, StoolUpdate,
    IssueStoolRequest, ReturnStoolRequest,
    CleanStoolRequest, ReviewStoolRequest, DetainStoolRequest,
    GenerateInspectionTasksRequest, SubmitInspectionResultRequest,
    ReviewInspectionTaskRequest, RestoreInspectedStoolRequest,
    CreateReservationRequest, CancelReservationRequest,
    ConfirmReservationRequest, IssueByReservationRequest,
    ReservationExpireRuleCreate, ReservationExpireRuleUpdate
)
from exceptions import ValidationException, StatusConflictException, NotFoundException


def validate_stool_create(db: Session, data: StoolCreate):
    if not data.stool_number or not data.stool_number.strip():
        raise ValidationException("座凳编号不能为空", "stool_number")
    if not data.storage_area or not data.storage_area.strip():
        raise ValidationException("存放区域不能为空", "storage_area")
    if not data.responsible_person or not data.responsible_person.strip():
        raise ValidationException("责任人不能为空", "responsible_person")
    existing = db.query(Stool).filter(Stool.stool_number == data.stool_number.strip()).first()
    if existing:
        raise ValidationException(f"座凳编号已存在: {data.stool_number.strip()}", "stool_number")
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
    date_to: Optional[datetime] = None,
    reservation_expire_status: Optional[ReservationExpireStatus] = None,
    applicant: Optional[str] = None
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
    if applicant:
        query = query.join(Reservation, Stool.id == Reservation.stool_id).filter(
            Reservation.applicant.like(f"%{applicant}%"),
            Reservation.status.in_([ReservationStatus.PENDING, ReservationStatus.CONFIRMED])
        )

    items = query.order_by(Stool.updated_at.desc()).all()

    if reservation_expire_status:
        filtered_items = []
        for stool in items:
            enriched = get_stool_with_reservation_info(db, stool)
            if enriched.get("active_reservation_expire_status") == reservation_expire_status:
                filtered_items.append(stool)
        items = filtered_items

    total = len(items)
    paged_items = items[skip:skip + limit]

    return total, paged_items


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

        if current_status == StoolStatus.RESERVED:
            raise StatusConflictException(
                f"座凳正处于「{current_status.value}」状态，存在活跃预约，不可直接修改状态",
                current_status=current_status.value,
                expected_status="通过预约发放/取消流程更新状态"
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

        if new_status == StoolStatus.RESERVED:
            raise StatusConflictException(
                "不可直接将座凳标记为已预约状态，请通过预约流程创建预约",
                current_status=current_status.value,
                expected_status="使用 /api/reservations 接口创建预约"
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
    active_reservation = get_active_reservation_for_stool(db, stool_id)
    if active_reservation:
        raise StatusConflictException(
            f"座凳存在活跃预约（预约ID：{active_reservation.id}），无法删除",
            current_status=stool.status.value,
            expected_status="先取消或完成预约"
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

    if stool.status == StoolStatus.RESERVED:
        active_reservation = get_active_reservation_for_stool(db, data.stool_id)
        if active_reservation:
            raise StatusConflictException(
                f"座凳 {stool.stool_number} 已被预约（预约ID：{active_reservation.id}），请通过预约发放流程处理",
                current_status=stool.status.value,
                expected_status="使用 /api/reservations/issue 接口发放"
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

    active_reservation = get_active_reservation_for_stool(db, data.stool_id)
    if active_reservation:
        raise StatusConflictException(
            f"座凳 {stool.stool_number} 存在待处理的预约申请（预约ID：{active_reservation.id}），请优先处理预约或取消预约后再发放",
            current_status=stool.status.value,
            expected_status="先处理预约申请"
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
        next_reservation = get_active_reservation_for_stool(db, record.stool_id)
        if next_reservation and next_reservation.status == ReservationStatus.PENDING:
            stool.status = StoolStatus.RESERVED
            next_reservation.status = ReservationStatus.CONFIRMED
            next_reservation.confirmed_at = now
            next_reservation.confirmed_by = data.reviewed_by.strip()
            add_reservation_log(
                db, next_reservation.id, "自动递补确认", data.reviewed_by.strip(),
                description="座凳复核完成恢复可用，排队预约自动递补确认",
                from_status=ReservationStatus.PENDING.value,
                to_status=ReservationStatus.CONFIRMED.value,
                details={
                    "stool_number": stool.stool_number,
                    "review_record_id": record.id
                }
            )
        else:
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
        if r.source_reservation_id:
            enriched["source_reservation_id"] = r.source_reservation_id
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
            StoolStatus.RESTORED,
            StoolStatus.RESERVED
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

        confirmed_reservation = get_confirmed_reservation_for_stool(db, task.stool_id)
        if confirmed_reservation:
            stool.status = StoolStatus.RESERVED
        else:
            pending_reservation = get_pending_reservation_for_stool(db, task.stool_id)
            if pending_reservation:
                stool.status = StoolStatus.RESERVED
                pending_reservation.status = ReservationStatus.CONFIRMED
                pending_reservation.confirmed_at = now
                pending_reservation.confirmed_by = data.inspected_by.strip()
                add_reservation_log(
                    db, pending_reservation.id, "自动递补确认", data.inspected_by.strip(),
                    description="巡检结果正常，排队预约自动递补确认",
                    from_status=ReservationStatus.PENDING.value,
                    to_status=ReservationStatus.CONFIRMED.value,
                    details={
                        "stool_number": stool.stool_number,
                        "inspection_task_id": task.id
                    }
                )
            else:
                stool.status = StoolStatus.RESTORED

        add_inspection_log(
            db, task.id, "提交巡检结果", data.inspected_by,
            description="巡检结果正常" + ("，座凳维持已预约状态" if stool.status == StoolStatus.RESERVED else "，座凳恢复可用状态"),
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

        confirmed_reservation = get_confirmed_reservation_for_stool(db, task.stool_id)
        if confirmed_reservation:
            stool.status = StoolStatus.RESERVED
        else:
            pending_reservation = get_pending_reservation_for_stool(db, task.stool_id)
            if pending_reservation:
                stool.status = StoolStatus.RESERVED
                pending_reservation.status = ReservationStatus.CONFIRMED
                pending_reservation.confirmed_at = now
                pending_reservation.confirmed_by = data.reviewed_by.strip()
                add_reservation_log(
                    db, pending_reservation.id, "自动递补确认", data.reviewed_by.strip(),
                    description="巡检异常复核通过恢复可用，排队预约自动递补确认",
                    from_status=ReservationStatus.PENDING.value,
                    to_status=ReservationStatus.CONFIRMED.value,
                    details={
                        "stool_number": stool.stool_number,
                        "inspection_task_id": task.id
                    }
                )
            else:
                stool.status = StoolStatus.RESTORED

        add_inspection_log(
            db, task.id, "复核完成", data.reviewed_by,
            description="复核通过" + ("，座凳维持已预约状态" if stool.status == StoolStatus.RESERVED else "，座凳恢复可用"),
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

    next_reservation = get_active_reservation_for_stool(db, task.stool_id)
    if next_reservation and next_reservation.status == ReservationStatus.PENDING:
        stool.status = StoolStatus.RESERVED
        next_reservation.status = ReservationStatus.CONFIRMED
        next_reservation.confirmed_at = now
        next_reservation.confirmed_by = data.restored_by.strip()
        add_reservation_log(
            db, next_reservation.id, "自动递补确认", data.restored_by.strip(),
            description="座凳巡检异常留置解除恢复可用，排队预约自动递补确认",
            from_status=ReservationStatus.PENDING.value,
            to_status=ReservationStatus.CONFIRMED.value,
            details={
                "stool_number": stool.stool_number,
                "inspection_task_id": task.id
            }
        )
        add_inspection_log(
            db, task.id, "恢复可用", data.restored_by,
            description=f"座凳恢复可用，已有排队预约（ID：{next_reservation.id}）自动递补确认",
            from_status=old_status.value,
            to_status=InspectionTaskStatus.CLOSED.value,
            details={"restore_note": data.restore_note, "reservation_id": next_reservation.id}
        )
    else:
        stool.status = StoolStatus.RESTORED
        add_inspection_log(
            db, task.id, "恢复可用", data.restored_by,
            description=f"座凳恢复可用状态，备注：{data.restore_note or '无'}",
            from_status=old_status.value,
            to_status=InspectionTaskStatus.CLOSED.value,
            details={"restore_note": data.restore_note}
        )
    stool.updated_at = now

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


RESERVATION_EXPIRE_HOURS = 24
PRIORITY_WEIGHT_TIME = 1.0
PRIORITY_WEIGHT_LOAD = 0.5


def get_active_expire_rule(db: Session) -> ReservationExpireRule:
    rule = db.query(ReservationExpireRule).filter(
        ReservationExpireRule.is_active == 1
    ).order_by(ReservationExpireRule.id.desc()).first()
    if not rule:
        rule = ReservationExpireRule(
            rule_name="默认规则",
            is_active=1,
            pending_expire_hours=24,
            confirmed_expire_hours=12,
            expected_use_grace_hours=2,
            expire_soon_hours=2,
            auto_release=1,
            auto_release_to_pending=0,
            auto_confirm_next=1,
            created_by="system"
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
    return rule


def create_expire_rule(db: Session, data: ReservationExpireRuleCreate) -> ReservationExpireRule:
    existing = db.query(ReservationExpireRule).filter(
        ReservationExpireRule.rule_name == data.rule_name.strip()
    ).first()
    if existing:
        raise ValidationException(f"规则名称已存在: {data.rule_name.strip()}", "rule_name")

    if data.is_active == 1:
        db.query(ReservationExpireRule).filter(
            ReservationExpireRule.is_active == 1
        ).update({ReservationExpireRule.is_active: 0})
        db.flush()

    rule = ReservationExpireRule(
        rule_name=data.rule_name.strip(),
        is_active=data.is_active,
        pending_expire_hours=data.pending_expire_hours,
        confirmed_expire_hours=data.confirmed_expire_hours,
        expected_use_grace_hours=data.expected_use_grace_hours,
        expire_soon_hours=data.expire_soon_hours,
        auto_release=data.auto_release,
        auto_release_to_pending=data.auto_release_to_pending,
        auto_confirm_next=data.auto_confirm_next,
        created_by=data.created_by.strip()
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def update_expire_rule(db: Session, rule_id: int, data: ReservationExpireRuleUpdate) -> ReservationExpireRule:
    rule = db.query(ReservationExpireRule).filter(ReservationExpireRule.id == rule_id).first()
    if not rule:
        raise NotFoundException("到期规则", str(rule_id))

    update_data = data.model_dump(exclude_unset=True)

    if "rule_name" in update_data and update_data["rule_name"]:
        existing = db.query(ReservationExpireRule).filter(
            ReservationExpireRule.rule_name == update_data["rule_name"].strip(),
            ReservationExpireRule.id != rule_id
        ).first()
        if existing:
            raise ValidationException(f"规则名称已存在: {update_data['rule_name'].strip()}", "rule_name")
        update_data["rule_name"] = update_data["rule_name"].strip()

    if "is_active" in update_data and update_data["is_active"] == 1:
        db.query(ReservationExpireRule).filter(
            ReservationExpireRule.is_active == 1,
            ReservationExpireRule.id != rule_id
        ).update({ReservationExpireRule.is_active: 0})
        db.flush()

    for key, value in update_data.items():
        if value is not None:
            setattr(rule, key, value)

    rule.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(rule)
    return rule


def list_expire_rules(db: Session) -> List[ReservationExpireRule]:
    return db.query(ReservationExpireRule).order_by(ReservationExpireRule.updated_at.desc()).all()


def get_expire_rule(db: Session, rule_id: int) -> Optional[ReservationExpireRule]:
    return db.query(ReservationExpireRule).filter(ReservationExpireRule.id == rule_id).first()


def delete_expire_rule(db: Session, rule_id: int):
    rule = get_expire_rule(db, rule_id)
    if not rule:
        raise NotFoundException("到期规则", str(rule_id))
    if rule.is_active == 1:
        raise StatusConflictException(
            "不能删除当前启用的规则，请先启用其他规则",
            current_status="已启用",
            expected_status="已停用"
        )
    db.delete(rule)
    db.commit()


def get_reservation_effective_expire_time(
    db: Session,
    reservation: Reservation
) -> tuple[Optional[datetime], Optional[str]]:
    now = datetime.utcnow()
    rule = get_active_expire_rule(db)
    candidates: List[tuple[datetime, str]] = []

    if reservation.status == ReservationStatus.CONFIRMED:
        if reservation.expected_use_time:
            grace_deadline = reservation.expected_use_time + timedelta(hours=rule.expected_use_grace_hours)
            candidates.append((grace_deadline, f"已超过预期使用时间 {rule.expected_use_grace_hours} 小时宽限期"))

        if reservation.confirmed_at:
            confirmed_deadline = reservation.confirmed_at + timedelta(hours=rule.confirmed_expire_hours)
            candidates.append((confirmed_deadline, f"已确认后超过 {rule.confirmed_expire_hours} 小时未发放"))

    elif reservation.status == ReservationStatus.PENDING:
        if reservation.expected_use_time:
            grace_deadline = reservation.expected_use_time + timedelta(hours=rule.expected_use_grace_hours)
            candidates.append((grace_deadline, f"已超过预期使用时间 {rule.expected_use_grace_hours} 小时宽限期"))

        if reservation.reserved_at:
            pending_deadline = reservation.reserved_at + timedelta(hours=rule.pending_expire_hours)
            candidates.append((pending_deadline, f"待确认超过 {rule.pending_expire_hours} 小时未处理"))

    if reservation.expired_at and reservation.status in [ReservationStatus.PENDING, ReservationStatus.CONFIRMED, ReservationStatus.EXPIRED]:
        candidates.append((reservation.expired_at, "预约已超过设定过期时间"))

    if not candidates:
        return None, None

    effective_expire_time, effective_reason = min(candidates, key=lambda x: x[0])
    return effective_expire_time, effective_reason


def calculate_reservation_expire_status(
    db: Session,
    reservation: Reservation
) -> tuple[ReservationExpireStatus, bool, Optional[str]]:
    now = datetime.utcnow()
    rule = get_active_expire_rule(db)

    if reservation.status in [ReservationStatus.CANCELLED, ReservationStatus.COMPLETED, ReservationStatus.RELEASED]:
        if reservation.status == ReservationStatus.RELEASED:
            return ReservationExpireStatus.RELEASED, False, None
        return ReservationExpireStatus.NORMAL, False, None

    if reservation.status == ReservationStatus.EXPIRED:
        if reservation.released_at:
            return ReservationExpireStatus.RELEASED, False, None
        if reservation.expired_processed_at:
            return ReservationExpireStatus.EXPIRED_HANDLED, False, "已过期且已处理"
        return ReservationExpireStatus.EXPIRED_UNHANDLED, False, "已过期未处理"

    effective_expire_time, effective_reason = get_reservation_effective_expire_time(db, reservation)

    if effective_expire_time and now >= effective_expire_time:
        return ReservationExpireStatus.EXPIRED_UNHANDLED, False, effective_reason

    is_expire_soon = False
    if effective_expire_time:
        soon_threshold = now + timedelta(hours=rule.expire_soon_hours)
        if effective_expire_time <= soon_threshold and effective_expire_time > now:
            is_expire_soon = True

    return ReservationExpireStatus.NORMAL, is_expire_soon, None


def get_reservation_with_expire_info(db: Session, reservation: Reservation) -> dict:
    expire_status, is_expire_soon, _ = calculate_reservation_expire_status(db, reservation)
    data = reservation.__dict__.copy()
    if "_sa_instance_state" in data:
        del data["_sa_instance_state"]
    data["expire_status"] = expire_status
    data["is_expire_soon"] = is_expire_soon
    return data


def get_stool_with_reservation_info(db: Session, stool: Stool) -> dict:
    data = stool.__dict__.copy()
    if "_sa_instance_state" in data:
        del data["_sa_instance_state"]

    active_reservation = get_active_reservation_for_stool(db, stool.id)
    if active_reservation:
        expire_status, is_expire_soon, _ = calculate_reservation_expire_status(db, active_reservation)
        data["active_reservation_id"] = active_reservation.id
        data["active_reservation_status"] = active_reservation.status
        data["active_reservation_expire_status"] = expire_status
        data["active_reservation_applicant"] = active_reservation.applicant
        data["active_reservation_expired_at"] = active_reservation.expired_at
    else:
        data["active_reservation_id"] = None
        data["active_reservation_status"] = None
        data["active_reservation_expire_status"] = None
        data["active_reservation_applicant"] = None
        data["active_reservation_expired_at"] = None

    return data


def add_reservation_log(
    db: Session,
    reservation_id: int,
    action_type: str,
    action_by: str,
    description: str = None,
    from_status: str = None,
    to_status: str = None,
    details: dict = None
) -> ReservationLog:
    log = ReservationLog(
        reservation_id=reservation_id,
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


def calculate_priority_score(
    db: Session,
    stool: Stool,
    expected_use_time: Optional[datetime] = None
) -> float:
    score = 0.0

    now = datetime.utcnow()
    if expected_use_time:
        time_diff = abs((expected_use_time - now).total_seconds() / 3600)
        score += PRIORITY_WEIGHT_TIME * max(0, 72 - time_diff) / 72
    else:
        score += PRIORITY_WEIGHT_TIME * 0.5

    load_priority = {
        LoadLevel.LIGHT: 0.2,
        LoadLevel.MEDIUM: 0.5,
        LoadLevel.HEAVY: 0.8,
        LoadLevel.EXTRA_HEAVY: 1.0
    }
    score += PRIORITY_WEIGHT_LOAD * load_priority.get(stool.load_level, 0.5)

    return round(score, 4)


def update_queue_positions(
    db: Session,
    storage_area: str = None,
    load_level: LoadLevel = None
):
    query = db.query(Reservation).filter(
        Reservation.status.in_([
            ReservationStatus.PENDING,
            ReservationStatus.CONFIRMED
        ])
    )

    if storage_area:
        query = query.filter(Reservation.storage_area == storage_area)
    if load_level:
        query = query.filter(Reservation.load_level == load_level)

    reservations = query.order_by(
        Reservation.priority_score.desc(),
        Reservation.reserved_at.asc()
    ).all()

    for idx, res in enumerate(reservations, start=1):
        res.queue_position = idx

    db.flush()


def get_active_reservation_for_stool(
    db: Session,
    stool_id: int
) -> Optional[Reservation]:
    return db.query(Reservation).filter(
        Reservation.stool_id == stool_id,
        Reservation.status.in_([
            ReservationStatus.PENDING,
            ReservationStatus.CONFIRMED
        ])
    ).order_by(
        Reservation.priority_score.desc(),
        Reservation.reserved_at.asc()
    ).first()


def has_active_reservation(db: Session, stool_id: int) -> bool:
    return get_active_reservation_for_stool(db, stool_id) is not None


def get_confirmed_reservation_for_stool(
    db: Session,
    stool_id: int
) -> Optional[Reservation]:
    return db.query(Reservation).filter(
        Reservation.stool_id == stool_id,
        Reservation.status == ReservationStatus.CONFIRMED
    ).first()


def get_pending_reservation_for_stool(
    db: Session,
    stool_id: int
) -> Optional[Reservation]:
    return db.query(Reservation).filter(
        Reservation.stool_id == stool_id,
        Reservation.status == ReservationStatus.PENDING
    ).order_by(
        Reservation.priority_score.desc(),
        Reservation.reserved_at.asc()
    ).first()


def create_reservation(
    db: Session,
    data: CreateReservationRequest
) -> Reservation:
    stool = get_stool(db, data.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(data.stool_id))

    if stool.status == StoolStatus.DETAINED:
        raise StatusConflictException(
            "座凳已被异常留置，不可预约",
            current_status=stool.status.value,
            expected_status="非留置状态"
        )

    if stool.status in [StoolStatus.PENDING_ISSUE, StoolStatus.RESTORED]:
        raise StatusConflictException(
            "座凳当前可直接借用，无需预约",
            current_status=stool.status.value,
            expected_status="已借出/清洁/复核/巡检中状态"
        )

    existing_pending = db.query(Reservation).filter(
        Reservation.stool_id == data.stool_id,
        Reservation.applicant == data.applicant.strip(),
        Reservation.status.in_([
            ReservationStatus.PENDING,
            ReservationStatus.CONFIRMED
        ])
    ).first()
    if existing_pending:
        raise ValidationException(
            f"您已针对该座凳提交过预约申请（预约ID：{existing_pending.id}），不可重复预约",
            "applicant"
        )

    priority_score = calculate_priority_score(
        db, stool, data.expected_use_time
    )

    now = datetime.utcnow()
    rule = get_active_expire_rule(db)

    expired_at_candidates = []
    if data.expected_use_time:
        expired_at_candidates.append(data.expected_use_time + timedelta(hours=rule.expected_use_grace_hours))
    expired_at_candidates.append(now + timedelta(hours=rule.pending_expire_hours))
    expired_at = min(expired_at_candidates)

    reservation = Reservation(
        stool_id=stool.id,
        stool_number=stool.stool_number,
        storage_area=stool.storage_area,
        load_level=stool.load_level,
        applicant=data.applicant.strip(),
        applicant_contact=data.applicant_contact.strip() if data.applicant_contact else None,
        purpose=data.purpose.strip() if data.purpose else None,
        status=ReservationStatus.PENDING,
        priority_score=priority_score,
        reserved_at=now,
        expected_use_time=data.expected_use_time,
        expired_at=expired_at
    )
    db.add(reservation)
    db.flush()

    add_reservation_log(
        db, reservation.id, "提交预约", data.applicant.strip(),
        description=f"预约座凳 {stool.stool_number}",
        from_status=None,
        to_status=ReservationStatus.PENDING.value,
        details={
            "stool_number": stool.stool_number,
            "storage_area": stool.storage_area,
            "load_level": stool.load_level.value,
            "purpose": data.purpose,
            "expected_use_time": data.expected_use_time.isoformat() if data.expected_use_time else None,
            "priority_score": priority_score
        }
    )

    update_queue_positions(db, stool.storage_area, stool.load_level)

    db.commit()
    db.refresh(reservation)
    return reservation


def get_reservation(
    db: Session,
    reservation_id: int
) -> Optional[Reservation]:
    return db.query(Reservation).filter(Reservation.id == reservation_id).first()


def get_reservation_detail(
    db: Session,
    reservation_id: int
) -> Optional[Reservation]:
    reservation = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if reservation:
        _ = reservation.logs
    return reservation


def list_reservations(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    stool_id: Optional[int] = None,
    storage_area: Optional[str] = None,
    load_level: Optional[LoadLevel] = None,
    applicant: Optional[str] = None,
    status: Optional[ReservationStatus] = None,
    expire_status: Optional[ReservationExpireStatus] = None,
    is_expire_soon: Optional[bool] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    expected_use_from: Optional[datetime] = None,
    expected_use_to: Optional[datetime] = None
):
    query = db.query(Reservation)

    if stool_id:
        query = query.filter(Reservation.stool_id == stool_id)
    if storage_area:
        query = query.filter(Reservation.storage_area.like(f"%{storage_area}%"))
    if load_level:
        query = query.filter(Reservation.load_level == load_level)
    if applicant:
        query = query.filter(Reservation.applicant.like(f"%{applicant}%"))
    if status:
        query = query.filter(Reservation.status == status)
    if date_from:
        query = query.filter(Reservation.created_at >= date_from)
    if date_to:
        query = query.filter(Reservation.created_at <= date_to)
    if expected_use_from:
        query = query.filter(Reservation.expected_use_time >= expected_use_from)
    if expected_use_to:
        query = query.filter(Reservation.expected_use_time <= expected_use_to)

    items = query.order_by(
        Reservation.priority_score.desc(),
        Reservation.reserved_at.asc()
    ).all()

    if expire_status or is_expire_soon is not None:
        filtered_items = []
        for res in items:
            es, ies, _ = calculate_reservation_expire_status(db, res)
            if expire_status and es != expire_status:
                continue
            if is_expire_soon is not None and ies != is_expire_soon:
                continue
            filtered_items.append(res)
        items = filtered_items

    total = len(items)
    paged_items = items[skip:skip + limit]

    return total, paged_items


def cancel_reservation(
    db: Session,
    data: CancelReservationRequest
) -> Reservation:
    reservation = get_reservation(db, data.reservation_id)
    if not reservation:
        raise NotFoundException("预约记录", str(data.reservation_id))

    if reservation.status not in [
        ReservationStatus.PENDING,
        ReservationStatus.CONFIRMED
    ]:
        raise StatusConflictException(
            f"预约当前状态为「{reservation.status.value}」，不可取消",
            current_status=reservation.status.value,
            expected_status=f"{ReservationStatus.PENDING.value} 或 {ReservationStatus.CONFIRMED.value}"
        )

    old_status = reservation.status
    now = datetime.utcnow()

    reservation.status = ReservationStatus.CANCELLED
    reservation.cancelled_at = now
    reservation.cancelled_by = data.cancelled_by.strip()
    reservation.cancel_reason = data.cancel_reason.strip() if data.cancel_reason else None
    reservation.queue_position = None

    add_reservation_log(
        db, reservation.id, "取消预约", data.cancelled_by.strip(),
        description=data.cancel_reason or "用户主动取消预约",
        from_status=old_status.value,
        to_status=ReservationStatus.CANCELLED.value,
        details={
            "cancel_reason": data.cancel_reason,
            "stool_number": reservation.stool_number
        }
    )

    update_queue_positions(db, reservation.storage_area, reservation.load_level)

    stool = get_stool(db, reservation.stool_id)
    if stool and stool.status == StoolStatus.RESERVED:
        next_reservation = get_active_reservation_for_stool(db, reservation.stool_id)
        if next_reservation:
            next_reservation.status = ReservationStatus.CONFIRMED
            next_reservation.confirmed_at = now
            next_reservation.confirmed_by = data.cancelled_by.strip()
            add_reservation_log(
                db, next_reservation.id, "自动递补确认", data.cancelled_by.strip(),
                description="上一预约取消，排队预约自动递补确认",
                from_status=ReservationStatus.PENDING.value,
                to_status=ReservationStatus.CONFIRMED.value,
                details={
                    "previous_reservation_id": reservation.id,
                    "stool_number": reservation.stool_number
                }
            )
        else:
            if has_pending_cleaning_or_review(db, reservation.stool_id):
                pass
            else:
                stool.status = StoolStatus.RESTORED
                stool.updated_at = now

    db.commit()
    db.refresh(reservation)
    return reservation


def has_pending_cleaning_or_review(db: Session, stool_id: int) -> bool:
    latest_record = db.query(BorrowRecord).filter(
        BorrowRecord.stool_id == stool_id
    ).order_by(BorrowRecord.created_at.desc()).first()

    if not latest_record:
        return False

    if latest_record.returned_at and not latest_record.cleaned_at:
        return True
    if latest_record.cleaned_at and not latest_record.reviewed_at and latest_record.is_detained == 0:
        return True

    return False


def confirm_reservation(
    db: Session,
    data: ConfirmReservationRequest
) -> Reservation:
    reservation = get_reservation(db, data.reservation_id)
    if not reservation:
        raise NotFoundException("预约记录", str(data.reservation_id))

    if reservation.status != ReservationStatus.PENDING:
        raise StatusConflictException(
            f"预约当前状态为「{reservation.status.value}」，不可确认",
            current_status=reservation.status.value,
            expected_status=ReservationStatus.PENDING.value
        )

    stool = get_stool(db, reservation.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(reservation.stool_id))

    existing_confirmed = db.query(Reservation).filter(
        Reservation.stool_id == reservation.stool_id,
        Reservation.status == ReservationStatus.CONFIRMED,
        Reservation.id != reservation.id
    ).first()
    if existing_confirmed:
        raise StatusConflictException(
            f"座凳已存在已确认的预约（预约ID：{existing_confirmed.id}），不可重复确认",
            current_status=stool.status.value,
            expected_status="无其他已确认预约"
        )

    if stool.status not in [StoolStatus.PENDING_ISSUE, StoolStatus.RESTORED, StoolStatus.RESERVED]:
        raise StatusConflictException(
            f"座凳当前状态为「{stool.status.value}」，不可确认预约",
            current_status=stool.status.value,
            expected_status=f"{StoolStatus.PENDING_ISSUE.value} 或 {StoolStatus.RESTORED.value}"
        )

    old_status = reservation.status
    now = datetime.utcnow()
    rule = get_active_expire_rule(db)

    reservation.status = ReservationStatus.CONFIRMED
    reservation.confirmed_at = now
    reservation.confirmed_by = data.confirmed_by.strip()

    confirmed_expire_candidates = []
    if reservation.expected_use_time:
        confirmed_expire_candidates.append(reservation.expected_use_time + timedelta(hours=rule.expected_use_grace_hours))
    confirmed_expire_candidates.append(now + timedelta(hours=rule.confirmed_expire_hours))
    reservation.expired_at = min(confirmed_expire_candidates)

    stool.status = StoolStatus.RESERVED
    stool.updated_at = now

    add_reservation_log(
        db, reservation.id, "确认预约", data.confirmed_by.strip(),
        description=f"确认预约人 {reservation.applicant} 已到场，座凳标记为已预约",
        from_status=old_status.value,
        to_status=ReservationStatus.CONFIRMED.value,
        details={
            "stool_number": reservation.stool_number,
            "applicant": reservation.applicant,
            "borrower": data.borrower,
            "borrower_contact": data.borrower_contact
        }
    )

    db.commit()
    db.refresh(reservation)
    return reservation


def issue_by_reservation(
    db: Session,
    data: IssueByReservationRequest
) -> BorrowRecord:
    reservation = get_reservation(db, data.reservation_id)
    if not reservation:
        raise NotFoundException("预约记录", str(data.reservation_id))

    if reservation.status != ReservationStatus.CONFIRMED:
        raise StatusConflictException(
            f"预约当前状态为「{reservation.status.value}」，不可发放",
            current_status=reservation.status.value,
            expected_status=ReservationStatus.CONFIRMED.value
        )

    stool = get_stool(db, reservation.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(reservation.stool_id))

    if stool.status != StoolStatus.RESERVED:
        raise StatusConflictException(
            f"座凳当前状态为「{stool.status.value}」，不可发放",
            current_status=stool.status.value,
            expected_status=StoolStatus.RESERVED.value
        )

    borrower = data.borrower.strip() if data.borrower else reservation.applicant
    borrower_contact = data.borrower_contact.strip() if data.borrower_contact else reservation.applicant_contact

    now = datetime.utcnow()
    record = BorrowRecord(
        stool_id=stool.id,
        borrower=borrower,
        borrower_contact=borrower_contact,
        issued_at=now,
        issued_by=data.issued_by.strip(),
        source_type="预约发放",
        source_reservation_id=reservation.id
    )
    db.add(record)
    db.flush()

    reservation.status = ReservationStatus.COMPLETED
    reservation.completed_at = now
    reservation.borrow_record_id = record.id
    reservation.queue_position = None

    stool.status = StoolStatus.BORROWED
    stool.updated_at = now

    add_reservation_log(
        db, reservation.id, "发放座凳", data.issued_by.strip(),
        description=f"预约完成，座凳已发放给 {borrower}",
        from_status=ReservationStatus.CONFIRMED.value,
        to_status=ReservationStatus.COMPLETED.value,
        details={
            "stool_number": stool.stool_number,
            "borrower": borrower,
            "borrow_record_id": record.id
        }
    )

    update_queue_positions(db, reservation.storage_area, reservation.load_level)

    db.commit()
    db.refresh(record)
    db.refresh(reservation)
    return record


def process_expired_reservations(db: Session) -> dict:
    now = datetime.utcnow()
    rule = get_active_expire_rule(db)
    expired_count = 0
    released_count = 0
    auto_confirm_count = 0

    candidates = db.query(Reservation).filter(
        Reservation.status.in_([
            ReservationStatus.PENDING,
            ReservationStatus.CONFIRMED
        ])
    ).all()

    to_expire = []
    for res in candidates:
        expire_status, _, expire_reason = calculate_reservation_expire_status(db, res)
        if expire_status == ReservationExpireStatus.EXPIRED_UNHANDLED and expire_reason:
            to_expire.append((res, expire_reason))

    for reservation, expire_reason in to_expire:
        old_status = reservation.status
        reservation.status = ReservationStatus.EXPIRED
        reservation.queue_position = None
        reservation.expired_processed_at = now
        reservation.expire_reason = expire_reason

        add_reservation_log(
            db, reservation.id, "自动过期", "system",
            description=expire_reason,
            from_status=old_status.value,
            to_status=ReservationStatus.EXPIRED.value,
            details={
                "stool_number": reservation.stool_number,
                "expire_reason": expire_reason,
                "expected_use_time": reservation.expected_use_time.isoformat() if reservation.expected_use_time else None,
                "confirmed_at": reservation.confirmed_at.isoformat() if reservation.confirmed_at else None,
                "reserved_at": reservation.reserved_at.isoformat() if reservation.reserved_at else None
            }
        )

        expired_count += 1

        stool = get_stool(db, reservation.stool_id)
        if stool and stool.status == StoolStatus.RESERVED:
            if rule.auto_confirm_next:
                next_reservation = get_active_reservation_for_stool(db, reservation.stool_id)
                if next_reservation and next_reservation.id != reservation.id:
                    next_reservation.status = ReservationStatus.CONFIRMED
                    next_reservation.confirmed_at = now
                    next_reservation.confirmed_by = "system"
                    add_reservation_log(
                        db, next_reservation.id, "自动递补确认", "system",
                        description=f"上一预约过期，排队自动递补",
                        from_status=ReservationStatus.PENDING.value,
                        to_status=ReservationStatus.CONFIRMED.value,
                        details={
                            "previous_reservation_id": reservation.id,
                            "stool_number": reservation.stool_number
                        }
                    )
                    auto_confirm_count += 1
                    continue

            if rule.auto_release:
                if has_pending_cleaning_or_review(db, reservation.stool_id):
                    add_reservation_log(
                        db, reservation.id, "资源保留", "system",
                        description="座凳存在待清洁/复核流程，暂不释放资源",
                        from_status=old_status.value,
                        to_status=ReservationStatus.EXPIRED.value,
                        details={"stool_number": reservation.stool_number}
                    )
                else:
                    if rule.auto_release_to_pending:
                        stool.status = StoolStatus.PENDING_ISSUE
                    else:
                        stool.status = StoolStatus.RESTORED
                    stool.updated_at = now
                    reservation.released_at = now

                    add_reservation_log(
                        db, reservation.id, "资源释放", "system",
                        description=f"座凳资源已释放，状态更新为「{stool.status.value}」",
                        from_status=StoolStatus.RESERVED.value,
                        to_status=stool.status.value,
                        details={
                            "stool_number": stool.stool_number,
                            "new_stool_status": stool.status.value,
                            "auto_release_to_pending": rule.auto_release_to_pending
                        }
                    )
                    released_count += 1

    if expired_count > 0:
        update_queue_positions(db)
        db.commit()

    return {
        "expired_count": expired_count,
        "released_count": released_count,
        "auto_confirm_count": auto_confirm_count
    }


def expire_reservations(db: Session) -> int:
    result = process_expired_reservations(db)
    return result["expired_count"]


def release_expired_reservation_stool(
    db: Session,
    reservation_id: int,
    released_by: str,
    to_pending: bool = False
) -> Reservation:
    reservation = get_reservation(db, reservation_id)
    if not reservation:
        raise NotFoundException("预约记录", str(reservation_id))

    if reservation.status != ReservationStatus.EXPIRED:
        raise StatusConflictException(
            f"预约当前状态为「{reservation.status.value}」，不可执行释放操作",
            current_status=reservation.status.value,
            expected_status=ReservationStatus.EXPIRED.value
        )

    if reservation.released_at:
        raise StatusConflictException(
            "该预约的座凳资源已释放，不可重复操作",
            current_status="已释放",
            expected_status="未释放"
        )

    stool = get_stool(db, reservation.stool_id)
    if not stool:
        raise NotFoundException("座凳", str(reservation.stool_id))

    if has_pending_cleaning_or_review(db, reservation.stool_id):
        raise StatusConflictException(
            "座凳存在待清洁/复核流程，暂不可释放",
            current_status="待清洁/复核中",
            expected_status="无待处理流程"
        )

    now = datetime.utcnow()

    if stool.status == StoolStatus.RESERVED:
        if to_pending:
            stool.status = StoolStatus.PENDING_ISSUE
        else:
            stool.status = StoolStatus.RESTORED
        stool.updated_at = now

        add_reservation_log(
            db, reservation.id, "手动释放资源", released_by.strip(),
            description=f"手动释放座凳资源，状态更新为「{stool.status.value}」",
            from_status=StoolStatus.RESERVED.value,
            to_status=stool.status.value,
            details={
                "stool_number": stool.stool_number,
                "new_stool_status": stool.status.value
            }
        )
    else:
        add_reservation_log(
            db, reservation.id, "确认已释放", released_by.strip(),
            description=f"确认座凳当前状态为「{stool.status.value}」，无需重复释放",
            from_status=None,
            to_status=None,
            details={
                "stool_number": stool.stool_number,
                "current_stool_status": stool.status.value
            }
        )

    reservation.status = ReservationStatus.RELEASED
    reservation.released_at = now
    reservation.expired_processed_at = now

    db.commit()
    db.refresh(reservation)
    return reservation


def get_reservation_logs(
    db: Session,
    reservation_id: int
) -> List[ReservationLog]:
    return db.query(ReservationLog).filter(
        ReservationLog.reservation_id == reservation_id
    ).order_by(ReservationLog.action_at.asc()).all()


def get_reservations_summary(db: Session) -> dict:
    total_queue = db.query(Reservation).filter(
        Reservation.status.in_([
            ReservationStatus.PENDING,
            ReservationStatus.CONFIRMED
        ])
    ).count()

    all_reservations = db.query(Reservation).all()

    expire_soon_count = 0
    expired_unhandled_count = 0
    expired_handled_count = 0

    for res in all_reservations:
        expire_status, is_expire_soon, _ = calculate_reservation_expire_status(db, res)
        if is_expire_soon:
            expire_soon_count += 1
        if expire_status == ReservationExpireStatus.EXPIRED_UNHANDLED:
            expired_unhandled_count += 1
        elif expire_status == ReservationExpireStatus.EXPIRED_HANDLED:
            expired_handled_count += 1

    summary = {
        "total_reservations": db.query(Reservation).count(),
        "pending_count": db.query(Reservation).filter(
            Reservation.status == ReservationStatus.PENDING
        ).count(),
        "confirmed_count": db.query(Reservation).filter(
            Reservation.status == ReservationStatus.CONFIRMED
        ).count(),
        "cancelled_count": db.query(Reservation).filter(
            Reservation.status == ReservationStatus.CANCELLED
        ).count(),
        "expired_count": db.query(Reservation).filter(
            Reservation.status == ReservationStatus.EXPIRED
        ).count(),
        "completed_count": db.query(Reservation).filter(
            Reservation.status == ReservationStatus.COMPLETED
        ).count(),
        "released_count": db.query(Reservation).filter(
            Reservation.status == ReservationStatus.RELEASED
        ).count(),
        "total_queue_count": total_queue,
        "expire_soon_count": expire_soon_count,
        "expired_unhandled_count": expired_unhandled_count,
        "expired_handled_count": expired_handled_count
    }
    return summary


def enrich_reservation_records(
    records: List[Reservation],
    db: Session
) -> List[dict]:
    result = []
    for r in records:
        enriched = r.__dict__.copy()
        if "_sa_instance_state" in enriched:
            del enriched["_sa_instance_state"]
        result.append(enriched)
    return result
