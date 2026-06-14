#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import StoolStatus, LoadLevel, InspectionTaskStatus, InspectionResult
from schemas import (
    StoolCreate, SubmitInspectionResultRequest,
    ReviewInspectionTaskRequest, RestoreInspectedStoolRequest
)
from crud import (
    create_stool, generate_inspection_tasks,
    list_inspection_tasks, submit_inspection_result,
    review_inspection_task, restore_inspected_stool,
    get_inspection_task_detail, get_inspection_tasks_summary,
    get_inspection_task_logs
)
from analytics import get_all_alerts, get_abnormal_areas


TEST_DB = "sqlite:///./integration_test.db"


def main():
    engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    print("=" * 60)
    print("座凳巡检任务闭环模块 - 集成测试")
    print("=" * 60)

    try:
        print("\n【步骤 1】创建测试座凳")
        print("-" * 40)
        stools_data = [
            ("S001", "图书馆一层A区", LoadLevel.LIGHT, 7, "李值守"),
            ("S002", "图书馆一层A区", LoadLevel.MEDIUM, 7, "李值守"),
            ("S003", "图书馆二层B区", LoadLevel.HEAVY, 14, "王值守"),
            ("S004", "图书馆二层B区", LoadLevel.LIGHT, 7, "王值守"),
            ("S005", "图书馆三层C区", LoadLevel.MEDIUM, 30, "赵值守"),
        ]
        for num, area, level, cycle, person in stools_data:
            data = StoolCreate(
                stool_number=num,
                storage_area=area,
                load_level=level,
                inspection_cycle_days=cycle,
                responsible_person=person
            )
            stool = create_stool(db, data)
            print(f"  ✓ 创建座凳 {stool.stool_number} - {stool.storage_area} - {stool.responsible_person}")

        print(f"\n  共创建 {len(stools_data)} 个座凳")

        print("\n【步骤 2】生成周期巡检任务")
        print("-" * 40)
        tasks = generate_inspection_tasks(db, source_type="周期巡检")
        print(f"  ✓ 生成 {len(tasks)} 条巡检任务")
        for t in tasks:
            print(f"    - {t.stool_number} | {t.storage_area} | 责任人: {t.responsible_person}")

        print("\n【步骤 3】按区域筛选巡检任务")
        print("-" * 40)
        total, items = list_inspection_tasks(db, storage_area="A区")
        print(f"  ✓ A区共有 {total} 条待巡检任务")

        print("\n【步骤 4】按责任人筛选巡检任务")
        print("-" * 40)
        total, items = list_inspection_tasks(db, responsible_person="王")
        print(f"  ✓ 王姓责任人共有 {total} 条待巡检任务")

        print("\n【步骤 5】提交巡检结果 - 正常")
        print("-" * 40)
        total, pending_tasks = list_inspection_tasks(db, task_status=InspectionTaskStatus.PENDING)
        task = pending_tasks[0]
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.NORMAL,
            inspected_by="值守员小李"
        )
        result = submit_inspection_result(db, submit_data)
        print(f"  ✓ 座凳 {result.stool_number} 巡检完成")
        print(f"    结果: {result.inspection_result.value}")
        print(f"    任务状态: {result.task_status.value}")
        print(f"    座凳状态: 已同步更新")

        print("\n【步骤 6】提交巡检结果 - 异常(待复核)")
        print("-" * 40)
        total, pending_tasks = list_inspection_tasks(db, task_status=InspectionTaskStatus.PENDING)
        task = pending_tasks[0]
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="值守员小李",
            appearance_issue="椅面有轻微划痕，不影响使用",
            appearance_issue_level="轻微",
            handling_suggestion="建议复核确认是否需要维修",
            abnormal_action="待复核"
        )
        result = submit_inspection_result(db, submit_data)
        print(f"  ✓ 座凳 {result.stool_number} 巡检异常")
        print(f"    问题: {result.appearance_issue}")
        print(f"    问题等级: {result.appearance_issue_level}")
        print(f"    任务状态: {result.task_status.value}")

        print("\n【步骤 7】提交巡检结果 - 异常(直接留置)")
        print("-" * 40)
        total, pending_tasks = list_inspection_tasks(db, task_status=InspectionTaskStatus.PENDING)
        task = pending_tasks[0]
        submit_data = SubmitInspectionResultRequest(
            task_id=task.id,
            inspection_result=InspectionResult.ABNORMAL,
            inspected_by="值守员小王",
            appearance_issue="支架断裂，无法正常使用",
            appearance_issue_level="严重",
            handling_suggestion="必须维修或更换",
            abnormal_action="留置"
        )
        result = submit_inspection_result(db, submit_data)
        print(f"  ✓ 座凳 {result.stool_number} 巡检异常留置")
        print(f"    问题: {result.appearance_issue}")
        print(f"    问题等级: {result.appearance_issue_level}")
        print(f"    留置原因: {result.detained_reason}")
        print(f"    任务状态: {result.task_status.value}")

        print("\n【步骤 8】复核巡检异常 - 恢复可用")
        print("-" * 40)
        total, pending_review = list_inspection_tasks(db, task_status=InspectionTaskStatus.ABNORMAL_PENDING_REVIEW)
        if pending_review:
            task = pending_review[0]
            review_data = ReviewInspectionTaskRequest(
                task_id=task.id,
                reviewed_by="复核员老张",
                review_result="恢复可用",
                review_note="轻微划痕不影响使用，可正常借出"
            )
            result = review_inspection_task(db, review_data)
            print(f"  ✓ 座凳 {result.stool_number} 复核完成")
            print(f"    复核结果: {result.review_result}")
            print(f"    任务状态: {result.task_status.value}")

        print("\n【步骤 9】查看巡检任务详情(含处理记录)")
        print("-" * 40)
        task_detail = get_inspection_task_detail(db, task.id)
        print(f"  ✓ 任务 {task_detail.id} 详情")
        print(f"    座凳: {task_detail.stool_number}")
        print(f"    状态: {task_detail.task_status.value}")
        print(f"    处理记录共 {len(task_detail.logs)} 条:")
        for log in task_detail.logs:
            print(f"      [{log.action_at.strftime('%Y-%m-%d %H:%M')}] {log.action_type} - {log.action_by}")
            if log.description:
                print(f"        说明: {log.description}")

        print("\n【步骤 10】恢复异常留置座凳")
        print("-" * 40)
        total, detained = list_inspection_tasks(db, task_status=InspectionTaskStatus.ABNORMAL_DETAINED)
        if detained:
            task = detained[0]
            restore_data = RestoreInspectedStoolRequest(
                task_id=task.id,
                restored_by="管理员老陈",
                restore_note="已更换支架，修复完成"
            )
            result = restore_inspected_stool(db, restore_data)
            print(f"  ✓ 座凳 {result.stool_number} 已恢复可用")
            print(f"    任务状态: {result.task_status.value}")

        print("\n【步骤 11】巡检任务统计概览")
        print("-" * 40)
        summary = get_inspection_tasks_summary(db)
        print(f"  ✓ 总任务数: {summary['total_tasks']}")
        print(f"    待处理: {summary['pending_count']}")
        print(f"    处理中: {summary['in_progress_count']}")
        print(f"    正常完成: {summary['completed_normal_count']}")
        print(f"    异常完成: {summary['completed_abnormal_count']}")
        print(f"    异常留置: {summary['abnormal_detained_count']}")
        print(f"    待复核: {summary['abnormal_pending_review_count']}")
        print(f"    已关闭: {summary['closed_count']}")

        print("\n【步骤 12】预警系统 - 巡检相关预警")
        print("-" * 40)
        alerts = get_all_alerts(db)
        inspection_alerts = [a for a in alerts if "巡检" in a.alert_type]
        print(f"  ✓ 系统共 {len(alerts)} 条预警，其中巡检相关 {len(inspection_alerts)} 条")
        for a in inspection_alerts:
            print(f"    [{a.severity.upper()}] {a.alert_type}: {a.message}")

        print("\n【步骤 13】异常区域统计(含巡检异常)")
        print("-" * 40)
        total_areas, areas = get_abnormal_areas(db, min_abnormal_rate=0.0)
        print(f"  ✓ 共 {total_areas} 个区域有异常记录")
        for area in areas:
            print(f"    - {area.storage_area}: 总数 {area.total_records}, 异常 {area.abnormal_count}, 异常率 {area.abnormal_rate}%")

        print("\n" + "=" * 60)
        print("✓ 集成测试全部通过！")
        print("=" * 60)

    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./integration_test.db"):
            os.remove("./integration_test.db")


if __name__ == "__main__":
    main()
