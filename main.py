from datetime import datetime, date
from typing import Optional, List
from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import engine, get_db, Base
from models import StoolStatus, LoadLevel, Stool, BorrowRecord, InspectionTaskStatus, InspectionResult, ReservationStatus, ReservationExpireStatus
from schemas import (
    StoolCreate, StoolUpdate, StoolResponse, StoolListResponse,
    IssueStoolRequest, ReturnStoolRequest, CleanStoolRequest,
    ReviewStoolRequest, DetainStoolRequest, BorrowRecordResponse,
    BorrowRecordListResponse, AlertItem, AlertListResponse,
    TurnoverDistributionResponse, TurnoverDistributionItem,
    AbnormalAreaItem, AbnormalAreaListResponse, PendingReviewSummary,
    GenerateInspectionTasksRequest, SubmitInspectionResultRequest,
    ReviewInspectionTaskRequest, RestoreInspectedStoolRequest,
    InspectionTaskResponse, InspectionTaskListResponse,
    InspectionTaskDetailResponse, InspectionTaskSummary,
    InspectionTaskLogResponse,
    CreateReservationRequest, CancelReservationRequest,
    ConfirmReservationRequest, IssueByReservationRequest,
    ReservationResponse, ReservationListResponse,
    ReservationDetailResponse, ReservationSummary,
    ReservationLogResponse,
    ReservationExpireRuleCreate, ReservationExpireRuleUpdate,
    ReservationExpireRuleResponse
)
from crud import (
    create_stool, get_stool, list_stools, update_stool, delete_stool,
    issue_stool, return_stool, clean_stool, review_stool, detain_stool,
    get_borrow_record, list_borrow_records, enrich_records,
    generate_inspection_tasks, get_inspection_task, list_inspection_tasks,
    submit_inspection_result, review_inspection_task, restore_inspected_stool,
    get_inspection_task_detail, get_inspection_task_logs, get_inspection_tasks_summary,
    create_reservation, get_reservation, get_reservation_detail,
    list_reservations, cancel_reservation, confirm_reservation,
    issue_by_reservation, expire_reservations, get_reservation_logs,
    get_reservations_summary, enrich_reservation_records,
    create_expire_rule, update_expire_rule, list_expire_rules,
    get_expire_rule, delete_expire_rule, get_active_expire_rule,
    process_expired_reservations, release_expired_reservation_stool,
    get_stool_with_reservation_info, get_reservation_with_expire_info
)
from analytics import (
    get_all_alerts, get_turnover_distribution,
    get_abnormal_areas, get_pending_review_records
)
from exceptions import NotFoundException, ValidationException

Base.metadata.create_all(bind=engine)


def ensure_compatible_schema():
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(borrow_records)"))}
        if "source_type" not in columns:
            conn.execute(text("ALTER TABLE borrow_records ADD COLUMN source_type VARCHAR(50) DEFAULT '借还流程' NOT NULL"))
        if "source_task_id" not in columns:
            conn.execute(text("ALTER TABLE borrow_records ADD COLUMN source_task_id INTEGER"))
        if "source_reservation_id" not in columns:
            conn.execute(text("ALTER TABLE borrow_records ADD COLUMN source_reservation_id INTEGER"))

        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "reservations" not in tables:
            conn.execute(text("""
                CREATE TABLE reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stool_id INTEGER NOT NULL,
                    stool_number VARCHAR(50) NOT NULL,
                    storage_area VARCHAR(100) NOT NULL,
                    load_level VARCHAR(20) NOT NULL,
                    applicant VARCHAR(100) NOT NULL,
                    applicant_contact VARCHAR(100),
                    purpose TEXT,
                    status VARCHAR(20) NOT NULL DEFAULT '待确认',
                    queue_position INTEGER,
                    priority_score FLOAT NOT NULL DEFAULT 0.0,
                    reserved_at DATETIME NOT NULL,
                    expected_use_time DATETIME,
                    expired_at DATETIME,
                    confirmed_at DATETIME,
                    confirmed_by VARCHAR(100),
                    cancelled_at DATETIME,
                    cancelled_by VARCHAR(100),
                    cancel_reason TEXT,
                    completed_at DATETIME,
                    borrow_record_id INTEGER,
                    expired_processed_at DATETIME,
                    released_at DATETIME,
                    expire_reason VARCHAR(200),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    FOREIGN KEY (stool_id) REFERENCES stools (id),
                    FOREIGN KEY (borrow_record_id) REFERENCES borrow_records (id)
                )
            """))
            conn.execute(text("CREATE INDEX idx_reservations_stool_id ON reservations(stool_id)"))
            conn.execute(text("CREATE INDEX idx_reservations_stool_number ON reservations(stool_number)"))
            conn.execute(text("CREATE INDEX idx_reservations_storage_area ON reservations(storage_area)"))
            conn.execute(text("CREATE INDEX idx_reservations_load_level ON reservations(load_level)"))
            conn.execute(text("CREATE INDEX idx_reservations_applicant ON reservations(applicant)"))
            conn.execute(text("CREATE INDEX idx_reservations_status ON reservations(status)"))
            conn.execute(text("CREATE INDEX idx_reservations_queue_position ON reservations(queue_position)"))
            conn.execute(text("CREATE INDEX idx_reservations_priority_score ON reservations(priority_score)"))
            conn.execute(text("CREATE INDEX idx_reservations_borrow_record_id ON reservations(borrow_record_id)"))
        else:
            reservation_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(reservations)"))}
            if "expired_processed_at" not in reservation_columns:
                conn.execute(text("ALTER TABLE reservations ADD COLUMN expired_processed_at DATETIME"))
            if "released_at" not in reservation_columns:
                conn.execute(text("ALTER TABLE reservations ADD COLUMN released_at DATETIME"))
            if "expire_reason" not in reservation_columns:
                conn.execute(text("ALTER TABLE reservations ADD COLUMN expire_reason VARCHAR(200)"))

        if "reservation_logs" not in tables:
            conn.execute(text("""
                CREATE TABLE reservation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id INTEGER NOT NULL,
                    action_type VARCHAR(50) NOT NULL,
                    action_by VARCHAR(100) NOT NULL,
                    action_at DATETIME NOT NULL,
                    description TEXT,
                    from_status VARCHAR(50),
                    to_status VARCHAR(50),
                    details TEXT,
                    FOREIGN KEY (reservation_id) REFERENCES reservations (id)
                )
            """))
            conn.execute(text("CREATE INDEX idx_reservation_logs_reservation_id ON reservation_logs(reservation_id)"))

        if "reservation_expire_rules" not in tables:
            conn.execute(text("""
                CREATE TABLE reservation_expire_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_name VARCHAR(100) NOT NULL UNIQUE,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    pending_expire_hours INTEGER NOT NULL DEFAULT 24,
                    confirmed_expire_hours INTEGER NOT NULL DEFAULT 12,
                    expected_use_grace_hours INTEGER NOT NULL DEFAULT 2,
                    expire_soon_hours INTEGER NOT NULL DEFAULT 2,
                    auto_release INTEGER NOT NULL DEFAULT 1,
                    auto_release_to_pending INTEGER NOT NULL DEFAULT 0,
                    auto_confirm_next INTEGER NOT NULL DEFAULT 1,
                    created_by VARCHAR(100) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """))
            conn.execute(text("CREATE INDEX idx_reservation_expire_rules_active ON reservation_expire_rules(is_active)"))
            conn.execute(text("""
                INSERT OR IGNORE INTO reservation_expire_rules (
                    rule_name, is_active, pending_expire_hours, confirmed_expire_hours,
                    expected_use_grace_hours, expire_soon_hours, auto_release,
                    auto_release_to_pending, auto_confirm_next, created_by, created_at, updated_at
                ) VALUES (
                    '默认规则', 1, 24, 12, 2, 2, 1, 0, 1, 'system',
                    DATETIME('now'), DATETIME('now')
                )
            """))


ensure_compatible_schema()

app = FastAPI(
    title="公共借阅角折叠座凳管理系统",
    description="管理折叠座凳的发放、回收、清洁确认和留置处理的后端API",
    version="1.0.0"
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors:
        first_error = errors[0]
        loc = first_error.get("loc", [])
        field = ".".join(str(x) for x in loc if x != "body") if loc else None
        msg = first_error.get("msg", "参数校验失败")
        if field and field != "":
            message = f"{field}: {msg}"
        else:
            message = msg
    else:
        message = "参数校验失败"
        field = None

    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "error_type": "validation_error",
                "message": message,
                "field": field
            }
        }
    )


def parse_datetime_param(value: Optional[str], field_name: str = "date") -> Optional[datetime]:
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime.strptime(value, "%Y-%m-%d")
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValidationException(
            f"日期格式错误，正确格式为 YYYY-MM-DD 或 ISO 8601 格式，当前值: {value}",
            field=field_name
        )


@app.get("/", tags=["系统"], summary="系统健康检查")
def health_check():
    return {
        "status": "ok",
        "service": "公共借阅角折叠座凳管理系统",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/stools", response_model=StoolResponse, tags=["座凳管理"], summary="新增座凳（管理者）")
def api_create_stool(data: StoolCreate, db: Session = Depends(get_db)):
    return create_stool(db, data)


@app.get("/api/stools", response_model=StoolListResponse, tags=["座凳管理"], summary="查询座凳列表")
def api_list_stools(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
    storage_area: Optional[str] = Query(None, description="存放区域筛选（模糊匹配）"),
    load_level: Optional[LoadLevel] = Query(None, description="承载等级筛选"),
    responsible_person: Optional[str] = Query(None, description="责任人筛选（模糊匹配）"),
    status: Optional[StoolStatus] = Query(None, description="状态筛选"),
    is_abnormal: Optional[bool] = Query(None, description="是否异常留置"),
    date_from: Optional[str] = Query(None, description="创建时间起（YYYY-MM-DD）"),
    date_to: Optional[str] = Query(None, description="创建时间止（YYYY-MM-DD）"),
    reservation_expire_status: Optional[ReservationExpireStatus] = Query(None, description="预约过期状态筛选"),
    applicant: Optional[str] = Query(None, description="预约申请人筛选（模糊匹配）"),
    db: Session = Depends(get_db)
):
    dt_from = parse_datetime_param(date_from, "date_from")
    dt_to = parse_datetime_param(date_to, "date_to")
    if dt_to and date_to and len(date_to) == 10:
        dt_to = dt_to.replace(hour=23, minute=59, second=59)

    total, items = list_stools(
        db, skip, limit, storage_area, load_level,
        responsible_person, status, is_abnormal, dt_from, dt_to,
        reservation_expire_status, applicant
    )
    enriched_items = [get_stool_with_reservation_info(db, stool) for stool in items]
    response_items = [StoolResponse(**e) for e in enriched_items]
    return StoolListResponse(total=total, items=response_items)


@app.get("/api/stools/{stool_id}", response_model=StoolResponse, tags=["座凳管理"], summary="查询座凳详情")
def api_get_stool(stool_id: int, db: Session = Depends(get_db)):
    stool = get_stool(db, stool_id)
    if not stool:
        raise NotFoundException("座凳", str(stool_id))
    enriched = get_stool_with_reservation_info(db, stool)
    return StoolResponse(**enriched)


@app.put("/api/stools/{stool_id}", response_model=StoolResponse, tags=["座凳管理"], summary="更新座凳信息（管理者）")
def api_update_stool(stool_id: int, data: StoolUpdate, db: Session = Depends(get_db)):
    return update_stool(db, stool_id, data)


@app.delete("/api/stools/{stool_id}", tags=["座凳管理"], summary="删除座凳（管理者）")
def api_delete_stool(stool_id: int, db: Session = Depends(get_db)):
    delete_stool(db, stool_id)
    return {"message": f"座凳 {stool_id} 删除成功"}


@app.post("/api/records/issue", response_model=BorrowRecordResponse, tags=["借还流程"], summary="发放座凳（值守人员）")
def api_issue_stool(data: IssueStoolRequest, db: Session = Depends(get_db)):
    record = issue_stool(db, data)
    enriched = enrich_records([record], db)[0]
    return BorrowRecordResponse(**enriched)


@app.post("/api/records/return", response_model=BorrowRecordResponse, tags=["借还流程"], summary="回收座凳（值守人员）")
def api_return_stool(data: ReturnStoolRequest, db: Session = Depends(get_db)):
    record = return_stool(db, data)
    enriched = enrich_records([record], db)[0]
    return BorrowRecordResponse(**enriched)


@app.post("/api/records/clean", response_model=BorrowRecordResponse, tags=["借还流程"], summary="清洁确认（值守人员）")
def api_clean_stool(data: CleanStoolRequest, db: Session = Depends(get_db)):
    record = clean_stool(db, data)
    enriched = enrich_records([record], db)[0]
    return BorrowRecordResponse(**enriched)


@app.post("/api/records/review", response_model=BorrowRecordResponse, tags=["借还流程"], summary="复核确认（复核人员）")
def api_review_stool(data: ReviewStoolRequest, db: Session = Depends(get_db)):
    record = review_stool(db, data)
    enriched = enrich_records([record], db)[0]
    return BorrowRecordResponse(**enriched)


@app.post("/api/records/detain", response_model=BorrowRecordResponse, tags=["借还流程"], summary="异常留置处理")
def api_detain_stool(data: DetainStoolRequest, db: Session = Depends(get_db)):
    record = detain_stool(db, data)
    enriched = enrich_records([record], db)[0]
    return BorrowRecordResponse(**enriched)


@app.get("/api/records", response_model=BorrowRecordListResponse, tags=["借还流程"], summary="查询借阅记录列表")
def api_list_records(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
    stool_id: Optional[int] = Query(None, description="座凳ID筛选"),
    storage_area: Optional[str] = Query(None, description="存放区域筛选（模糊匹配）"),
    load_level: Optional[LoadLevel] = Query(None, description="承载等级筛选"),
    responsible_person: Optional[str] = Query(None, description="责任人筛选（模糊匹配）"),
    status: Optional[StoolStatus] = Query(None, description="座凳状态筛选"),
    is_abnormal: Optional[bool] = Query(None, description="是否异常留置"),
    date_from: Optional[str] = Query(None, description="创建时间起（YYYY-MM-DD）"),
    date_to: Optional[str] = Query(None, description="创建时间止（YYYY-MM-DD）"),
    db: Session = Depends(get_db)
):
    dt_from = parse_datetime_param(date_from, "date_from")
    dt_to = parse_datetime_param(date_to, "date_to")
    if dt_to and date_to and len(date_to) == 10:
        dt_to = dt_to.replace(hour=23, minute=59, second=59)

    total, items = list_borrow_records(
        db, skip, limit, stool_id, storage_area, load_level,
        responsible_person, status, is_abnormal, dt_from, dt_to
    )
    enriched = enrich_records(items, db)
    response_items = [BorrowRecordResponse(**e) for e in enriched]
    return BorrowRecordListResponse(total=total, items=response_items)


@app.get("/api/records/{record_id}", response_model=BorrowRecordResponse, tags=["借还流程"], summary="查询借阅记录详情")
def api_get_record(record_id: int, db: Session = Depends(get_db)):
    record = get_borrow_record(db, record_id)
    if not record:
        raise NotFoundException("借阅记录", str(record_id))
    enriched = enrich_records([record], db)[0]
    return BorrowRecordResponse(**enriched)


@app.get("/api/alerts", response_model=AlertListResponse, tags=["预警识别"], summary="获取系统预警信息")
def api_get_alerts(
    alert_type: Optional[str] = Query(None, description="预警类型：借出时长过长/高承载座凳损耗集中/存放区域连续外观问题/清洁后未复核风险/预约即将过期/预约已过期未处理/预约资源已释放"),
    severity: Optional[str] = Query(None, description="严重等级：high/medium/low"),
    storage_area: Optional[str] = Query(None, description="存放区域筛选（模糊匹配）"),
    load_level: Optional[LoadLevel] = Query(None, description="承载等级筛选"),
    applicant: Optional[str] = Query(None, description="申请人筛选（模糊匹配）"),
    reservation_status: Optional[ReservationStatus] = Query(None, description="关联预约状态筛选"),
    date_from: Optional[str] = Query(None, description="预警时间起（YYYY-MM-DD）"),
    date_to: Optional[str] = Query(None, description="预警时间止（YYYY-MM-DD）"),
    db: Session = Depends(get_db)
):
    alerts = get_all_alerts(db, alert_type, severity)

    if storage_area:
        alerts = [a for a in alerts if a.details and a.details.get("storage_area") and storage_area in a.details.get("storage_area", "")]
    if load_level:
        alerts = [a for a in alerts if a.details and a.details.get("load_level") == load_level.value]
    if applicant:
        alerts = [a for a in alerts if a.details and a.details.get("applicant") and applicant in a.details.get("applicant", "")]
    if reservation_status:
        alerts = [a for a in alerts if a.details and a.details.get("status") == reservation_status.value]
    if date_from:
        dt_from = parse_datetime_param(date_from, "date_from")
        alerts = [a for a in alerts if a.details and a.details.get("created_at") and datetime.fromisoformat(a.details.get("created_at", "")) >= dt_from]
    if date_to:
        dt_to = parse_datetime_param(date_to, "date_to")
        if date_to and len(date_to) == 10:
            dt_to = dt_to.replace(hour=23, minute=59, second=59)
        alerts = [a for a in alerts if a.details and a.details.get("created_at") and datetime.fromisoformat(a.details.get("created_at", "")) <= dt_to]

    return AlertListResponse(total=len(alerts), items=alerts)


@app.get("/api/stats/turnover", response_model=TurnoverDistributionResponse, tags=["统计分析"], summary="周转时长分布")
def api_turnover_stats(
    storage_area: Optional[str] = Query(None, description="存放区域筛选"),
    load_level: Optional[LoadLevel] = Query(None, description="承载等级筛选"),
    date_from: Optional[str] = Query(None, description="统计时间起（YYYY-MM-DD）"),
    date_to: Optional[str] = Query(None, description="统计时间止（YYYY-MM-DD）"),
    db: Session = Depends(get_db)
):
    dt_from = parse_datetime_param(date_from, "date_from")
    dt_to = parse_datetime_param(date_to, "date_to")
    if dt_to and date_to and len(date_to) == 10:
        dt_to = dt_to.replace(hour=23, minute=59, second=59)

    total, avg_hours, dist = get_turnover_distribution(db, storage_area, load_level, dt_from, dt_to)
    return TurnoverDistributionResponse(
        total_records=total,
        avg_turnover_hours=avg_hours,
        distribution=dist
    )


@app.get("/api/stats/abnormal-areas", response_model=AbnormalAreaListResponse, tags=["统计分析"], summary="异常高发区域")
def api_abnormal_areas(
    min_abnormal_rate: float = Query(0.0, ge=0.0, le=100.0, description="最低异常率阈值（%）"),
    date_from: Optional[str] = Query(None, description="统计时间起（YYYY-MM-DD）"),
    date_to: Optional[str] = Query(None, description="统计时间止（YYYY-MM-DD）"),
    db: Session = Depends(get_db)
):
    dt_from = parse_datetime_param(date_from, "date_from")
    dt_to = parse_datetime_param(date_to, "date_to")
    if dt_to and date_to and len(date_to) == 10:
        dt_to = dt_to.replace(hour=23, minute=59, second=59)

    total, items = get_abnormal_areas(db, min_abnormal_rate, dt_from, dt_to)
    return AbnormalAreaListResponse(total_areas=total, items=items)


@app.get("/api/stats/pending-review", response_model=PendingReviewSummary, tags=["统计分析"], summary="待复核记录汇总")
def api_pending_review(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
    storage_area: Optional[str] = Query(None, description="存放区域筛选"),
    db: Session = Depends(get_db)
):
    result = get_pending_review_records(db, skip, limit, storage_area)
    enriched = enrich_records(result["items"], db)
    response_items = [BorrowRecordResponse(**e) for e in enriched]
    return PendingReviewSummary(
        total_pending=result["total_pending"],
        over_24h_count=result["over_24h_count"],
        over_3d_count=result["over_3d_count"],
        items=response_items
    )


@app.post("/api/inspection/generate", tags=["巡检管理"], summary="生成巡检任务（管理者）")
def api_generate_inspection_tasks(
    data: GenerateInspectionTasksRequest = None,
    db: Session = Depends(get_db)
):
    source_type = data.source_type if data else "周期巡检"
    tasks = generate_inspection_tasks(db, source_type)
    return {
        "message": f"成功生成 {len(tasks)} 条巡检任务",
        "generated_count": len(tasks),
        "tasks": [InspectionTaskResponse.model_validate(t) for t in tasks]
    }


@app.get("/api/inspection/tasks", response_model=InspectionTaskListResponse, tags=["巡检管理"], summary="查询巡检任务列表")
def api_list_inspection_tasks(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
    storage_area: Optional[str] = Query(None, description="存放区域筛选（模糊匹配）"),
    responsible_person: Optional[str] = Query(None, description="责任人筛选（模糊匹配）"),
    task_status: Optional[InspectionTaskStatus] = Query(None, description="任务状态筛选"),
    load_level: Optional[LoadLevel] = Query(None, description="承载等级筛选"),
    source_type: Optional[str] = Query(None, description="任务来源类型筛选"),
    is_abnormal: Optional[bool] = Query(None, description="是否异常"),
    date_from: Optional[str] = Query(None, description="创建时间起（YYYY-MM-DD）"),
    date_to: Optional[str] = Query(None, description="创建时间止（YYYY-MM-DD）"),
    db: Session = Depends(get_db)
):
    dt_from = parse_datetime_param(date_from, "date_from")
    dt_to = parse_datetime_param(date_to, "date_to")
    if dt_to and date_to and len(date_to) == 10:
        dt_to = dt_to.replace(hour=23, minute=59, second=59)

    total, items = list_inspection_tasks(
        db, skip, limit, storage_area, responsible_person,
        task_status, load_level, source_type, is_abnormal, dt_from, dt_to
    )
    return InspectionTaskListResponse(total=total, items=items)


@app.get("/api/inspection/tasks/{task_id}", response_model=InspectionTaskDetailResponse, tags=["巡检管理"], summary="查询巡检任务详情")
def api_get_inspection_task(task_id: int, db: Session = Depends(get_db)):
    task = get_inspection_task_detail(db, task_id)
    if not task:
        raise NotFoundException("巡检任务", str(task_id))
    return InspectionTaskDetailResponse.model_validate(task)


@app.get("/api/inspection/tasks/{task_id}/logs", tags=["巡检管理"], summary="查询巡检任务处理记录")
def api_get_inspection_task_logs(task_id: int, db: Session = Depends(get_db)):
    task = get_inspection_task(db, task_id)
    if not task:
        raise NotFoundException("巡检任务", str(task_id))
    logs = get_inspection_task_logs(db, task_id)
    return {
        "total": len(logs),
        "items": [InspectionTaskLogResponse.model_validate(log) for log in logs]
    }


@app.post("/api/inspection/submit", response_model=InspectionTaskResponse, tags=["巡检管理"], summary="提交巡检结果（值守人员）")
def api_submit_inspection_result(data: SubmitInspectionResultRequest, db: Session = Depends(get_db)):
    task = submit_inspection_result(db, data)
    return InspectionTaskResponse.model_validate(task)


@app.post("/api/inspection/review", response_model=InspectionTaskResponse, tags=["巡检管理"], summary="复核巡检异常（复核人员）")
def api_review_inspection_task(data: ReviewInspectionTaskRequest, db: Session = Depends(get_db)):
    task = review_inspection_task(db, data)
    return InspectionTaskResponse.model_validate(task)


@app.post("/api/inspection/restore", response_model=InspectionTaskResponse, tags=["巡检管理"], summary="恢复异常留置座凳（管理者）")
def api_restore_inspected_stool(data: RestoreInspectedStoolRequest, db: Session = Depends(get_db)):
    task = restore_inspected_stool(db, data)
    return InspectionTaskResponse.model_validate(task)


@app.get("/api/inspection/summary", response_model=InspectionTaskSummary, tags=["巡检管理"], summary="巡检任务统计概览")
def api_inspection_summary(db: Session = Depends(get_db)):
    summary = get_inspection_tasks_summary(db)
    return InspectionTaskSummary(**summary)


@app.post("/api/reservations", response_model=ReservationResponse, tags=["预约管理"], summary="提交预约申请（使用人）")
def api_create_reservation(data: CreateReservationRequest, db: Session = Depends(get_db)):
    reservation = create_reservation(db, data)
    return reservation


@app.get("/api/reservations", response_model=ReservationListResponse, tags=["预约管理"], summary="查询预约列表")
def api_list_reservations(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
    stool_id: Optional[int] = Query(None, description="座凳ID筛选"),
    storage_area: Optional[str] = Query(None, description="存放区域筛选（模糊匹配）"),
    load_level: Optional[LoadLevel] = Query(None, description="承载等级筛选"),
    applicant: Optional[str] = Query(None, description="预约人筛选（模糊匹配）"),
    status: Optional[ReservationStatus] = Query(None, description="预约状态筛选"),
    expire_status: Optional[ReservationExpireStatus] = Query(None, description="过期状态筛选"),
    is_expire_soon: Optional[bool] = Query(None, description="是否即将过期"),
    date_from: Optional[str] = Query(None, description="创建时间起（YYYY-MM-DD）"),
    date_to: Optional[str] = Query(None, description="创建时间止（YYYY-MM-DD）"),
    expected_use_from: Optional[str] = Query(None, description="期望使用时间起（YYYY-MM-DD）"),
    expected_use_to: Optional[str] = Query(None, description="期望使用时间止（YYYY-MM-DD）"),
    db: Session = Depends(get_db)
):
    dt_from = parse_datetime_param(date_from, "date_from")
    dt_to = parse_datetime_param(date_to, "date_to")
    if dt_to and date_to and len(date_to) == 10:
        dt_to = dt_to.replace(hour=23, minute=59, second=59)

    exp_use_from = parse_datetime_param(expected_use_from, "expected_use_from")
    exp_use_to = parse_datetime_param(expected_use_to, "expected_use_to")
    if exp_use_to and expected_use_to and len(expected_use_to) == 10:
        exp_use_to = exp_use_to.replace(hour=23, minute=59, second=59)

    total, items = list_reservations(
        db, skip, limit, stool_id, storage_area, load_level,
        applicant, status, expire_status, is_expire_soon,
        dt_from, dt_to, exp_use_from, exp_use_to
    )
    enriched_items = [get_reservation_with_expire_info(db, res) for res in items]
    response_items = [ReservationResponse(**e) for e in enriched_items]
    return ReservationListResponse(total=total, items=response_items)


@app.get("/api/reservations/summary", response_model=ReservationSummary, tags=["预约管理"], summary="预约统计概览")
def api_reservations_summary(db: Session = Depends(get_db)):
    summary = get_reservations_summary(db)
    return ReservationSummary(**summary)


@app.get("/api/reservations/{reservation_id}", response_model=ReservationDetailResponse, tags=["预约管理"], summary="查询预约详情")
def api_get_reservation(reservation_id: int, db: Session = Depends(get_db)):
    reservation = get_reservation_detail(db, reservation_id)
    if not reservation:
        raise NotFoundException("预约记录", str(reservation_id))
    enriched = get_reservation_with_expire_info(db, reservation)
    enriched["logs"] = reservation.logs
    return ReservationDetailResponse(**enriched)


@app.post("/api/reservations/cancel", response_model=ReservationResponse, tags=["预约管理"], summary="取消预约（使用人/管理者）")
def api_cancel_reservation(data: CancelReservationRequest, db: Session = Depends(get_db)):
    reservation = cancel_reservation(db, data)
    return reservation


@app.post("/api/reservations/confirm", response_model=ReservationResponse, tags=["预约管理"], summary="确认预约到场（值守人员）")
def api_confirm_reservation(data: ConfirmReservationRequest, db: Session = Depends(get_db)):
    reservation = confirm_reservation(db, data)
    return reservation


@app.post("/api/reservations/issue", response_model=BorrowRecordResponse, tags=["预约管理"], summary="通过预约发放座凳（值守人员）")
def api_issue_by_reservation(data: IssueByReservationRequest, db: Session = Depends(get_db)):
    record = issue_by_reservation(db, data)
    enriched = enrich_records([record], db)[0]
    return BorrowRecordResponse(**enriched)


@app.post("/api/reservations/expire", tags=["预约管理"], summary="触发过期预约自动释放（系统/管理者）")
def api_expire_reservations(db: Session = Depends(get_db)):
    result = process_expired_reservations(db)
    return {
        "message": f"成功处理 {result['expired_count']} 条过期预约",
        "expired_count": result["expired_count"],
        "released_count": result["released_count"],
        "auto_confirm_count": result["auto_confirm_count"]
    }


@app.post("/api/reservations/{reservation_id}/release", response_model=ReservationResponse, tags=["预约管理"], summary="手动释放过期预约的座凳资源（管理者）")
def api_release_expired_reservation_stool(
    reservation_id: int,
    released_by: str = Query(..., min_length=1, max_length=100, description="释放操作人"),
    to_pending: bool = Query(False, description="是否释放为待发放状态"),
    db: Session = Depends(get_db)
):
    reservation = release_expired_reservation_stool(db, reservation_id, released_by, to_pending)
    enriched = get_reservation_with_expire_info(db, reservation)
    return ReservationResponse(**enriched)


@app.get("/api/reservations/{reservation_id}/logs", tags=["预约管理"], summary="查询预约操作记录")
def api_get_reservation_logs(reservation_id: int, db: Session = Depends(get_db)):
    reservation = get_reservation(db, reservation_id)
    if not reservation:
        raise NotFoundException("预约记录", str(reservation_id))
    logs = get_reservation_logs(db, reservation_id)
    return {
        "total": len(logs),
        "items": [ReservationLogResponse.model_validate(log) for log in logs]
    }


@app.get("/api/reservation-expire-rules", tags=["预约到期规则"], summary="查询预约到期规则列表")
def api_list_expire_rules(db: Session = Depends(get_db)):
    rules = list_expire_rules(db)
    return {
        "total": len(rules),
        "items": [ReservationExpireRuleResponse.model_validate(r) for r in rules]
    }


@app.get("/api/reservation-expire-rules/active", response_model=ReservationExpireRuleResponse, tags=["预约到期规则"], summary="获取当前启用的到期规则")
def api_get_active_expire_rule(db: Session = Depends(get_db)):
    rule = get_active_expire_rule(db)
    return ReservationExpireRuleResponse.model_validate(rule)


@app.get("/api/reservation-expire-rules/{rule_id}", response_model=ReservationExpireRuleResponse, tags=["预约到期规则"], summary="查询预约到期规则详情")
def api_get_expire_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = get_expire_rule(db, rule_id)
    if not rule:
        raise NotFoundException("到期规则", str(rule_id))
    return ReservationExpireRuleResponse.model_validate(rule)


@app.post("/api/reservation-expire-rules", response_model=ReservationExpireRuleResponse, tags=["预约到期规则"], summary="创建预约到期规则（管理者）")
def api_create_expire_rule(data: ReservationExpireRuleCreate, db: Session = Depends(get_db)):
    rule = create_expire_rule(db, data)
    return ReservationExpireRuleResponse.model_validate(rule)


@app.put("/api/reservation-expire-rules/{rule_id}", response_model=ReservationExpireRuleResponse, tags=["预约到期规则"], summary="更新预约到期规则（管理者）")
def api_update_expire_rule(rule_id: int, data: ReservationExpireRuleUpdate, db: Session = Depends(get_db)):
    rule = update_expire_rule(db, rule_id, data)
    return ReservationExpireRuleResponse.model_validate(rule)


@app.delete("/api/reservation-expire-rules/{rule_id}", tags=["预约到期规则"], summary="删除预约到期规则（管理者）")
def api_delete_expire_rule(rule_id: int, db: Session = Depends(get_db)):
    delete_expire_rule(db, rule_id)
    return {"message": f"到期规则 {rule_id} 删除成功"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8122)
