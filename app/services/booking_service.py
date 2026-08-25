from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from typing import List, Optional
import logging

from app.models.models import Doctor, Appointment, AppointmentStatus, Patient
from app.schemas.schemas import AppointmentCreate, AppointmentReschedule
from app.core.exceptions import NotFoundError, ValidationError, ConflictError

logger = logging.getLogger(__name__)

MIN_BOOKING_NOTICE = timedelta(hours=1)
SLOT_DURATION = timedelta(minutes=30)


class BookingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    async def get_available_slots(
        self,
        doctor_id: int,
        date: str,
        min_hours_notice: int = 0,
    ) -> List[datetime]:
        """Get all available 30-minute slots for a doctor on a given date."""
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(f"Invalid date format: '{date}'. Expected YYYY-MM-DD.")

        doctor = await self._get_doctor_or_raise(doctor_id)

        start_of_day = datetime.combine(target_date, doctor.working_hours_start)
        end_of_day = datetime.combine(target_date, doctor.working_hours_end)

        all_slots = []
        current = start_of_day
        while current < end_of_day:
            all_slots.append(current)
            current += SLOT_DURATION

        booked_result = await self.db.execute(
            select(Appointment.start_time).where(
                and_(
                    Appointment.doctor_id == doctor_id,
                    Appointment.start_time >= start_of_day,
                    Appointment.start_time < end_of_day,
                    Appointment.status == AppointmentStatus.BOOKED,
                )
            )
        )
        booked_slots = {slot[0] for slot in booked_result.all()}

        now = datetime.now()
        notice_delta = timedelta(hours=min_hours_notice) if min_hours_notice > 0 else timedelta(0)

        return [
            slot
            for slot in all_slots
            if slot not in booked_slots
            and slot > now
            and (slot - now) >= notice_delta
        ]

    # ------------------------------------------------------------------
    # Booking
    # ------------------------------------------------------------------
    async def book_appointment(self, appointment_data: AppointmentCreate) -> Appointment:
        """Book an appointment with validation."""
        doctor = await self._get_doctor_or_raise(appointment_data.doctor_id)
        await self._get_patient_or_raise(appointment_data.patient_id)

        start_time = appointment_data.start_time
        end_time = start_time + SLOT_DURATION

        await self._validate_slot(doctor, start_time, end_time, exclude_appointment_id=None)

        appointment = Appointment(
            doctor_id=appointment_data.doctor_id,
            patient_id=appointment_data.patient_id,
            start_time=start_time,
            end_time=end_time,
            status=AppointmentStatus.BOOKED,
        )
        self.db.add(appointment)

        try:
            await self.db.commit()
        except IntegrityError:
            # Safety net for a race: two requests pass the SELECT-based
            # conflict check above at nearly the same time, and only one
            # INSERT can win against the partial unique index.
            await self.db.rollback()
            raise ConflictError("This time slot is already booked")

        await self.db.refresh(appointment)
        return appointment

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------
    async def cancel_appointment(self, appointment_id: int, reason: str) -> Appointment:
        """Cancel an appointment and free up the slot."""
        appointment = await self._get_appointment_or_raise(appointment_id)

        if appointment.status == AppointmentStatus.CANCELLED:
            raise ValidationError("Appointment is already cancelled")
        if appointment.status == AppointmentStatus.COMPLETED:
            raise ValidationError("Cannot cancel a completed appointment")

        appointment.status = AppointmentStatus.CANCELLED
        appointment.cancellation_reason = reason

        await self.db.commit()
        await self.db.refresh(appointment)
        return appointment

    # ------------------------------------------------------------------
    # Reschedule
    # ------------------------------------------------------------------
    async def reschedule_appointment(
        self,
        appointment_id: int,
        new_slot: AppointmentReschedule,
    ) -> Appointment:
        """Reschedule an appointment to a new slot, applying the same rules as a fresh booking."""
        appointment = await self._get_appointment_or_raise(appointment_id)

        if appointment.status == AppointmentStatus.CANCELLED:
            raise ValidationError("Cannot reschedule a cancelled appointment")
        if appointment.status == AppointmentStatus.COMPLETED:
            raise ValidationError("Cannot reschedule a completed appointment")

        doctor = await self._get_doctor_or_raise(appointment.doctor_id)

        new_start = new_slot.new_start_time
        new_end = new_start + SLOT_DURATION

        # Same validation path as a fresh booking: working hours, conflict
        # check (excluding this appointment's own current row), and the
        # 1-hour advance-notice rule. The original implementation duplicated
        # a subset of this logic by hand and silently dropped the
        # advance-notice check -- this reuses one code path so the two can't
        # drift apart again.
        await self._validate_slot(
            doctor, new_start, new_end, exclude_appointment_id=appointment_id
        )

        appointment.start_time = new_start
        appointment.end_time = new_end

        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError("The new time slot is already booked")

        await self.db.refresh(appointment)
        return appointment

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    async def _validate_slot(
        self,
        doctor: Doctor,
        start_time: datetime,
        end_time: datetime,
        exclude_appointment_id: Optional[int],
    ) -> None:
        """Validation shared by book_appointment and reschedule_appointment."""
        now = datetime.now()
        earliest_allowed = now + MIN_BOOKING_NOTICE

        if start_time < earliest_allowed:
            raise ValidationError(
                f"Appointments must be booked at least 1 hour in advance. "
                f"Current time: {now.strftime('%Y-%m-%d %H:%M')}, "
                f"Earliest booking: {earliest_allowed.strftime('%Y-%m-%d %H:%M')}"
            )

        day_start = datetime.combine(start_time.date(), doctor.working_hours_start)
        day_end = datetime.combine(start_time.date(), doctor.working_hours_end)

        if start_time < day_start or end_time > day_end:
            raise ValidationError("Appointment time is outside working hours")

        conflict_query = select(Appointment).where(
            and_(
                Appointment.doctor_id == doctor.id,
                Appointment.start_time == start_time,
                Appointment.status == AppointmentStatus.BOOKED,
            )
        )
        if exclude_appointment_id is not None:
            conflict_query = conflict_query.where(Appointment.id != exclude_appointment_id)

        existing = await self.db.execute(conflict_query)
        if existing.scalar_one_or_none():
            raise ConflictError("This time slot is already booked")

    async def _get_doctor_or_raise(self, doctor_id: int) -> Doctor:
        result = await self.db.execute(select(Doctor).where(Doctor.id == doctor_id))
        doctor = result.scalar_one_or_none()
        if not doctor:
            raise NotFoundError(f"Doctor {doctor_id} not found")
        return doctor

    async def _get_patient_or_raise(self, patient_id: int) -> Patient:
        result = await self.db.execute(select(Patient).where(Patient.id == patient_id))
        patient = result.scalar_one_or_none()
        if not patient:
            raise NotFoundError(f"Patient {patient_id} not found")
        return patient

    async def _get_appointment_or_raise(self, appointment_id: int) -> Appointment:
        result = await self.db.execute(select(Appointment).where(Appointment.id == appointment_id))
        appointment = result.scalar_one_or_none()
        if not appointment:
            raise NotFoundError(f"Appointment {appointment_id} not found")
        return appointment
