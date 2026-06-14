import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

from database import Base
from models import (
    Stool, StoolStatus, LoadLevel,
    InspectionTask, InspectionTaskStatus, InspectionResult,
    BorrowRecord
)
from schemas import (
    StoolCreate, SubmitInspectionResultRequest,
    ReviewInspectionTaskRequest, RestoreInspectedStoolRequest
)
from crud import (
    create_stool, generate_inspection_tasks,
    get_inspection_task, list_inspection_tasks,
    submit_inspection_result, review_inspection_task,
    restore_inspected_stool, get_inspection_task_detail,
    get_inspection_tasks_summary, get_last_inspection_time
)


TEST_DATABASE_URL = "sqlite:///./test_inspection.db"


@pytest.fixture
def db():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_inspection.db"):
            os.remove("./test_inspection.db")


def create_test_stool(db, number="STOOL-001", area="A区", level=LoadLevel.MEDIUM, cycle=1, person="张三", created_days_ago=2):
    data = StoolCreate(
        stool_number=number,
        storage_area=area,
        load_level=level,
        inspection_cycle_days=cycle,
        responsible_person=person
    )
    stool = create_stool(db, data)
    old_time = datetime.utcnow() - timedelta(days=created_days_ago)
    stool.created_at = old_time
    stool.updated_at = old_time
    db.commit()
    db.refresh(stool)
    return stool


class TestInspectionTaskGeneration:
    def test_generate_inspection_tasks_new_stools(self, db):
        create_test_stool(db, "STOOL-001")
        create_test_stool(db, "STOOL-002")

        tasks = generate_inspection_tasks(db)
        assert len(tasks) == 2
        for task in tasks:
            assert task.task_status == InspectionTaskStatus.PENDING
            assert task.source_type == "周期巡检"

    def test_generate_inspection_tasks_no_duplicate(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)
        tasks = generate_inspection_tasks(db)
        assert len(tasks) == 0

    def test_generate_inspection_tasks_skip_borrowed(self, db):
        stool = create_test_stool(db, "STOOL-001")
        stool.status = StoolStatus.BORROWED
        db.commit()

        tasks = generate_inspection_tasks(db)
        assert len(tasks) == 0

    def test_generate_inspection_tasks_after_cycle(self, db):
        stool = create_test_stool(db, "STOOL-001", cycle=1, created_days_ago=2)

        tasks = generate_inspection_tasks(db)
        assert len(tasks) == 1
        task = tasks[0]

        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="李四"
        )
        submit_inspection_result(db, submit_data)

        old_inspected_at = get_last_inspection_time(db, stool.id)
        assert old_inspected_at is not None

        tasks2 = generate_inspection_tasks(db)
        assert len(tasks2) == 0

        task.inspected_at = datetime.utcnow() - timedelta(days=2)
        db.commit()

        tasks3 = generate_inspection_tasks(db)
        assert len(tasks3) == 1

    def test_generate_with_source_type(self, db):
        create_test_stool(db, "STOOL-001")
        tasks = generate_inspection_tasks(db, source_type="专项巡检")
        assert len(tasks) == 1
        assert tasks[0].source_type == "专项巡检"


class TestInspectionTaskList:
    def test_list_all_tasks(self, db):
        for i in range(5):
            create_test_stool(db, f"STOOL-00{i+1}")
        generate_inspection_tasks(db)

        total, items = list_inspection_tasks(db)
        assert total == 5

    def test_list_filter_by_area(self, db):
        create_test_stool(db, "STOOL-001", area="A区")
        create_test_stool(db, "STOOL-002", area="B区")
        generate_inspection_tasks(db)

        total, items = list_inspection_tasks(db, storage_area="A")
        assert total == 1
        assert items[0].storage_area == "A区"

    def test_list_filter_by_responsible(self, db):
        create_test_stool(db, "STOOL-001", person="张三")
        create_test_stool(db, "STOOL-002", person="李四")
        generate_inspection_tasks(db)

        total, items = list_inspection_tasks(db, responsible_person="张")
        assert total == 1
        assert items[0].responsible_person == "张三"

    def test_list_filter_by_status(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        total, items = list_inspection_tasks(db, task_status=InspectionTaskStatus.PENDING)
        assert total == 1

        task = items[0]
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="李四"
        )
        submit_inspection_result(db, submit_data)

        total2, _ = list_inspection_tasks(db, task_status=InspectionTaskStatus.COMPLETED_NORMAL)
        assert total2 == 1

    def test_list_filter_by_load_level(self, db):
        create_test_stool(db, "STOOL-001", level=LoadLevel.LIGHT)
        create_test_stool(db, "STOOL-002", level=LoadLevel.HEAVY)
        generate_inspection_tasks(db)

        total, items = list_inspection_tasks(db, load_level=LoadLevel.HEAVY)
        assert total == 1
        assert items[0].load_level == LoadLevel.HEAVY

    def test_list_filter_by_is_abnormal(self, db):
        create_test_stool(db, "STOOL-001")
        create_test_stool(db, "STOOL-002")
        generate_inspection_tasks(db)

        tasks = db.query(InspectionTask).all()
        submit_data1 = SubmitInspectionResultRequest(
            task_id=tasks[0].id,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="李四"
        )
        submit_inspection_result(db, submit_data1)

        submit_data2 = SubmitInspectionResultRequest(
            task_id=tasks[1].id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue="表面有划痕",
            appearance_issue_level="轻微",
            abnormal_action="待复核"
        )
        submit_inspection_result(db, submit_data2)

        total_abnormal, _ = list_inspection_tasks(db, is_abnormal=True)
        assert total_abnormal == 1

        total_normal, _ = list_inspection_tasks(db, is_abnormal=False)
        assert total_normal == 1


class TestSubmitInspectionResult:
    def test_submit_normal_result(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="李四"
        )
        result = submit_inspection_result(db, submit_data)

        assert result.task_status == InspectionTaskStatus.COMPLETED_NORMAL
        assert result.inspection_result == InspectionResult.NORMAL
        assert result.inspected_by == "李四"
        assert result.inspected_at is not None

        stool = db.query(Stool).filter(Stool.id == task.stool_id).first()
        assert stool.status == StoolStatus.RESTORED

    def test_submit_abnormal_result_detain(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue="椅面破损",
            appearance_issue_level="严重",
            handling_suggestion="需要维修",
            abnormal_action="留置"
        )
        result = submit_inspection_result(db, submit_data)

        assert result.task_status == InspectionTaskStatus.ABNORMAL_DETAINED
        assert result.is_detained == 1
        assert result.detained_reason is not None
        assert result.detained_at is not None

        stool = db.query(Stool).filter(Stool.id == task.stool_id).first()
        assert stool.status == StoolStatus.DETAINED

    def test_submit_abnormal_result_pending_review(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue="表面轻微划痕",
            appearance_issue_level="轻微",
            handling_suggestion="建议复核",
            abnormal_action="待复核"
        )
        result = submit_inspection_result(db, submit_data)

        assert result.task_status == InspectionTaskStatus.ABNORMAL_PENDING_REVIEW

        stool = db.query(Stool).filter(Stool.id == task.stool_id).first()
        assert stool.status == StoolStatus.PENDING_INSPECTION_REVIEW

    def test_submit_result_not_found(self, db):
        from exceptions import NotFoundException
        submit_data = SubmitInspectionResultRequest(
            task_id=999,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="李四"
        )
        with pytest.raises(NotFoundException):
            submit_inspection_result(db, submit_data)

    def test_submit_result_status_conflict(self, db):
        from exceptions import StatusConflictException
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="李四"
        )
        submit_inspection_result(db, submit_data)

        with pytest.raises(StatusConflictException):
            submit_inspection_result(db, submit_data)


class TestReviewInspectionTask:
    def test_review_restore(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue="轻微问题",
            abnormal_action="待复核"
        )
        submit_inspection_result(db, submit_data)

        review_data = ReviewInspectionTaskRequest(
            task_id=task.id,
            reviewed_by="王五",
            review_result="恢复可用",
            review_note="问题不大，可正常使用"
        )
        result = review_inspection_task(db, review_data)

        assert result.task_status == InspectionTaskStatus.COMPLETED_ABNORMAL
        assert result.review_result == "恢复可用"
        assert result.reviewed_by == "王五"

        stool = db.query(Stool).filter(Stool.id == task.stool_id).first()
        assert stool.status == StoolStatus.RESTORED

    def test_review_detain(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue="结构松动",
            abnormal_action="待复核"
        )
        submit_inspection_result(db, submit_data)

        review_data = ReviewInspectionTaskRequest(
            task_id=task.id,
            reviewed_by="王五",
            review_result="需留置",
            review_note="需要维修"
        )
        result = review_inspection_task(db, review_data)

        assert result.task_status == InspectionTaskStatus.ABNORMAL_DETAINED
        assert result.is_detained == 1

        stool = db.query(Stool).filter(Stool.id == task.stool_id).first()
        assert stool.status == StoolStatus.DETAINED

    def test_review_reinspect(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue="外观问题",
            abnormal_action="待复核"
        )
        submit_inspection_result(db, submit_data)

        review_data = ReviewInspectionTaskRequest(
            task_id=task.id,
            reviewed_by="王五",
            review_result="再次巡检",
            review_note="描述不清楚，重新检查"
        )
        result = review_inspection_task(db, review_data)

        assert result.task_status == InspectionTaskStatus.IN_PROGRESS
        assert result.inspection_result is None
        assert result.reviewed_at is None

    def test_review_status_conflict(self, db):
        from exceptions import StatusConflictException
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        review_data = ReviewInspectionTaskRequest(
            task_id=task.id,
            reviewed_by="王五",
            review_result="恢复可用"
        )
        with pytest.raises(StatusConflictException):
            review_inspection_task(db, review_data)

    def test_review_invalid_result(self, db):
        from exceptions import ValidationException
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            abnormal_action="待复核"
        )
        submit_inspection_result(db, submit_data)

        review_data = ReviewInspectionTaskRequest(
            task_id=task.id,
            reviewed_by="王五",
            review_result="无效结果"
        )
        with pytest.raises(ValidationException):
            review_inspection_task(db, review_data)


class TestRestoreInspectedStool:
    def test_restore_detained_stool(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue="椅面破损",
            abnormal_action="留置"
        )
        submit_inspection_result(db, submit_data)

        restore_data = RestoreInspectedStoolRequest(
            task_id=task.id,
            restored_by="赵六",
            restore_note="已维修完成"
        )
        result = restore_inspected_stool(db, restore_data)

        assert result.task_status == InspectionTaskStatus.CLOSED

        stool = db.query(Stool).filter(Stool.id == task.stool_id).first()
        assert stool.status == StoolStatus.RESTORED

    def test_restore_status_conflict(self, db):
        from exceptions import StatusConflictException
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        restore_data = RestoreInspectedStoolRequest(
            task_id=task.id,
            restored_by="赵六"
        )
        with pytest.raises(StatusConflictException):
            restore_inspected_stool(db, restore_data)


class TestInspectionTaskDetail:
    def test_get_task_detail_with_logs(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="李四"
        )
        submit_inspection_result(db, submit_data)

        detail = get_inspection_task_detail(db, task.id)
        assert detail is not None
        assert len(detail.logs) > 0

    def test_task_not_found(self, db):
        result = get_inspection_task_detail(db, 999)
        assert result is None


class TestInspectionSummary:
    def test_summary_counts(self, db):
        for i in range(3):
            create_test_stool(db, f"STOOL-00{i+1}")
        generate_inspection_tasks(db)

        tasks = db.query(InspectionTask).all()
        submit_data1 = SubmitInspectionResultRequest(
            task_id=tasks[0].id,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="李四"
        )
        submit_inspection_result(db, submit_data1)

        submit_data2 = SubmitInspectionResultRequest(
            task_id=tasks[1].id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            abnormal_action="待复核"
        )
        submit_inspection_result(db, submit_data2)

        submit_data3 = SubmitInspectionResultRequest(
            task_id=tasks[2].id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            abnormal_action="留置"
        )
        submit_inspection_result(db, submit_data3)

        summary = get_inspection_tasks_summary(db)
        assert summary["total_tasks"] == 3
        assert summary["completed_normal_count"] == 1
        assert summary["abnormal_pending_review_count"] == 1
        assert summary["abnormal_detained_count"] == 1


class TestInspectionTaskLogs:
    def test_logs_generated_for_status_changes(self, db):
        create_test_stool(db, "STOOL-001")
        generate_inspection_tasks(db)

        task = db.query(InspectionTask).first()
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue="问题",
            abnormal_action="待复核"
        )
        submit_inspection_result(db, submit_data)

        review_data = ReviewInspectionTaskRequest(
            task_id=task.id,
            reviewed_by="王五",
            review_result="恢复可用"
        )
        review_inspection_task(db, review_data)

        from crud import get_inspection_task_logs
        logs = get_inspection_task_logs(db, task.id)
        assert len(logs) >= 3


class TestAnalyticsIntegration:
    def test_inspection_alerts_detected(self, db):
        create_test_stool(db, "STOOL-001")
        tasks = generate_inspection_tasks(db)

        task = tasks[0]
        task.scheduled_at = datetime.utcnow() - timedelta(days=10)
        db.commit()

        from analytics import detect_inspection_overdue_alerts
        alerts = detect_inspection_overdue_alerts(db)
        assert len(alerts) > 0

    def test_inspection_pending_review_alerts(self, db):
        create_test_stool(db, "STOOL-001")
        tasks = generate_inspection_tasks(db)

        task = tasks[0]
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            abnormal_action="待复核"
        )
        submit_inspection_result(db, submit_data)

        task = get_inspection_task(db, task.id)
        task.inspected_at = datetime.utcnow() - timedelta(days=2)
        db.commit()

        from analytics import detect_inspection_pending_review_alerts
        alerts = detect_inspection_pending_review_alerts(db)
        assert len(alerts) > 0

    def test_inspection_detained_alerts(self, db):
        create_test_stool(db, "STOOL-001")
        tasks = generate_inspection_tasks(db)

        task = tasks[0]
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            abnormal_action="留置"
        )
        submit_inspection_result(db, submit_data)

        task = get_inspection_task(db, task.id)
        task.detained_at = datetime.utcnow() - timedelta(days=10)
        db.commit()

        from analytics import detect_inspection_detained_alerts
        alerts = detect_inspection_detained_alerts(db)
        assert len(alerts) > 0

    def test_abnormal_areas_includes_inspection(self, db):
        create_test_stool(db, "STOOL-001", area="测试区")
        tasks = generate_inspection_tasks(db)

        task = tasks[0]
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="李四",
            appearance_issue_level="严重",
            abnormal_action="留置"
        )
        submit_inspection_result(db, submit_data)

        from analytics import get_abnormal_areas
        total, areas = get_abnormal_areas(db, min_abnormal_rate=0.0)
        assert total >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
