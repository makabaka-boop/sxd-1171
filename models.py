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


class UserRole(str, enum.Enum):
    MANAGER = "管理者"
    ATTENDANT = "值守人员"
    REVIEWER = "复核人员"


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
