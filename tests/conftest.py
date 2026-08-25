import pytest_asyncio
from datetime import time
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.models import Base, Doctor, Patient


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def test_data(db_session):
    doctor = Doctor(
        name="Dr. Smith",
        specialty="Cardiology",
        working_hours_start=time(9, 0),
        working_hours_end=time(17, 0),
    )
    patient = Patient(
        name="John Doe",
        email="john@example.com",
        phone="1234567890",
    )
    db_session.add(doctor)
    db_session.add(patient)
    await db_session.commit()
    await db_session.refresh(doctor)
    await db_session.refresh(patient)
    return {"doctor": doctor, "patient": patient}
