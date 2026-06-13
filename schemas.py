from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from models import StoolStatus, LoadLevel


class StoolBase(BaseModel):
    stool_number: str = Field(..., min_length=1, max_length=50, description="座凳编号")
    storage_area: str = Field(..., min_length=1, max_length=100, description="存放区域")
    load_level: LoadLevel = Field(..., description="承载等级")
    inspection_cycle_days: int = Field(..., ge=1, description="巡查周期（天）")
    responsible_person: str = Field(..., min_length=1, max_length=100, description="责任人")


class StoolCreate(StoolBase):
    pass


class StoolUpdate(BaseModel):
    storage_area: Optional[str] = Field(None, min_length=1, max_length=100)
    load_level: Optional[LoadLevel] = None
    inspection_cycle_days: Optional[int] = Field(None, ge=1)
    responsible_person: Optional[str] = Field(None, min_length=1, max_length=100)
    status: Optional[StoolStatus] = None


class StoolResponse(StoolBase):
    id: int
    status: StoolStatus
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BorrowRecordBase(BaseModel):
    pass


class IssueStoolRequest(BaseModel):
    stool_id: int = Field(..., description="座凳ID")
    borrower: Optional[str] = Field(None, max_length=100, description="借用人")
    borrower_contact: Optional[str] = Field(None, max_length=100, description="借用人联系方式")
    issued_by: str = Field(..., min_length=1, max_length=100, description="发放操作人（值守人员）")


class ReturnStoolRequest(BaseModel):
    record_id: int = Field(..., description="借阅记录ID")
    returned_by: str = Field(..., min_length=1, max_length=100, description="回收操作人（值守人员）")
    appearance_issue: Optional[str] = Field(None, description="外观问题描述")
    appearance_issue_level: Optional[str] = Field(None, description="外观问题等级: 轻微/一般/严重")


class CleanStoolRequest(BaseModel):
    record_id: int = Field(..., description="借阅记录ID")
    cleaned_by: str = Field(..., min_length=1, max_length=100, description="清洁操作人（值守人员）")
    cleaning_result: str = Field(..., description="清洁结果: 合格/不合格/需复核")
    cleaning_note: Optional[str] = Field(None, description="清洁备注")


class ReviewStoolRequest(BaseModel):
    record_id: int = Field(..., description="借阅记录ID")
    reviewed_by: str = Field(..., min_length=1, max_length=100, description="复核操作人（复核人员）")
    review_result: str = Field(..., description="复核结果: 恢复可用/需留置/再次清洁")
    review_note: Optional[str] = Field(None, description="复核备注")


class DetainStoolRequest(BaseModel):
    record_id: int = Field(..., description="借阅记录ID")
    detained_by: str = Field(..., min_length=1, max_length=100, description="留置操作人")
    detained_reason: str = Field(..., min_length=1, description="留置原因")


class BorrowRecordResponse(BaseModel):
    id: int
    stool_id: int
    stool_number: Optional[str] = None
    borrower: Optional[str] = None
    borrower_contact: Optional[str] = None

    issued_at: Optional[datetime] = None
    issued_by: Optional[str] = None

    returned_at: Optional[datetime] = None
    returned_by: Optional[str] = None
    appearance_issue: Optional[str] = None
    appearance_issue_level: Optional[str] = None

    cleaned_at: Optional[datetime] = None
    cleaned_by: Optional[str] = None
    cleaning_result: Optional[str] = None
    cleaning_note: Optional[str] = None

    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    review_result: Optional[str] = None
    review_note: Optional[str] = None

    is_detained: int = 0
    detained_reason: Optional[str] = None
    detained_at: Optional[datetime] = None
    detained_by: Optional[str] = None

    turnover_hours: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class StoolListResponse(BaseModel):
    total: int
    items: List[StoolResponse]


class BorrowRecordListResponse(BaseModel):
    total: int
    items: List[BorrowRecordResponse]


class AlertItem(BaseModel):
    alert_type: str
    severity: str
    message: str
    related_id: Optional[int] = None
    related_stool_number: Optional[str] = None
    details: Optional[dict] = None


class AlertListResponse(BaseModel):
    total: int
    items: List[AlertItem]


class TurnoverDistributionItem(BaseModel):
    range: str
    count: int
    percentage: float


class TurnoverDistributionResponse(BaseModel):
    total_records: int
    avg_turnover_hours: Optional[float] = None
    distribution: List[TurnoverDistributionItem]


class AbnormalAreaItem(BaseModel):
    storage_area: str
    total_records: int
    abnormal_count: int
    abnormal_rate: float
    recent_abnormal_count: int


class AbnormalAreaListResponse(BaseModel):
    total_areas: int
    items: List[AbnormalAreaItem]


class PendingReviewSummary(BaseModel):
    total_pending: int
    over_24h_count: int
    over_3d_count: int
    items: List[BorrowRecordResponse]
