from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from datetime import datetime

from app.core.database import get_db
from app.core.exceptions import NotFoundError, ValidationError, ConflictError
from app.schemas.schemas import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentReschedule,
    AvailabilityResponse,
    DoctorResponse,
    PatientResponse,
    DoctorCreate,
    PatientCreate,
)
from app.services.booking_service import BookingService
from app.models.models import Doctor, Patient, Appointment

router = APIRouter()


def _raise_http(e: Exception):
    if isinstance(e, NotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    if isinstance(e, ConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if isinstance(e, ValidationError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/doctors/{doctor_id}/availability", response_model=AvailabilityResponse)
async def get_doctor_availability(
    doctor_id: int,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    min_hours_notice: int = Query(0, ge=0, description="Minimum hours notice required"),
    db: AsyncSession = Depends(get_db),
):
    """Get all available 30-minute slots for a doctor on a given date."""
    service = BookingService(db)
    try:
        available_slots = await service.get_available_slots(doctor_id, date, min_hours_notice)
        return AvailabilityResponse(doctor_id=doctor_id, date=date, available_slots=available_slots)
    except (NotFoundError, ValidationError, ConflictError) as e:
        _raise_http(e)


@router.post("/appointments", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def book_appointment(appointment: AppointmentCreate, db: AsyncSession = Depends(get_db)):
    """Book a new appointment."""
    service = BookingService(db)
    try:
        return await service.book_appointment(appointment)
    except (NotFoundError, ValidationError, ConflictError) as e:
        _raise_http(e)


@router.patch("/appointments/{appointment_id}/cancel", response_model=AppointmentResponse)
async def cancel_appointment(
    appointment_id: int,
    reason: str = Query(..., description="Reason for cancellation"),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an appointment with a reason."""
    service = BookingService(db)
    try:
        return await service.cancel_appointment(appointment_id, reason)
    except (NotFoundError, ValidationError, ConflictError) as e:
        _raise_http(e)


@router.patch("/appointments/{appointment_id}/reschedule", response_model=AppointmentResponse)
async def reschedule_appointment(
    appointment_id: int,
    reschedule_data: AppointmentReschedule,
    db: AsyncSession = Depends(get_db),
):
    """Reschedule an appointment to a new slot."""
    service = BookingService(db)
    try:
        return await service.reschedule_appointment(appointment_id, reschedule_data)
    except (NotFoundError, ValidationError, ConflictError) as e:
        _raise_http(e)


@router.get("/patients/{patient_id}/appointments", response_model=List[AppointmentResponse])
async def get_patient_appointments(
    patient_id: int,
    upcoming_only: bool = Query(True, description="Only show upcoming appointments"),
    db: AsyncSession = Depends(get_db),
):
    """Get all appointments for a patient, sorted by date."""
    query = select(Appointment).where(Appointment.patient_id == patient_id)
    if upcoming_only:
        query = query.where(Appointment.start_time >= datetime.now())
    query = query.order_by(Appointment.start_time)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/doctors", response_model=DoctorResponse, status_code=status.HTTP_201_CREATED)
async def create_doctor(doctor_data: DoctorCreate, db: AsyncSession = Depends(get_db)):
    """Create a new doctor."""
    doctor = Doctor(
        name=doctor_data.name,
        specialty=doctor_data.specialty,
        working_hours_start=doctor_data.working_hours_start,
        working_hours_end=doctor_data.working_hours_end,
    )
    db.add(doctor)
    await db.commit()
    await db.refresh(doctor)
    return doctor


@router.post("/patients", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(patient_data: PatientCreate, db: AsyncSession = Depends(get_db)):
    """Create a new patient."""
    patient = Patient(
        name=patient_data.name,
        email=patient_data.email,
        phone=patient_data.phone,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient
