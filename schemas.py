from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from models import StoolStatus, LoadLevel, InspectionTaskStatus, InspectionResult, ReservationStatus, ReservationExpireStatus


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

    active_reservation_id: Optional[int] = None
    active_reservation_status: Optional[ReservationStatus] = None
    active_reservation_expire_status: Optional[ReservationExpireStatus] = None
    active_reservation_applicant: Optional[str] = None
    active_reservation_expired_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ReservationExpireRuleBase(BaseModel):
    rule_name: str = Field(..., min_length=1, max_length=100, description="规则名称")
    is_active: int = Field(1, ge=0, le=1, description="是否启用")
    pending_expire_hours: int = Field(24, ge=1, le=168, description="待确认预约过期时间(小时)")
    confirmed_expire_hours: int = Field(12, ge=1, le=72, description="已确认预约过期时间(小时)")
    expected_use_grace_hours: int = Field(2, ge=0, le=24, description="超过预期使用时间的宽限期(小时)")
    expire_soon_hours: int = Field(2, ge=0, le=24, description="即将过期预警阈值(小时)")
    auto_release: int = Field(1, ge=0, le=1, description="是否自动释放座凳资源")
    auto_release_to_pending: int = Field(0, ge=0, le=1, description="释放后是否置为待发放(否则为恢复可用)")
    auto_confirm_next: int = Field(1, ge=0, le=1, description="是否自动确认下一个排队预约")


class ReservationExpireRuleCreate(ReservationExpireRuleBase):
    created_by: str = Field(..., min_length=1, max_length=100, description="创建人")


class ReservationExpireRuleUpdate(BaseModel):
    rule_name: Optional[str] = Field(None, min_length=1, max_length=100)
    is_active: Optional[int] = Field(None, ge=0, le=1)
    pending_expire_hours: Optional[int] = Field(None, ge=1, le=168)
    confirmed_expire_hours: Optional[int] = Field(None, ge=1, le=72)
    expected_use_grace_hours: Optional[int] = Field(None, ge=0, le=24)
    expire_soon_hours: Optional[int] = Field(None, ge=0, le=24)
    auto_release: Optional[int] = Field(None, ge=0, le=1)
    auto_release_to_pending: Optional[int] = Field(None, ge=0, le=1)
    auto_confirm_next: Optional[int] = Field(None, ge=0, le=1)


class ReservationExpireRuleResponse(ReservationExpireRuleBase):
    id: int
    created_by: str
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
    source_type: str = "借还流程"
    source_task_id: Optional[int] = None
    source_reservation_id: Optional[int] = None
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


class InspectionTaskBase(BaseModel):
    pass


class GenerateInspectionTasksRequest(BaseModel):
    source_type: Optional[str] = Field("周期巡检", description="任务来源类型：周期巡检/专项巡检/其他")


class SubmitInspectionResultRequest(BaseModel):
    task_id: int = Field(..., description="巡检任务ID")
    inspection_result: InspectionResult = Field(..., description="巡检结果：正常/异常")
    inspected_by: str = Field(..., min_length=1, max_length=100, description="巡检操作人（值守人员）")
    appearance_issue: Optional[str] = Field(None, description="外观问题描述")
    appearance_issue_level: Optional[str] = Field(None, description="外观问题等级：轻微/一般/严重")
    handling_suggestion: Optional[str] = Field(None, description="处理建议")
    abnormal_action: Optional[str] = Field(None, description="异常处理方式：留置/待复核")


class ReviewInspectionTaskRequest(BaseModel):
    task_id: int = Field(..., description="巡检任务ID")
    reviewed_by: str = Field(..., min_length=1, max_length=100, description="复核操作人")
    review_result: str = Field(..., description="复核结果：恢复可用/需留置/再次巡检")
    review_note: Optional[str] = Field(None, description="复核备注")


class RestoreInspectedStoolRequest(BaseModel):
    task_id: int = Field(..., description="巡检任务ID")
    restored_by: str = Field(..., min_length=1, max_length=100, description="恢复操作人")
    restore_note: Optional[str] = Field(None, description="恢复备注")


class InspectionTaskLogResponse(BaseModel):
    id: int
    task_id: int
    action_type: str
    action_by: str
    action_at: datetime
    description: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    details: Optional[str] = None

    class Config:
        from_attributes = True


class InspectionTaskResponse(BaseModel):
    id: int
    stool_id: int
    stool_number: str
    storage_area: str
    load_level: LoadLevel
    responsible_person: str

    task_status: InspectionTaskStatus
    inspection_cycle_days: int
    last_inspection_at: Optional[datetime] = None
    scheduled_at: datetime

    inspection_result: Optional[InspectionResult] = None
    inspected_by: Optional[str] = None
    inspected_at: Optional[datetime] = None
    appearance_issue: Optional[str] = None
    appearance_issue_level: Optional[str] = None
    handling_suggestion: Optional[str] = None

    is_detained: int = 0
    detained_reason: Optional[str] = None
    detained_at: Optional[datetime] = None
    detained_by: Optional[str] = None

    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_result: Optional[str] = None
    review_note: Optional[str] = None

    source_type: str
    source_record_id: Optional[int] = None

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class InspectionTaskDetailResponse(InspectionTaskResponse):
    logs: List[InspectionTaskLogResponse] = []


class InspectionTaskListResponse(BaseModel):
    total: int
    items: List[InspectionTaskResponse]


class InspectionTaskSummary(BaseModel):
    total_tasks: int
    pending_count: int
    in_progress_count: int
    completed_normal_count: int
    completed_abnormal_count: int
    abnormal_detained_count: int
    abnormal_pending_review_count: int
    closed_count: int


class CreateReservationRequest(BaseModel):
    stool_id: int = Field(..., description="座凳ID")
    applicant: str = Field(..., min_length=1, max_length=100, description="预约使用人")
    applicant_contact: Optional[str] = Field(None, max_length=100, description="联系方式")
    purpose: Optional[str] = Field(None, description="使用用途")
    expected_use_time: Optional[datetime] = Field(None, description="期望使用时间")


class CancelReservationRequest(BaseModel):
    reservation_id: int = Field(..., description="预约ID")
    cancelled_by: str = Field(..., min_length=1, max_length=100, description="取消操作人")
    cancel_reason: Optional[str] = Field(None, description="取消原因")


class ConfirmReservationRequest(BaseModel):
    reservation_id: int = Field(..., description="预约ID")
    confirmed_by: str = Field(..., min_length=1, max_length=100, description="确认操作人（值守人员）")
    borrower: Optional[str] = Field(None, max_length=100, description="借用人（默认使用预约人）")
    borrower_contact: Optional[str] = Field(None, max_length=100, description="借用人联系方式")


class ReservationLogResponse(BaseModel):
    id: int
    reservation_id: int
    action_type: str
    action_by: str
    action_at: datetime
    description: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    details: Optional[str] = None

    class Config:
        from_attributes = True


class ReservationResponse(BaseModel):
    id: int
    stool_id: int
    stool_number: str
    storage_area: str
    load_level: LoadLevel
    applicant: str
    applicant_contact: Optional[str] = None
    purpose: Optional[str] = None
    status: ReservationStatus
    queue_position: Optional[int] = None
    priority_score: float
    reserved_at: datetime
    expected_use_time: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    confirmed_by: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[str] = None
    cancel_reason: Optional[str] = None
    completed_at: Optional[datetime] = None
    borrow_record_id: Optional[int] = None

    expire_status: Optional[ReservationExpireStatus] = None
    is_expire_soon: Optional[bool] = None
    expired_processed_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    expire_reason: Optional[str] = None

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReservationDetailResponse(ReservationResponse):
    logs: List[ReservationLogResponse] = []


class ReservationListResponse(BaseModel):
    total: int
    items: List[ReservationResponse]


class ReservationSummary(BaseModel):
    total_reservations: int
    pending_count: int
    confirmed_count: int
    cancelled_count: int
    expired_count: int
    completed_count: int
    released_count: int
    total_queue_count: int
    expire_soon_count: int
    expired_unhandled_count: int
    expired_handled_count: int


class IssueByReservationRequest(BaseModel):
    reservation_id: int = Field(..., description="预约ID")
    issued_by: str = Field(..., min_length=1, max_length=100, description="发放操作人（值守人员）")
    borrower: Optional[str] = Field(None, max_length=100, description="借用人（默认使用预约人）")
    borrower_contact: Optional[str] = Field(None, max_length=100, description="借用人联系方式")
