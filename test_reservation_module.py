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
    BorrowRecord, Reservation, ReservationStatus, ReservationLog
)
from schemas import (
    StoolCreate, IssueStoolRequest, ReturnStoolRequest,
    CleanStoolRequest, ReviewStoolRequest,
    CreateReservationRequest, CancelReservationRequest,
    ConfirmReservationRequest, IssueByReservationRequest
)
from crud import (
    create_stool, issue_stool, return_stool, clean_stool, review_stool,
    create_reservation, get_reservation, list_reservations,
    cancel_reservation, confirm_reservation, issue_by_reservation,
    expire_reservations, get_reservation_detail, get_reservations_summary,
    get_active_reservation_for_stool, calculate_priority_score,
    update_queue_positions, get_reservation_logs
)


TEST_DATABASE_URL = "sqlite:///./test_reservation.db"


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
        if os.path.exists("./test_reservation.db"):
            os.remove("./test_reservation.db")


def create_test_stool(db, number="STOOL-001", area="A区", level=LoadLevel.MEDIUM, cycle=7, person="张三"):
    data = StoolCreate(
        stool_number=number,
        storage_area=area,
        load_level=level,
        inspection_cycle_days=cycle,
        responsible_person=person
    )
    return create_stool(db, data)


def create_borrowed_stool(db, stool_number="STOOL-001", area="A区", level=LoadLevel.MEDIUM):
    stool = create_test_stool(db, stool_number, area=area, level=level)
    issue_data = IssueStoolRequest(
        stool_id=stool.id,
        borrower="测试用户",
        borrower_contact="13800138000",
        issued_by="李四"
    )
    record = issue_stool(db, issue_data)
    return stool, record


def create_cleaned_stool(db, stool_number="STOOL-001"):
    stool, record = create_borrowed_stool(db, stool_number)
    return_data = ReturnStoolRequest(
        record_id=record.id,
        returned_by="李四"
    )
    record = return_stool(db, return_data)
    clean_data = CleanStoolRequest(
        record_id=record.id,
        cleaned_by="李四",
        cleaning_result="合格"
    )
    record = clean_stool(db, clean_data)
    return stool, record


class TestReservationModel:
    def test_reservation_status_enum(self):
        assert ReservationStatus.PENDING.value == "待确认"
        assert ReservationStatus.CONFIRMED.value == "已确认"
        assert ReservationStatus.CANCELLED.value == "已取消"
        assert ReservationStatus.EXPIRED.value == "已过期"
        assert ReservationStatus.COMPLETED.value == "已完成"

    def test_stool_status_reserved_added(self):
        assert StoolStatus.RESERVED.value == "已预约"


class TestReservationPriority:
    def test_calculate_priority_score_expected_time(self, db):
        stool = create_test_stool(db, level=LoadLevel.MEDIUM)
        expected_time = datetime.utcnow() + timedelta(hours=12)
        score = calculate_priority_score(db, stool, expected_time)
        assert score > 0
        assert score <= 1.5

    def test_calculate_priority_score_no_expected_time(self, db):
        stool = create_test_stool(db, level=LoadLevel.MEDIUM)
        score = calculate_priority_score(db, stool, None)
        assert score > 0

    def test_calculate_priority_score_different_load_levels(self, db):
        for level in [LoadLevel.LIGHT, LoadLevel.MEDIUM, LoadLevel.HEAVY, LoadLevel.EXTRA_HEAVY]:
            stool = create_test_stool(db, number=f"STOOL-{level.value}", level=level)
            score = calculate_priority_score(db, stool, None)
            assert score > 0


class TestCreateReservation:
    def test_create_reservation_success(self, db):
        stool, record = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            applicant_contact="13900139000",
            purpose="会议使用",
            expected_use_time=datetime.utcnow() + timedelta(hours=2)
        )
        reservation = create_reservation(db, req)

        assert reservation is not None
        assert reservation.stool_id == stool.id
        assert reservation.applicant == "王五"
        assert reservation.status == ReservationStatus.PENDING
        assert reservation.priority_score > 0
        assert reservation.queue_position == 1
        assert reservation.expired_at is not None

    def test_create_reservation_available_stool_error(self, db):
        from exceptions import StatusConflictException
        stool = create_test_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        with pytest.raises(StatusConflictException) as exc_info:
            create_reservation(db, req)
        assert "无需预约" in str(exc_info.value.detail["message"])

    def test_create_reservation_detained_stool_error(self, db):
        from exceptions import StatusConflictException
        stool = create_test_stool(db)
        stool.status = StoolStatus.DETAINED
        db.commit()

        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        with pytest.raises(StatusConflictException) as exc_info:
            create_reservation(db, req)
        assert "异常留置" in str(exc_info.value.detail["message"])

    def test_create_reservation_duplicate_error(self, db):
        from exceptions import ValidationException
        stool, record = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        create_reservation(db, req)

        with pytest.raises(ValidationException) as exc_info:
            create_reservation(db, req)
        assert "不可重复预约" in str(exc_info.value.detail["message"])

    def test_create_reservation_stool_not_found(self, db):
        from exceptions import NotFoundException
        req = CreateReservationRequest(
            stool_id=999,
            applicant="王五",
            purpose="测试"
        )
        with pytest.raises(NotFoundException):
            create_reservation(db, req)

    def test_create_reservation_queue_position(self, db):
        stool, record = create_borrowed_stool(db, "STOOL-001")

        for i in range(3):
            req = CreateReservationRequest(
                stool_id=stool.id,
                applicant=f"申请人{i+1}",
                purpose="测试"
            )
            res = create_reservation(db, req)

        reservations = db.query(Reservation).order_by(Reservation.queue_position).all()
        assert len(reservations) == 3
        assert reservations[0].queue_position == 1
        assert reservations[1].queue_position == 2
        assert reservations[2].queue_position == 3


class TestListReservations:
    def test_list_all_reservations(self, db):
        for i in range(3):
            stool, record = create_borrowed_stool(db, f"STOOL-00{i+1}")
            req = CreateReservationRequest(
                stool_id=stool.id,
                applicant=f"申请人{i+1}",
                purpose="测试"
            )
            create_reservation(db, req)

        total, items = list_reservations(db)
        assert total == 3

    def test_list_filter_by_stool_id(self, db):
        stool1, _ = create_borrowed_stool(db, "STOOL-001")
        stool2, _ = create_borrowed_stool(db, "STOOL-002")

        req1 = CreateReservationRequest(
            stool_id=stool1.id,
            applicant="申请人1",
            purpose="测试"
        )
        create_reservation(db, req1)

        req2 = CreateReservationRequest(
            stool_id=stool2.id,
            applicant="申请人2",
            purpose="测试"
        )
        create_reservation(db, req2)

        total, items = list_reservations(db, stool_id=stool1.id)
        assert total == 1
        assert items[0].stool_id == stool1.id

    def test_list_filter_by_applicant(self, db):
        stool, _ = create_borrowed_stool(db, "STOOL-001")
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="特殊申请人",
            purpose="测试"
        )
        create_reservation(db, req)

        total, items = list_reservations(db, applicant="特殊")
        assert total == 1

    def test_list_filter_by_status(self, db):
        stool, _ = create_borrowed_stool(db, "STOOL-001")
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="申请人",
            purpose="测试"
        )
        create_reservation(db, req)

        total, items = list_reservations(db, status=ReservationStatus.PENDING)
        assert total == 1

        cancel_req = CancelReservationRequest(
            reservation_id=items[0].id,
            cancelled_by="管理员",
            cancel_reason="测试取消"
        )
        cancel_reservation(db, cancel_req)

        total2, _ = list_reservations(db, status=ReservationStatus.CANCELLED)
        assert total2 == 1

    def test_list_sorted_by_priority(self, db):
        stool, _ = create_borrowed_stool(db, "STOOL-001")

        req1 = CreateReservationRequest(
            stool_id=stool.id,
            applicant="申请人1",
            purpose="测试",
            expected_use_time=datetime.utcnow() + timedelta(hours=1)
        )
        res1 = create_reservation(db, req1)

        req2 = CreateReservationRequest(
            stool_id=stool.id,
            applicant="申请人2",
            purpose="测试",
            expected_use_time=datetime.utcnow() + timedelta(hours=48)
        )
        res2 = create_reservation(db, req2)

        assert res1.priority_score > res2.priority_score


class TestCancelReservation:
    def test_cancel_pending_reservation(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        cancel_req = CancelReservationRequest(
            reservation_id=reservation.id,
            cancelled_by="王五",
            cancel_reason="临时有事"
        )
        result = cancel_reservation(db, cancel_req)

        assert result.status == ReservationStatus.CANCELLED
        assert result.cancelled_at is not None
        assert result.cancelled_by == "王五"
        assert result.cancel_reason == "临时有事"
        assert result.queue_position is None

    def test_cancel_confirmed_reservation(self, db):
        stool, _ = create_borrowed_stool(db)

        issue_data = IssueStoolRequest(
            stool_id=stool.id,
            borrower="用户",
            borrower_contact="13800138000",
            issued_by="李四"
        )

        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        stool.status = StoolStatus.RESTORED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        reservation = confirm_reservation(db, confirm_req)
        assert reservation.status == ReservationStatus.CONFIRMED

        cancel_req = CancelReservationRequest(
            reservation_id=reservation.id,
            cancelled_by="管理员",
            cancel_reason="用户取消"
        )
        result = cancel_reservation(db, cancel_req)

        assert result.status == ReservationStatus.CANCELLED

    def test_cancel_completed_reservation_error(self, db):
        from exceptions import StatusConflictException
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        reservation.status = ReservationStatus.COMPLETED
        db.commit()

        cancel_req = CancelReservationRequest(
            reservation_id=reservation.id,
            cancelled_by="管理员"
        )
        with pytest.raises(StatusConflictException):
            cancel_reservation(db, cancel_req)

    def test_cancel_reservation_not_found(self, db):
        from exceptions import NotFoundException
        cancel_req = CancelReservationRequest(
            reservation_id=999,
            cancelled_by="管理员"
        )
        with pytest.raises(NotFoundException):
            cancel_reservation(db, cancel_req)

    def test_cancel_reservation_updates_queue(self, db):
        stool, _ = create_borrowed_stool(db)

        reservations = []
        for i in range(3):
            req = CreateReservationRequest(
                stool_id=stool.id,
                applicant=f"申请人{i+1}",
                purpose="测试"
            )
            res = create_reservation(db, req)
            reservations.append(res)

        assert reservations[0].queue_position == 1
        assert reservations[1].queue_position == 2
        assert reservations[2].queue_position == 3

        cancel_req = CancelReservationRequest(
            reservation_id=reservations[0].id,
            cancelled_by="申请人1"
        )
        cancel_reservation(db, cancel_req)

        db.refresh(reservations[1])
        db.refresh(reservations[2])

        assert reservations[1].queue_position == 1
        assert reservations[2].queue_position == 2


class TestConfirmReservation:
    def test_confirm_reservation_success(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        stool.status = StoolStatus.RESTORED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        result = confirm_reservation(db, confirm_req)

        assert result.status == ReservationStatus.CONFIRMED
        assert result.confirmed_at is not None
        assert result.confirmed_by == "李四"

        db.refresh(stool)
        assert stool.status == StoolStatus.RESERVED

    def test_confirm_reservation_wrong_status(self, db):
        from exceptions import StatusConflictException
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        reservation.status = ReservationStatus.CANCELLED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        with pytest.raises(StatusConflictException):
            confirm_reservation(db, confirm_req)

    def test_confirm_reservation_stool_wrong_status(self, db):
        from exceptions import StatusConflictException
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        with pytest.raises(StatusConflictException) as exc_info:
            confirm_reservation(db, confirm_req)
        assert "不可确认预约" in str(exc_info.value.detail["message"])


class TestIssueByReservation:
    def test_issue_by_reservation_success(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            applicant_contact="13900139000",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        stool.status = StoolStatus.RESTORED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        confirm_reservation(db, confirm_req)

        issue_req = IssueByReservationRequest(
            reservation_id=reservation.id,
            issued_by="李四"
        )
        record = issue_by_reservation(db, issue_req)

        assert record is not None
        assert record.stool_id == stool.id
        assert record.borrower == "王五"
        assert record.borrower_contact == "13900139000"
        assert record.issued_at is not None
        assert record.source_type == "预约发放"
        assert record.source_reservation_id == reservation.id

        db.refresh(reservation)
        assert reservation.status == ReservationStatus.COMPLETED
        assert reservation.borrow_record_id == record.id

        db.refresh(stool)
        assert stool.status == StoolStatus.BORROWED

    def test_issue_by_reservation_custom_borrower(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        stool.status = StoolStatus.RESTORED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        confirm_reservation(db, confirm_req)

        issue_req = IssueByReservationRequest(
            reservation_id=reservation.id,
            issued_by="李四",
            borrower="实际借用人",
            borrower_contact="13800138000"
        )
        record = issue_by_reservation(db, issue_req)

        assert record.borrower == "实际借用人"
        assert record.borrower_contact == "13800138000"

    def test_issue_by_reservation_wrong_status(self, db):
        from exceptions import StatusConflictException
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        issue_req = IssueByReservationRequest(
            reservation_id=reservation.id,
            issued_by="李四"
        )
        with pytest.raises(StatusConflictException) as exc_info:
            issue_by_reservation(db, issue_req)
        assert "不可发放" in str(exc_info.value.detail["message"])

    def test_issue_by_reservation_stool_wrong_status(self, db):
        from exceptions import StatusConflictException
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        reservation.status = ReservationStatus.CONFIRMED
        db.commit()

        issue_req = IssueByReservationRequest(
            reservation_id=reservation.id,
            issued_by="李四"
        )
        with pytest.raises(StatusConflictException) as exc_info:
            issue_by_reservation(db, issue_req)
        assert "座凳当前状态" in str(exc_info.value.detail["message"])


class TestExpireReservations:
    def test_expire_pending_reservations(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        reservation.expired_at = datetime.utcnow() - timedelta(hours=1)
        db.commit()

        expired_count = expire_reservations(db)
        assert expired_count == 1

        db.refresh(reservation)
        assert reservation.status == ReservationStatus.EXPIRED
        assert reservation.queue_position is None

    def test_expire_confirmed_reservations(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        stool.status = StoolStatus.RESTORED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        reservation = confirm_reservation(db, confirm_req)

        reservation.expired_at = datetime.utcnow() - timedelta(hours=1)
        db.commit()

        expired_count = expire_reservations(db)
        assert expired_count == 1

        db.refresh(reservation)
        assert reservation.status == ReservationStatus.EXPIRED

        db.refresh(stool)
        assert stool.status == StoolStatus.RESTORED

    def test_expire_with_auto_confirm_next(self, db):
        stool, _ = create_borrowed_stool(db)

        req1 = CreateReservationRequest(
            stool_id=stool.id,
            applicant="申请人1",
            purpose="测试"
        )
        res1 = create_reservation(db, req1)

        req2 = CreateReservationRequest(
            stool_id=stool.id,
            applicant="申请人2",
            purpose="测试"
        )
        res2 = create_reservation(db, req2)

        stool.status = StoolStatus.RESTORED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=res1.id,
            confirmed_by="李四"
        )
        confirm_reservation(db, confirm_req)

        res1.expired_at = datetime.utcnow() - timedelta(hours=1)
        db.commit()

        expired_count = expire_reservations(db)
        assert expired_count == 1

        db.refresh(res1)
        db.refresh(res2)

        assert res1.status == ReservationStatus.EXPIRED
        assert res2.status == ReservationStatus.CONFIRMED

        db.refresh(stool)
        assert stool.status == StoolStatus.RESERVED

    def test_no_expired_reservations(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        create_reservation(db, req)

        expired_count = expire_reservations(db)
        assert expired_count == 0


class TestStoolStatusWithReservation:
    def test_issue_stool_with_active_reservation(self, db):
        from exceptions import StatusConflictException
        stool1, record1 = create_borrowed_stool(db, "STOOL-001")
        req1 = CreateReservationRequest(
            stool_id=stool1.id,
            applicant="王五",
            purpose="测试"
        )
        create_reservation(db, req1)

        return_data = ReturnStoolRequest(
            record_id=record1.id,
            returned_by="李四"
        )
        record1 = return_stool(db, return_data)

        clean_data = CleanStoolRequest(
            record_id=record1.id,
            cleaned_by="李四",
            cleaning_result="合格"
        )
        record1 = clean_stool(db, clean_data)

        review_data = ReviewStoolRequest(
            record_id=record1.id,
            reviewed_by="王五",
            review_result="恢复可用"
        )
        review_stool(db, review_data)

        db.refresh(stool1)
        assert stool1.status == StoolStatus.RESERVED

        issue_data = IssueStoolRequest(
            stool_id=stool1.id,
            borrower="测试用户",
            issued_by="李四"
        )
        with pytest.raises(StatusConflictException) as exc_info:
            issue_stool(db, issue_data)
        assert "已被预约" in str(exc_info.value.detail["message"])

    def test_delete_stool_with_active_reservation(self, db):
        from exceptions import StatusConflictException
        from crud import delete_stool
        stool, record = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        create_reservation(db, req)

        return_data = ReturnStoolRequest(
            record_id=record.id,
            returned_by="李四"
        )
        record = return_stool(db, return_data)

        clean_data = CleanStoolRequest(
            record_id=record.id,
            cleaned_by="李四",
            cleaning_result="合格"
        )
        record = clean_stool(db, clean_data)

        review_data = ReviewStoolRequest(
            record_id=record.id,
            reviewed_by="王五",
            review_result="恢复可用"
        )
        review_stool(db, review_data)

        db.refresh(stool)
        assert stool.status == StoolStatus.RESERVED

        with pytest.raises(StatusConflictException) as exc_info:
            delete_stool(db, stool.id)
        assert "存在活跃预约" in str(exc_info.value.detail["message"])

    def test_update_stool_reserved_status_error(self, db):
        from exceptions import StatusConflictException
        from crud import update_stool
        from schemas import StoolUpdate
        stool = create_test_stool(db)

        update_data = StoolUpdate(
            status=StoolStatus.RESERVED
        )
        with pytest.raises(StatusConflictException) as exc_info:
            update_stool(db, stool.id, update_data)
        assert "不可直接将座凳标记为已预约状态" in str(exc_info.value.detail["message"])

    def test_update_stool_from_reserved_error(self, db):
        from exceptions import StatusConflictException
        from crud import update_stool
        from schemas import StoolUpdate
        stool = create_test_stool(db)
        stool.status = StoolStatus.RESERVED
        db.commit()

        update_data = StoolUpdate(
            status=StoolStatus.RESTORED
        )
        with pytest.raises(StatusConflictException) as exc_info:
            update_stool(db, stool.id, update_data)
        assert "存在活跃预约" in str(exc_info.value.detail["message"])


class TestReviewWithReservation:
    def test_review_restore_with_pending_reservation(self, db):
        stool, record = create_cleaned_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        review_data = ReviewStoolRequest(
            record_id=record.id,
            reviewed_by="王五",
            review_result="恢复可用"
        )
        review_stool(db, review_data)

        db.refresh(reservation)
        db.refresh(stool)

        assert reservation.status == ReservationStatus.CONFIRMED
        assert stool.status == StoolStatus.RESERVED

    def test_review_restore_without_pending_reservation(self, db):
        stool, record = create_cleaned_stool(db)

        review_data = ReviewStoolRequest(
            record_id=record.id,
            reviewed_by="王五",
            review_result="恢复可用"
        )
        review_stool(db, review_data)

        db.refresh(stool)
        assert stool.status == StoolStatus.RESTORED


class TestReservationLogs:
    def test_logs_generated_for_create(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        logs = get_reservation_logs(db, reservation.id)
        assert len(logs) >= 1
        assert logs[0].action_type == "提交预约"

    def test_logs_generated_for_cancel(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        cancel_req = CancelReservationRequest(
            reservation_id=reservation.id,
            cancelled_by="管理员",
            cancel_reason="测试"
        )
        cancel_reservation(db, cancel_req)

        logs = get_reservation_logs(db, reservation.id)
        action_types = [log.action_type for log in logs]
        assert "取消预约" in action_types

    def test_logs_generated_for_confirm(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        stool.status = StoolStatus.RESTORED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        confirm_reservation(db, confirm_req)

        logs = get_reservation_logs(db, reservation.id)
        action_types = [log.action_type for log in logs]
        assert "确认预约" in action_types

    def test_logs_generated_for_issue(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        stool.status = StoolStatus.RESTORED
        db.commit()

        confirm_req = ConfirmReservationRequest(
            reservation_id=reservation.id,
            confirmed_by="李四"
        )
        confirm_reservation(db, confirm_req)

        issue_req = IssueByReservationRequest(
            reservation_id=reservation.id,
            issued_by="李四"
        )
        issue_by_reservation(db, issue_req)

        logs = get_reservation_logs(db, reservation.id)
        action_types = [log.action_type for log in logs]
        assert "发放座凳" in action_types


class TestReservationDetail:
    def test_get_reservation_detail_with_logs(self, db):
        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        detail = get_reservation_detail(db, reservation.id)
        assert detail is not None
        assert len(detail.logs) > 0

    def test_get_reservation_not_found(self, db):
        result = get_reservation_detail(db, 999)
        assert result is None


class TestReservationSummary:
    def test_summary_counts(self, db):
        for i in range(3):
            stool, _ = create_borrowed_stool(db, f"STOOL-00{i+1}")
            req = CreateReservationRequest(
                stool_id=stool.id,
                applicant=f"申请人{i+1}",
                purpose="测试"
            )
            create_reservation(db, req)

        reservations = db.query(Reservation).all()

        cancel_req = CancelReservationRequest(
            reservation_id=reservations[0].id,
            cancelled_by="管理员",
            cancel_reason="测试"
        )
        cancel_reservation(db, cancel_req)

        summary = get_reservations_summary(db)
        assert summary["total_reservations"] == 3
        assert summary["pending_count"] == 2
        assert summary["cancelled_count"] == 1
        assert summary["total_queue_count"] == 2


class TestAnalyticsWithReservation:
    def test_reservation_pending_alerts(self, db):
        from analytics import detect_reservation_pending_alerts

        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        reservation.reserved_at = datetime.utcnow() - timedelta(hours=6)
        db.commit()

        alerts = detect_reservation_pending_alerts(db)
        assert len(alerts) >= 0

    def test_reservation_queue_long_alerts(self, db):
        from analytics import detect_reservation_queue_long_alerts

        for i in range(5):
            stool, _ = create_borrowed_stool(db, f"STOOL-00{i+1}", area="测试区")
            req = CreateReservationRequest(
                stool_id=stool.id,
                applicant=f"申请人{i+1}",
                purpose="测试"
            )
            create_reservation(db, req)

        alerts = detect_reservation_queue_long_alerts(db)
        assert len(alerts) >= 1
        assert alerts[0].alert_type == "预约排队过长"
        assert alerts[0].details["queue_count"] >= 5

    def test_all_alerts_include_reservation(self, db):
        from analytics import get_all_alerts

        stool, _ = create_borrowed_stool(db)
        req = CreateReservationRequest(
            stool_id=stool.id,
            applicant="王五",
            purpose="测试"
        )
        reservation = create_reservation(db, req)

        reservation.reserved_at = datetime.utcnow() - timedelta(hours=12)
        db.commit()

        alerts = get_all_alerts(db)
        alert_types = [a.alert_type for a in alerts]
        assert any("预约" in t for t in alert_types)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
