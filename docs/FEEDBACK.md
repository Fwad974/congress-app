# Feedback & Ratings

Inline session ratings, a post-event survey, private speaker feedback, an
anonymized speaker digest, and a real-time organizer sentiment dashboard. UI at
`/feedback` (feature flag `feedback`) plus a per-session **Rate** button on the
schedule.

## Who does what

| Who | Can |
|-----|-----|
| **Attendee** | Rate any session (1–5 stars + optional comment + optional private speaker note); complete the post-event survey (unlocks the Participation Certificate); see their own ratings |
| **Speaker** | See an **anonymized** digest of audience reaction to their own sessions (average, distribution, comments) — never the private speaker feedback |
| **Organizer** (`admin`/`super_admin`) | Real-time sentiment dashboard across all sessions, the survey aggregate + CSV export, and the private speaker feedback |

## Features (per the spec)

- **1-tap session rating on exit** — a ★ button on every schedule card opens a
  quick star picker (1–5) with an optional comment. One rating per attendee per
  session, editable (upsert). `POST /api/sessions/{id}/rating`.
- **Post-event satisfaction survey, incentivized with a certificate** —
  overall / organization / venue (1–5), would-recommend, and comments.
  Completing it unlocks the **Participation Certificate** (a new `survey`
  certificate kind, downloadable/verifiable like the others; only offered while
  the feedback feature is on). `POST /api/feedback/survey`.
- **Speaker-specific feedback, private to organizers** — an extra
  `speaker_feedback` field on a rating that **only organizers ever see**. It is
  excluded from the speaker digest and the speaker's own session summary.
- **Real-time sentiment dashboard** — `GET /api/feedback/sentiment`: overall
  average, rating distribution, top / needs-attention sessions, recent comments,
  and the survey aggregate (avg overall/org/venue + recommend %). The dashboard
  auto-refreshes every 15s (poll-based).
- **Feedback digest for speakers** — `GET /api/feedback/my-talks`: per presented
  session, the average, distribution, and anonymized comments; no names, no
  private speaker feedback.

## Privacy model

| Field | Attendee (own) | Speaker (own session) | Organizer |
|-------|:--:|:--:|:--:|
| `stars` / `comment` | ✔ (own) | ✔ anonymized aggregate | ✔ |
| `speaker_feedback` | ✔ (own) | �’ **never** | ✔ |

A rating summary (`/api/sessions/{id}/rating/summary`) is available to the
session's own speaker (anonymized) and to organizers (full); anyone else gets
403.

## Data model (`app/models/feedback.py`)

- **`SessionRating`** — one per (session, attendee): `stars` (1–5), `comment`,
  `speaker_feedback` (organizers only). Unique on (session, user).
- **`SurveyResponse`** — one per attendee: `overall`, `organization`, `venue`,
  `would_recommend`, `comment`. Unique on user.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /api/sessions/{id}/rating` | attendee | Rate / update a session |
| `GET /api/sessions/{id}/rating` | attendee | My rating for a session |
| `GET /api/sessions/{id}/rating/summary` | speaker (own) / organizer | Session summary (private feedback for organizers only) |
| `GET /api/feedback/mine` | attendee | Sessions I've rated |
| `GET /api/feedback/survey` · `POST` | attendee | Get / submit the survey |
| `GET /api/feedback/my-talks` | speaker | Anonymized digest of my sessions |
| `GET /api/feedback/sentiment` | organizer | Real-time sentiment dashboard |
| `GET /api/feedback/survey/export` | organizer | Survey responses as CSV |

## Files

| File | Role |
|------|------|
| `app/models/feedback.py` | `SessionRating`, `SurveyResponse` |
| `app/schemas/feedback.py` | request/response schemas |
| `app/api/feedback.py` | ratings, survey, sentiment, speaker digest |
| `app/templates/feedback.html` | `/feedback` page (My Feedback / My Talks / Sentiment) |
| `app/templates/schedule.html` | per-session ★ rating button + modal |
| `app/api/certificates.py` | `survey` (Participation) certificate |
