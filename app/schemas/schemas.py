from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime, time
from typing import Optional, List
from enum import Enum


class AppointmentStatus(str, Enum):
    BOOKED = "booked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class DoctorBase(BaseModel):
    name: str
    specialty: Optional[str] = None
    working_hours_start: time
    working_hours_end: time


class DoctorCreate(DoctorBase):
    pass


class DoctorResponse(DoctorBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class PatientBase(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None


class PatientCreate(PatientBase):
    pass


class PatientResponse(PatientBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class AppointmentBase(BaseModel):
    doctor_id: int
    patient_id: int
    start_time: datetime
    end_time: Optional[datetime] = None

    @field_validator("start_time")
    @classmethod
    def validate_start_time(cls, v: datetime) -> datetime:
        if v < datetime.now():
            raise ValueError("Cannot book appointments in the past")
        return v


class AppointmentCreate(AppointmentBase):
    pass


class AppointmentResponse(AppointmentBase):
    id: int
    status: AppointmentStatus
    cancellation_reason: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AppointmentUpdate(BaseModel):
    status: Optional[AppointmentStatus] = None
    cancellation_reason: Optional[str] = None


class AppointmentReschedule(BaseModel):
    new_start_time: datetime

    @field_validator("new_start_time")
    @classmethod
    def validate_new_start_time(cls, v: datetime) -> datetime:
        if v < datetime.now():
            raise ValueError("Cannot reschedule to past time")
        return v


class AvailabilityResponse(BaseModel):
    doctor_id: int
    date: str
    available_slots: List[datetime]
