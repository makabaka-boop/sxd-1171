from datetime import datetime, date
from typing import Optional, List
from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import engine, get_db, Base
from models import StoolStatus, LoadLevel, Stool, BorrowRecord, InspectionTaskStatus, InspectionResult
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
    InspectionTaskLogResponse
)
from crud import (
    create_stool, get_stool, list_stools, update_stool, delete_stool,
    issue_stool, return_stool, clean_stool, review_stool, detain_stool,
    get_borrow_record, list_borrow_records, enrich_records,
    generate_inspection_tasks, get_inspection_task, list_inspection_tasks,
    submit_inspection_result, review_inspection_task, restore_inspected_stool,
    get_inspection_task_detail, get_inspection_task_logs, get_inspection_tasks_summary
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
    db: Session = Depends(get_db)
):
    dt_from = parse_datetime_param(date_from, "date_from")
    dt_to = parse_datetime_param(date_to, "date_to")
    if dt_to and date_to and len(date_to) == 10:
        dt_to = dt_to.replace(hour=23, minute=59, second=59)

    total, items = list_stools(
        db, skip, limit, storage_area, load_level,
        responsible_person, status, is_abnormal, dt_from, dt_to
    )
    return StoolListResponse(total=total, items=items)


@app.get("/api/stools/{stool_id}", response_model=StoolResponse, tags=["座凳管理"], summary="查询座凳详情")
def api_get_stool(stool_id: int, db: Session = Depends(get_db)):
    stool = get_stool(db, stool_id)
    if not stool:
        raise NotFoundException("座凳", str(stool_id))
    return stool


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
    alert_type: Optional[str] = Query(None, description="预警类型：借出时长过长/高承载座凳损耗集中/存放区域连续外观问题/清洁后未复核风险"),
    severity: Optional[str] = Query(None, description="严重等级：high/medium/low"),
    db: Session = Depends(get_db)
):
    alerts = get_all_alerts(db, alert_type, severity)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8122)
