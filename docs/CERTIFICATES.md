# Certificates, CME credits & session attendance

Attendance is recorded by **scanning a QR at the session door** (or typing the
session's code on `/certificates`). Certificates unlock automatically:

| Certificate | Unlocks when |
|-------------|--------------|
| **Certificate of Attendance** | checked in to ≥ `CERT_MIN_SESSIONS` sessions (default 3) |
| **CME/CPD Credit Certificate** | earned ≥ `CME_MIN_CREDITS` credits — `CME_CREDITS_PER_SESSION` (default 1) per attended session |
| **Speaker Certificate** | presenter (`speaker_id`) on ≥ 1 schedule item |

## Flow

1. **Admin** opens a session on `/schedule` and clicks the **⌗ QR** action →
   a printable sheet (`/schedule/{id}/qr-print`) with the session QR + code.
   The code (`ScheduleItem.attend_code`) is generated lazily on first request.
2. **Attendee** scans the QR — it opens `/certificates?checkin=CODE`, which
   records attendance (idempotent, one per session/user) — or types the code
   into the check-in box on `/certificates`.
3. `/certificates` shows live progress bars per certificate; an unlocked one
   gets a **View ↗** link to a printable certificate page
   (`/certificates/{kind}/view`, print → save as PDF).

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /api/attendance/checkin` | any | `{code}` → record attendance |
| `GET /api/attendance/mine` | any | Sessions I've checked in to |
| `GET /api/certificates/status` | any | Certificates + progress |
| `GET /api/schedule/{id}/qr` | admin | Session QR (SVG; generates the code) |

QR codes are generated with **segno** (pure Python — `app/core/qr.py`); the
same helper powers the poster scavenger-hunt QRs
(`/api/posters/{id}/qr`, printable at `/posters/{id}/qr-print`).

## Files

| File | Role |
|------|------|
| `app/models/attendance.py` | `SessionAttendance` |
| `app/api/certificates.py` | check-in, status, session QR |
| `app/templates/certificates.html` | dynamic certificates page + check-in |
| `app/templates/certificate_view.html` | printable certificate |
| `app/templates/session_qr.html` | printable session QR sheet |
