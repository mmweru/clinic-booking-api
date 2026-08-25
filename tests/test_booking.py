import pytest
from datetime import datetime, timedelta

from app.services.booking_service import BookingService
from app.schemas import AppointmentCreate, AppointmentReschedule
from app.core.exceptions import NotFoundError, ValidationError, ConflictError


def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    return (datetime.now() + timedelta(days=1)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


@pytest.mark.asyncio
async def test_get_available_slots(db_session, test_data):
    """9am-5pm working hours = 8 hours = 16 slots of 30 min, all free."""
    service = BookingService(db_session)
    future_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    slots = await service.get_available_slots(test_data["doctor"].id, future_date)

    assert len(slots) == 16
    assert all(isinstance(slot, datetime) for slot in slots)


@pytest.mark.asyncio
async def test_get_available_slots_doctor_not_found(db_session, test_data):
    service = BookingService(db_session)
    future_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    with pytest.raises(NotFoundError):
        await service.get_available_slots(999, future_date)


@pytest.mark.asyncio
async def test_book_appointment(db_session, test_data):
    service = BookingService(db_session)
    appointment_time = _tomorrow_at(10)

    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=appointment_time,
    )

    appointment = await service.book_appointment(appointment_data)

    assert appointment.doctor_id == test_data["doctor"].id
    assert appointment.patient_id == test_data["patient"].id
    assert appointment.start_time == appointment_time
    assert appointment.status == "booked"


@pytest.mark.asyncio
async def test_booking_conflict(db_session, test_data):
    """Booking the same slot twice must fail with a conflict, not a 500."""
    service = BookingService(db_session)
    appointment_time = _tomorrow_at(10)

    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=appointment_time,
    )

    await service.book_appointment(appointment_data)

    with pytest.raises(ConflictError):
        await service.book_appointment(appointment_data)


@pytest.mark.asyncio
async def test_booking_outside_working_hours(db_session, test_data):
    service = BookingService(db_session)
    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=_tomorrow_at(20),  
    )

    with pytest.raises(ValidationError):
        await service.book_appointment(appointment_data)


@pytest.mark.asyncio
async def test_booking_within_one_hour_rejected(db_session, test_data):
    service = BookingService(db_session)
    near_future = datetime.now() + timedelta(minutes=30)
    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=near_future,
    )

    with pytest.raises(ValidationError):
        await service.book_appointment(appointment_data)


@pytest.mark.asyncio
async def test_cancel_appointment(db_session, test_data):
    service = BookingService(db_session)
    appointment_time = _tomorrow_at(10)

    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=appointment_time,
    )
    appointment = await service.book_appointment(appointment_data)

    cancelled = await service.cancel_appointment(appointment.id, "Patient changed mind")

    assert cancelled.status == "cancelled"
    assert cancelled.cancellation_reason == "Patient changed mind"


@pytest.mark.asyncio
async def test_cancel_then_rebook_same_slot(db_session, test_data):
    """
    Regression test for the original bug: a raw UniqueConstraint on
    (doctor_id, start_time) with no status filter meant a cancelled slot
    could never be rebooked -- the INSERT would hit the constraint left
    behind by the cancelled row. This must now succeed.
    """
    service = BookingService(db_session)
    appointment_time = _tomorrow_at(10)

    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=appointment_time,
    )
    first = await service.book_appointment(appointment_data)
    await service.cancel_appointment(first.id, "Patient changed mind")

    # Same slot, should be bookable again now that it's cancelled.
    rebooked = await service.book_appointment(appointment_data)

    assert rebooked.status == "booked"
    assert rebooked.start_time == appointment_time

    date_str = appointment_time.strftime("%Y-%m-%d")
    available = await service.get_available_slots(test_data["doctor"].id, date_str)
    assert appointment_time not in available  # it's booked again, not free


@pytest.mark.asyncio
async def test_cancel_already_cancelled_fails(db_session, test_data):
    service = BookingService(db_session)
    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=_tomorrow_at(10),
    )
    appointment = await service.book_appointment(appointment_data)
    await service.cancel_appointment(appointment.id, "first cancel")

    with pytest.raises(ValidationError):
        await service.cancel_appointment(appointment.id, "second cancel")


@pytest.mark.asyncio
async def test_reschedule_appointment(db_session, test_data):
    service = BookingService(db_session)
    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=_tomorrow_at(10),
    )
    appointment = await service.book_appointment(appointment_data)

    new_time = _tomorrow_at(14)
    updated = await service.reschedule_appointment(
        appointment.id, AppointmentReschedule(new_start_time=new_time)
    )

    assert updated.start_time == new_time

    # Old slot should be free again.
    date_str = new_time.strftime("%Y-%m-%d")
    available = await service.get_available_slots(test_data["doctor"].id, date_str)
    assert _tomorrow_at(10) in available


@pytest.mark.asyncio
async def test_reschedule_applies_same_rules_as_fresh_booking(db_session, test_data):
    """
    Regression test: the original reschedule path didn't enforce the
    1-hour advance-notice rule the way book_appointment did.
    """
    service = BookingService(db_session)
    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=_tomorrow_at(10),
    )
    appointment = await service.book_appointment(appointment_data)

    too_soon = datetime.now() + timedelta(minutes=15)
    with pytest.raises(ValidationError):
        await service.reschedule_appointment(
            appointment.id, AppointmentReschedule(new_start_time=too_soon)
        )


@pytest.mark.asyncio
async def test_reschedule_cancelled_appointment_fails(db_session, test_data):
    service = BookingService(db_session)
    appointment_data = AppointmentCreate(
        doctor_id=test_data["doctor"].id,
        patient_id=test_data["patient"].id,
        start_time=_tomorrow_at(10),
    )
    appointment = await service.book_appointment(appointment_data)
    await service.cancel_appointment(appointment.id, "no longer needed")

    with pytest.raises(ValidationError):
        await service.reschedule_appointment(
            appointment.id, AppointmentReschedule(new_start_time=_tomorrow_at(14))
        )
