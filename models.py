import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey, Text, Float
from sqlalchemy.orm import relationship

from database import Base


class StoolStatus(str, enum.Enum):
    PENDING_ISSUE = "待发放"
    BORROWED = "已借出"
    PENDING_CLEANING = "待清洁"
    PENDING_REVIEW = "待复核"
    RESTORED = "恢复可用"
    DETAINED = "异常留置"
    PENDING_INSPECTION = "待巡检"
    PENDING_INSPECTION_REVIEW = "待巡检复核"
    RESERVED = "已预约"


class ReservationStatus(str, enum.Enum):
    PENDING = "待确认"
    CONFIRMED = "已确认"
    CANCELLED = "已取消"
    EXPIRED = "已过期"
    COMPLETED = "已完成"
    RELEASED = "已释放"


class ReservationExpireStatus(str, enum.Enum):
    NORMAL = "正常"
    EXPIRE_SOON = "即将过期"
    EXPIRED_UNHANDLED = "已过期未处理"
    EXPIRED_HANDLED = "已过期已处理"
    RELEASED = "已释放"


class UserRole(str, enum.Enum):
    MANAGER = "管理者"
    ATTENDANT = "值守人员"
    REVIEWER = "复核人员"


class InspectionTaskStatus(str, enum.Enum):
    PENDING = "待处理"
    IN_PROGRESS = "处理中"
    COMPLETED_NORMAL = "正常完成"
    COMPLETED_ABNORMAL = "异常完成"
    ABNORMAL_DETAINED = "异常留置"
    ABNORMAL_PENDING_REVIEW = "待复核"
    CLOSED = "已关闭"


class InspectionResult(str, enum.Enum):
    NORMAL = "正常"
    ABNORMAL = "异常"


class LoadLevel(str, enum.Enum):
    LIGHT = "轻型"
    MEDIUM = "中型"
    HEAVY = "重型"
    EXTRA_HEAVY = "超重型"


class Stool(Base):
    __tablename__ = "stools"

    id = Column(Integer, primary_key=True, index=True)
    stool_number = Column(String(50), unique=True, index=True, nullable=False)
    storage_area = Column(String(100), nullable=False, index=True)
    load_level = Column(Enum(LoadLevel), nullable=False, index=True)
    inspection_cycle_days = Column(Integer, nullable=False)
    responsible_person = Column(String(100), nullable=False, index=True)
    status = Column(Enum(StoolStatus), default=StoolStatus.PENDING_ISSUE, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    borrow_records = relationship("BorrowRecord", back_populates="stool", cascade="all, delete-orphan")
    reservations = relationship("Reservation", back_populates="stool", cascade="all, delete-orphan")


class BorrowRecord(Base):
    __tablename__ = "borrow_records"

    id = Column(Integer, primary_key=True, index=True)
    stool_id = Column(Integer, ForeignKey("stools.id"), nullable=False, index=True)
    borrower = Column(String(100), nullable=True)
    borrower_contact = Column(String(100), nullable=True)

    issued_at = Column(DateTime, nullable=True)
    issued_by = Column(String(100), nullable=True)

    returned_at = Column(DateTime, nullable=True)
    returned_by = Column(String(100), nullable=True)
    appearance_issue = Column(Text, nullable=True)
    appearance_issue_level = Column(String(20), nullable=True)

    cleaned_at = Column(DateTime, nullable=True)
    cleaned_by = Column(String(100), nullable=True)
    cleaning_result = Column(String(20), nullable=True)
    cleaning_note = Column(Text, nullable=True)

    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    review_result = Column(String(20), nullable=True)
    review_note = Column(Text, nullable=True)

    is_detained = Column(Integer, default=0, nullable=False)
    detained_reason = Column(Text, nullable=True)
    detained_at = Column(DateTime, nullable=True)
    detained_by = Column(String(100), nullable=True)

    turnover_hours = Column(Float, nullable=True)
    source_type = Column(String(50), default="借还流程", nullable=False)
    source_task_id = Column(Integer, nullable=True)
    source_reservation_id = Column(Integer, nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    stool = relationship("Stool", back_populates="borrow_records")
    reservation = relationship("Reservation", back_populates="borrow_record")


class ReservationExpireRule(Base):
    __tablename__ = "reservation_expire_rules"

    id = Column(Integer, primary_key=True, index=True)
    rule_name = Column(String(100), nullable=False, unique=True)
    is_active = Column(Integer, default=1, nullable=False, index=True)

    pending_expire_hours = Column(Integer, default=24, nullable=False, comment="待确认预约过期时间(小时)")
    confirmed_expire_hours = Column(Integer, default=12, nullable=False, comment="已确认预约过期时间(小时)")
    expected_use_grace_hours = Column(Integer, default=2, nullable=False, comment="超过预期使用时间的宽限期(小时)")
    expire_soon_hours = Column(Integer, default=2, nullable=False, comment="即将过期预警阈值(小时)")

    auto_release = Column(Integer, default=1, nullable=False, comment="是否自动释放座凳资源")
    auto_release_to_pending = Column(Integer, default=0, nullable=False, comment="释放后是否置为待发放(否则为恢复可用)")
    auto_confirm_next = Column(Integer, default=1, nullable=False, comment="是否自动确认下一个排队预约")

    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    stool_id = Column(Integer, ForeignKey("stools.id"), nullable=False, index=True)
    stool_number = Column(String(50), index=True, nullable=False)
    storage_area = Column(String(100), nullable=False, index=True)
    load_level = Column(Enum(LoadLevel), nullable=False, index=True)

    applicant = Column(String(100), nullable=False, index=True)
    applicant_contact = Column(String(100), nullable=True)
    purpose = Column(Text, nullable=True)

    status = Column(Enum(ReservationStatus), default=ReservationStatus.PENDING, nullable=False, index=True)
    queue_position = Column(Integer, nullable=True, index=True)
    priority_score = Column(Float, default=0.0, nullable=False, index=True)

    reserved_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expected_use_time = Column(DateTime, nullable=True)
    expired_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String(100), nullable=True)

    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(String(100), nullable=True)
    cancel_reason = Column(Text, nullable=True)

    completed_at = Column(DateTime, nullable=True)
    borrow_record_id = Column(Integer, ForeignKey("borrow_records.id"), nullable=True, index=True)

    expired_processed_at = Column(DateTime, nullable=True, comment="过期处理时间")
    released_at = Column(DateTime, nullable=True, comment="资源释放时间")
    expire_reason = Column(String(200), nullable=True, comment="过期原因")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    stool = relationship("Stool")
    borrow_record = relationship("BorrowRecord", back_populates="reservation")
    logs = relationship("ReservationLog", back_populates="reservation", cascade="all, delete-orphan")


class ReservationLog(Base):
    __tablename__ = "reservation_logs"

    id = Column(Integer, primary_key=True, index=True)
    reservation_id = Column(Integer, ForeignKey("reservations.id"), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)
    action_by = Column(String(100), nullable=False)
    action_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    description = Column(Text, nullable=True)
    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=True)
    details = Column(Text, nullable=True)

    reservation = relationship("Reservation", back_populates="logs")


class InspectionTask(Base):
    __tablename__ = "inspection_tasks"

    id = Column(Integer, primary_key=True, index=True)
    stool_id = Column(Integer, ForeignKey("stools.id"), nullable=False, index=True)
    stool_number = Column(String(50), index=True, nullable=False)
    storage_area = Column(String(100), nullable=False, index=True)
    load_level = Column(Enum(LoadLevel), nullable=False)
    responsible_person = Column(String(100), nullable=False, index=True)

    task_status = Column(Enum(InspectionTaskStatus), default=InspectionTaskStatus.PENDING, nullable=False, index=True)
    inspection_cycle_days = Column(Integer, nullable=False)
    last_inspection_at = Column(DateTime, nullable=True)
    scheduled_at = Column(DateTime, nullable=False, index=True)

    inspection_result = Column(Enum(InspectionResult), nullable=True)
    inspected_by = Column(String(100), nullable=True)
    inspected_at = Column(DateTime, nullable=True)
    appearance_issue = Column(Text, nullable=True)
    appearance_issue_level = Column(String(20), nullable=True)
    handling_suggestion = Column(Text, nullable=True)

    is_detained = Column(Integer, default=0, nullable=False)
    detained_reason = Column(Text, nullable=True)
    detained_at = Column(DateTime, nullable=True)
    detained_by = Column(String(100), nullable=True)

    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_result = Column(String(20), nullable=True)
    review_note = Column(Text, nullable=True)

    source_type = Column(String(50), default="周期巡检", nullable=False)
    source_record_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    stool = relationship("Stool")
    logs = relationship("InspectionTaskLog", back_populates="task", cascade="all, delete-orphan")


class InspectionTaskLog(Base):
    __tablename__ = "inspection_task_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("inspection_tasks.id"), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)
    action_by = Column(String(100), nullable=False)
    action_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    description = Column(Text, nullable=True)
    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=True)
    details = Column(Text, nullable=True)

    task = relationship("InspectionTask", back_populates="logs")
