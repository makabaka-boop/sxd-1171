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
    PENDING_INSPECTION_REVIEW = "待巡检复核"


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

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    stool = relationship("Stool", back_populates="borrow_records")


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
