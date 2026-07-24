# Sponsor & Exhibitor Portal

Tiered sponsor visibility, virtual exhibitor booths, and **opt-in** lead
generation for partners. UI at `/sponsors` (feature flag `sponsors`).

## Who does what

| Who | Can |
|-----|-----|
| **Attendee** (any authenticated user) | Browse the tiered directory, open a virtual booth (about + promo video + brochure + team bios), and — with explicit consent — share their contact details as a lead |
| **Organizer** (`admin` / `super_admin`) | Create/edit/hide/delete sponsors, upload logos, read & export leads (PII, logged), and view per-sponsor analytics |

## Features (per the spec)

- **Sponsor tier pages** — four tiers (`platinum`, `gold`, `silver`, `bronze`);
  the directory groups sponsors by tier and orders them tier → `display_order`
  → name. Higher tiers get larger cards.
- **Virtual exhibitor booth** — each sponsor has an `about` blurb, a promo
  `video_url` (YouTube/Vimeo auto-embed, otherwise a link), a `brochure_url`,
  a `website_url`, a `booth_number`, and a **team** list (name / title / bio).
- **Lead capture with opt-in consent (DATA-06)** — an attendee submits a lead
  only after ticking a consent box; the API **refuses** a lead without
  `consent: true` (HTTP 400). Leads are unique per (sponsor, attendee) and
  idempotent (re-submitting updates the note). The consent flag and timestamp
  are recorded.
- **Sponsor logo placement throughout the app + venue screens** — the public
  `GET /api/sponsors` list powers a logo rail on the **home** page and a
  "Sponsored by" band on the **presenter/venue screen** (`/present/{id}`), plus
  the directory itself.
- **Exhibitor analytics** — booth visits (unique visitors + total repeat
  visits), leads, and the lead **conversion rate** (leads / unique visitors),
  per sponsor and in aggregate. Organizer viewing does **not** count as a visit.

## Data model (`app/models/sponsor.py`)

- **`Sponsor`** — name, `tier`, tagline, `website_url`, logo
  (`stored_logo`/`logo_name`), `about`, `video_url`, `brochure_url`,
  `booth_number`, `team` (JSON `[{name, title, bio}]`), `is_active`,
  `display_order`.
- **`SponsorLead`** — one per (sponsor, attendee): `consent` (bool, must be
  true), `message`, timestamp. Unique on (sponsor, user).
- **`SponsorVisit`** — one per (sponsor, attendee) with a `visits` repeat
  counter and `last_visit_at`, so analytics get unique visitors + total visits
  without unbounded row growth. Unique on (sponsor, user).

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `GET /api/sponsors` | any | Tiered directory (`?include_inactive=` for organizers) |
| `GET /api/sponsors/{id}` | any | Booth detail (records an attendee visit) |
| `POST /api/sponsors/{id}/lead` | any | Submit a lead — **requires `consent: true`** |
| `POST /api/sponsors` | organizer | Create |
| `PUT /api/sponsors/{id}` | organizer | Update (fields + team) |
| `DELETE /api/sponsors/{id}` | organizer | Delete (removes leads + visits) |
| `POST /api/sponsors/{id}/logo` | organizer | Upload logo (raster image) |
| `GET /api/sponsors/{id}/logo` | any | Serve logo |
| `GET /api/sponsors/{id}/leads` | organizer | Leads with attendee PII (audit-logged) |
| `GET /api/sponsors/{id}/leads/export` | organizer | Leads as CSV (audit-logged) |
| `GET /api/sponsors/analytics` | organizer | Per-sponsor + aggregate analytics |

Sponsor CRUD is audit-logged (`sponsor_create` / `sponsor_update` /
`sponsor_delete`); lead access/export is logged as `export_data` (DATA-03,
PII access). Lead CSV cells starting with `= + - @` are quote-prefixed to
defuse spreadsheet formula injection, and the file carries a UTF-8 BOM for
Excel.

## Security & privacy notes

- **DATA-06** (opt-in for sponsor lead sharing) is enforced server-side: no
  consent → no lead. Attendee contact details are shared with a sponsor only
  through a consented lead.
- Leads are PII; only organizers can read/export them and every access is
  audit-logged (DATA-03).
- Logos are stored with opaque uuid names under `UPLOAD_DIR/sponsors/`
  (traversal-safe), raster formats only (no SVG, to avoid inline-script risk).

## Files

| File | Role |
|------|------|
| `app/models/sponsor.py` | `Sponsor`, `SponsorLead`, `SponsorVisit` |
| `app/schemas/sponsor.py` | request/response schemas |
| `app/api/sponsors.py` | directory, booth, leads, analytics, logo |
| `app/core/sponsor_files.py` | logo storage helper |
| `app/templates/sponsors.html` | `/sponsors` page (directory + booth + manage + analytics) |
| `app/templates/home.html`, `present.html` | logo placement (rail + venue band) |
