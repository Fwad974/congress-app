# Certificates, CME credits & session attendance

Attendance is recorded by **scanning a QR at the session door** (or typing the
session's code on `/certificates`). Certificates unlock automatically:

| Certificate | Unlocks when |
|-------------|--------------|
| **Certificate of Attendance** | checked in to ≥ `CERT_MIN_SESSIONS` sessions (default 3) — or, when `CERT_ATTENDANCE_PCT` > 0, to ≥ that percentage of the program (see below) |
| **CME/CPD Credit Certificate** | earned ≥ `CME_MIN_CREDITS` credits — `CME_CREDITS_PER_SESSION` (default 1) per attended session |
| **Speaker Certificate** | presenter (`speaker_id`) on ≥ 1 schedule item |

### Percentage-based eligibility

Set `CERT_ATTENDANCE_PCT` (e.g. `80`) to make the attendance-certificate goal
**⌈PCT% of the program⌉** instead of a fixed count. "The program" counts every
schedule item except breaks. With parallel tracks one person can't attend
everything, so tune the percentage accordingly. `0` (default) keeps the fixed
`CERT_MIN_SESSIONS` goal.

## Downloadable PDF + QR verification

Every unlocked certificate can be **downloaded as a PDF**
(`GET /api/certificates/{kind}/download`). The first view/download **issues**
the certificate: a row in `issued_certificates` with a unique serial
(`DSCC<year>-ATT|CME|SPK-XXXXXXXX`) and a frozen issue date. The PDF (and the
printable page) carry the serial plus a QR that resolves to the **public
verification page** `/verify/{serial}` — no login required — where anyone can
confirm the holder, kind, issue date, and attested sessions/credits.
Re-downloading reuses the same serial; if the holder attends more sessions,
the attested counts refresh upward so `/verify` always matches the newest
copy. A `revoked` flag on the row turns the verdict to "revoked" (set it via
SQL/DB tooling; there is no admin UI for it yet).

## Attendance report for institutions

`GET /api/attendance/report/export` (button on `/certificates`) downloads a
CSV listing every attended session (title, type, location, session start,
check-in time, credits), totals, and the serial + verification URL of each
issued certificate — suitable for sending to an employer or CME registrar.

## Flow

1. **Admin** opens a session on `/schedule` and clicks the **⌗ QR** action →
   a printable sheet (`/schedule/{id}/qr-print`) with the session QR + code.
   The code (`ScheduleItem.attend_code`) is generated lazily on first request.
2. **Attendee** scans the QR — it opens `/certificates?checkin=CODE`, which
   records attendance (idempotent, one per session/user) — or types the code
   into the check-in box on `/certificates`.
3. `/certificates` shows live progress bars per certificate; an unlocked one
   gets **View ↗** (printable page) and **PDF ⬇** (downloadable PDF with
   verification QR) links.
4. **Institutions** verify at `/verify/{serial}` (or scan the certificate QR)
   and can request the CSV attendance report from the attendee.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /api/attendance/checkin` | any | `{code}` → record attendance |
| `GET /api/attendance/mine` | any | Sessions I've checked in to |
| `GET /api/attendance/report/export` | any | CSV attendance report (own data) |
| `GET /api/certificates/status` | any | Certificates + progress |
| `GET /api/certificates/{kind}/download` | any (unlocked) | Certificate PDF with serial + verification QR |
| `GET /api/certificates/verify/{serial}` | public | Verification JSON |
| `GET /verify/{serial}` | public | Verification page (QR target) |
| `GET /api/schedule/{id}/qr` | admin | Session QR (SVG; generates the code) |

QR codes are generated with **segno** (pure Python — `app/core/qr.py`); the
same helper powers the poster scavenger-hunt QRs
(`/api/posters/{id}/qr`, printable at `/posters/{id}/qr-print`). Certificate
PDFs are rendered server-side with **reportlab** (`app/core/pdf.py`).

## Files

| File | Role |
|------|------|
| `app/models/attendance.py` | `SessionAttendance` |
| `app/models/certificate.py` | `IssuedCertificate` (serial, issue date, revocation) |
| `app/api/certificates.py` | check-in, status, session QR, PDF download, verification, CSV report |
| `app/core/pdf.py` | server-side certificate PDF (reportlab + QR) |
| `app/templates/certificates.html` | dynamic certificates page + check-in |
| `app/templates/certificate_view.html` | printable certificate (serial + QR) |
| `app/templates/certificate_verify.html` | public verification page |
| `app/templates/session_qr.html` | printable session QR sheet |
