# Clinic Booking System API

A REST API for a small clinic (5 doctors) to manage appointment booking, built with
FastAPI, SQLAlchemy (async), and PostgreSQL.

**Live app:** https://clinic-booking-api-dgna.onrender.com
**API docs:** https://clinic-booking-api-dgna.onrender.com/docs
**Repo:** (this repository)

---

## Contents

- [Section 1: System Design](#section-1-system-design)
- [Section 2: API Implementation](#section-2-api-implementation)
- [Quick Start — Try It Live](#quick-start--try-it-live-no-setup-needed)
- [Running Locally](#running-locally)
- [Section 3: Deployment & CI/CD](#section-3-deployment--cicd)
- [Section 4: AI Reflection](#section-4-ai-reflection)
- [Known Limitations](#known-limitations--what-id-do-next-with-more-time)

---

## Section 1: System Design

### Models

**Doctor** — `id, name, specialty, working_hours_start, working_hours_end`
Working hours are stored as a `Time` pair rather than, say, a list of available
slots, because slots are *derived*, not stored — see "Key decisions" below.

**Patient** — `id, name, email (unique), phone`

**Appointment** — `id, doctor_id, patient_id, start_time, end_time, status,
cancellation_reason, created_at, updated_at`
`status` is one of `booked / cancelled / completed`. Cancelling never deletes
the row — it's flipped to `cancelled` and reason is stored — so the clinic
keeps a full history, which matters for a real clinic (audit trail, no-show
tracking, re-booking history).

### Components

- **`app/models`** — SQLAlchemy ORM models (the DB shape).
- **`app/schemas`** — Pydantic request/response contracts (the API shape).
  Kept separate from models on purpose: the DB shape and the API shape *will*
  diverge as the system grows (e.g. we may want to expose a doctor's name on
  an appointment response without exposing their working hours).
- **`app/services/booking_service.py`** — all booking business rules
  (availability calculation, conflict checks, working-hours validation,
  advance-notice rule). Routes stay thin; this is the one place that knows
  what "a valid booking" means.
- **`app/api/routes.py`** — HTTP layer only: parses input, calls the service,
  translates domain exceptions to HTTP status codes.
- **`app/core`** — cross-cutting: DB engine/session, settings, and a small
  set of domain exceptions (`NotFoundError`, `ValidationError`,
  `ConflictError`) that the API layer maps to 404 / 400 / 409.

### Key decisions & trade-offs

1. **Slots are computed on the fly, not stored as rows.**
   A "slot" is just `working_hours_start` to `working_hours_end` in 30-minute
   increments, minus whatever's already booked. Storing every possible slot
   as a row would mean pre-generating rows per doctor per day forever, and
   keeping them in sync with working-hour changes. Trade-off: every
   availability check is an O(hours/0.5) loop in Python rather than a single
   indexed query — fine at 5 doctors, would need revisiting (e.g.
   materialized slot table + background job) at real scale.

2. **Double-booking is enforced at the database level, not just in app code.**
   A `SELECT`-then-`INSERT` check in application code has a race window: two
   requests can both pass the check before either commits. I added a
   **partial unique index** on `(doctor_id, start_time)` that only applies
   `WHERE status = 'booked'` — so it blocks two *active* bookings for the
   same slot, but still lets a slot be rebooked after cancellation. The
   service catches the resulting `IntegrityError` and turns it into a clean
   409, so a race condition returns a normal API error instead of a 500.
   *(Note: an earlier version of this constraint didn't have the `WHERE
   status = 'booked'` filter — see Section 4 for how that bug was found.)*

3. **Reschedule reuses the exact same validation as a fresh booking**, via a
   shared `_validate_slot()` method (working hours, 1-hour advance notice,
   conflict check excluding the appointment's own row). The two operations
   are different HTTP verbs but the *same* business rule — "is this
   `(doctor, start_time)` a legal slot for anyone to occupy" — so they share
   one code path rather than two copies that could drift apart.

4. **Cancellation is a soft state change, not a delete.** Needed for
   history/audit and because `GET /patients/{id}/appointments` should still
   be able to show a patient their past cancellations.

5. **"We're starting small but want to grow"** — the design that best
   supports this without over-engineering now:
   - Doctor/Patient/Appointment as separate tables (not a flat booking
     table) so adding e.g. multiple clinic locations later is a new FK, not
     a schema rewrite.
   - Business rules live in one service class, not scattered across route
     handlers, so adding a rule (e.g. "doctors can block out vacation days")
     touches one file.
   - Config (DB URL, CORS, debug flag) is environment-driven from day one,
     not hardcoded, so moving from one doctor's laptop to a real deployment
     doesn't require code changes.
   - What I deliberately did *not* build, to stay in scope: auth/roles
     (anyone can hit any endpoint right now), multi-location support,
     timezone handling (all times are assumed to be the clinic's local
     time), and recurring appointments.

---

## Section 2: API Implementation

Built with FastAPI + SQLAlchemy (async) + PostgreSQL.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/doctors/{id}/availability?date=YYYY-MM-DD` | Free 30-min slots for a doctor on a date |
| `POST` | `/api/v1/appointments` | Book a slot |
| `PATCH` | `/api/v1/appointments/{id}/cancel?reason=...` | Cancel with a reason |
| `PATCH` | `/api/v1/appointments/{id}/reschedule` | Move to a new slot |
| `GET` | `/api/v1/patients/{id}/appointments?upcoming_only=true` | A patient's appointments, sorted by date |
| `POST` | `/api/v1/doctors` | Create a doctor (setup/seeding) |
| `POST` | `/api/v1/patients` | Create a patient (setup/seeding) |

Interactive docs are auto-generated by FastAPI at `/docs` once the app is running.

### Constraints from the brief — how each is met

| Requirement | How it's met |
|---|---|
| Validation failures return meaningful errors with correct HTTP status codes | Domain exceptions (`NotFoundError` → 404, `ConflictError` → 409, `ValidationError` → 400) mapped centrally in `routes.py`, with human-readable messages (e.g. "Appointments must be booked at least 1 hour in advance") |
| Code structured sensibly, not a single file | Split into `models/`, `schemas/`, `services/`, `api/`, `core/` |
| Basic test coverage for booking logic | 12 tests in `tests/test_booking.py` covering booking, conflicts, cancellation, reschedule, and two regression tests for bugs found during review (see Section 4) |
| Bonus: `GET /patients/{id}/appointments` sorted by date | Implemented, with `upcoming_only` query param |
| Bonus: prevent bookings within 1 hour of now | Implemented in `_validate_slot()`, applied to both booking and reschedule |

---

## Quick Start — Try It Live (No Setup Needed)

The live deployment already has one doctor and one patient seeded, so you can
test the core booking flow immediately at
`https://clinic-booking-api-dgna.onrender.com/docs` without creating any data
yourself.

> **Note:** hosted on Render's free tier — the first request after ~15
> minutes of inactivity may take 30–50 seconds to respond while the service
> wakes up. Subsequent requests are fast.

**Seeded data:**

| Resource | ID | Details |
|---|---|---|
| Doctor | `1` | Dr. Amina Yusuf, General Practice, 09:00–17:00 |
| Patient | `1` | Test Patient, test.patient@example.com |

**Try the flow in order** (dates below use `2026-08-26` as an example —
**if that date has already passed by the time you're reading this, swap in
any date from tomorrow onward**; past dates always return zero slots by
design):

1. **Check availability** — `GET /api/v1/doctors/1/availability?date=2026-08-26`

2. **Book a slot** — `POST /api/v1/appointments`
   ```json
   {
     "doctor_id": 1,
     "patient_id": 1,
     "start_time": "2026-08-26T10:00:00"
   }
   ```

3. **View the patient's appointments** — `GET /api/v1/patients/1/appointments`

4. **Cancel it** — `PATCH /api/v1/appointments/1/cancel?reason=testing`

5. **Re-check availability** — repeat step 1; the 10:00 slot should be free
   again, confirming cancellation releases the slot.

<details>
<summary>Seed commands (curl) — for creating additional test data</summary>

```bash
curl -X POST https://clinic-booking-api-dgna.onrender.com/api/v1/doctors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Dr. Jane Doe",
    "specialty": "Pediatrics",
    "working_hours_start": "08:00:00",
    "working_hours_end": "16:00:00"
  }'

curl -X POST https://clinic-booking-api-dgna.onrender.com/api/v1/patients \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Another Patient",
    "email": "another.patient@example.com",
    "phone": "0798765432"
  }'
```

Swap the URL for `http://localhost:8000` when running locally.

</details>

---

## Running Locally

### Option A — Docker Compose (recommended, no local Postgres needed)

```bash
docker compose up --build
```
This starts Postgres and the API together. API will be live at
`http://localhost:8000`, docs at `http://localhost:8000/docs`.

### Option B — Run directly with Python

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # then edit .env with your local Postgres URL
uvicorn app.main:app --reload
```

### Running tests

```bash
pytest tests/ -v
```
Tests use an in-memory SQLite database (via `aiosqlite`) — no Postgres
required to run the suite.

---

## Section 3: Deployment & CI/CD

**Public URL:** https://clinic-booking-api-dgna.onrender.com

**Deploy target:** Render (see step-by-step guide below) — swap for
Fly.io/Railway by changing only the `deploy` job in
`.github/workflows/ci-cd.yml`; the `test` job is provider-agnostic.

**Pipeline (`.github/workflows/ci-cd.yml`):**
- On every PR into `main`: installs dependencies and runs `pytest tests/ -v`.
  A red X on the PR blocks merging on a broken build.
- On every push to `main` (i.e. once a PR is merged): if tests pass, calls
  Render's deploy hook to trigger a fresh deploy. Nothing deploys off a
  branch that hasn't been merged.

---

## Section 4: AI Reflection

1. **What I used AI for across the four sections:** scaffolding the initial
   project structure and boilerplate (models/schemas/routes split), drafting
   the booking-conflict and availability logic, writing the Dockerfile and
   GitHub Actions workflow, and structuring this README.

2. **Where an AI suggestion improved the work:** I asked for a review of the
   original `booking_service.py` against the brief's requirement that
   "once cancelled, [a] slot must become available again." The review
   surfaced that the DB-level `UniqueConstraint(doctor_id, start_time)` had
   no status filter, so a cancelled row would permanently block the same
   slot from ever being rebooked — the API would report the slot as
   "available" via `get_available_slots()` while the actual `INSERT` would
   fail with an `IntegrityError`. The fix — a partial unique index scoped to
   `WHERE status = 'booked'` — came out of that review.

3. **Where AI output was wrong or incomplete and how I caught it:** the
   partial-index fix above initially compared
   `status == AppointmentStatus.BOOKED.value` in the index's `WHERE` clause,
   but SQLAlchemy's `Enum` type stores the Python enum's `.name` ("BOOKED")
   in the DB by default, not `.value` ("booked") — so the WHERE clause would
   never have matched any row. I caught this by actually running the test
   suite (specifically a new `test_cancel_then_rebook_same_slot` test)
   rather than trusting the fix looked right, and fixed it by pinning the
   column's stored value explicitly with `values_callable`.

4. **Two decisions made without AI:**
   - **Keeping cancellation as a soft-delete (status flip) rather than a
     row delete.** This is a product/domain call about what a clinic needs
     (audit trail, re-booking history) rather than a coding problem, so I
     made it based on how the scenario is described ("starting small but
     want to grow") rather than asking AI to guess clinic requirements.
   - **Not adding authentication/authorization in this pass.** The brief
     doesn't mention auth and adding it would have expanded scope
     significantly (user model, sessions/tokens, route guards) for a
     take-home with a defined time limit. I judged that flagging it as an
     explicit, deliberate omission was more useful to a reviewer than
     either silently skipping it or bolting on a half-implemented auth
     layer.

---

## Known limitations / what I'd do next with more time

- No authentication — any client can book/cancel/reschedule for any patient.
- No pagination on `GET /patients/{id}/appointments`.
- Availability calculation is O(n) per request in Python rather than a set
  DB query; fine at current scale, worth revisiting if doctor count grows
  significantly.
- No handling of doctor time-off / holidays — working hours are assumed
  constant every day.
