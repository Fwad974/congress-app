# Dubai Stem Cell Congress 2027 — Conference App

FastAPI + PostgreSQL web application with **secure Admin User Management Dashboard**.

## What's New: Admin Dashboard

Full-stack User Management dashboard at `/admin` with:

### Backend Security

| Rule | Implementation |
|------|---------------|
| **AUTH-01** | 2FA flagged for admin roles |
| **AUTH-02** | Admin session timeout: 30 min. Regular: 24h |
| **AUTH-03** | 5 failed logins → 15 min lock. 10 → manual unlock only |
| **ROLE-01** | Only Super Admin can create other Super Admins |
| **ROLE-02** | Admins can assign up to Moderator level only |
| **ROLE-04** | Self-demotion/role-change prohibited |
| **MOD-04** | Escalation ladder: warn → 24h mute → suspend, enforced (muted users can't post; suspended users are logged out) and audit-logged with a reason. See [`docs/MODERATION.md`](docs/MODERATION.md). |
| **AUDIT-01** | All admin actions logged: who, what, when, target, IP hash |
| **AUDIT-02** | Logs are write-once, immutable (no updated_at column) |
| **DATA-03** | PII exports are admin-only and logged |

### Security Features

- **RBAC Enforcement** — Role hierarchy with 8 levels (attendee → super_admin)
- **Rate Limiting** — 120 req/min per IP on admin endpoints
- **Login Lockout** — Sliding window: 5 failures = 15m lock, 10 = permanent
- **Audit Trail** — Every admin action recorded with IP hash, user agent, request path
- **Role Assignment Rules** — Admins can only manage users below their level
- **Bulk Action Safety** — Cannot bulk-modify users at or above your role level
- **CSV Export Logging** — All data exports logged per DATA-03

### Continuous Integration

Every push and pull request runs the full test suite via GitHub Actions
(`.github/workflows/ci.yml`) on Python 3.11. Tests use an isolated SQLite
database (`tests/conftest.py`) so CI needs no Postgres or Redis. Run locally
with:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

### API Endpoints

| Method | Endpoint | Description | Min Role |
|--------|----------|-------------|----------|
| GET | `/api/admin/stats` | Dashboard statistics | Admin |
| GET | `/api/admin/users` | List users (paginated, searchable) | Admin |
| GET | `/api/admin/users/{id}` | Get single user | Admin |
| POST | `/api/admin/users` | Create user | Admin |
| PUT | `/api/admin/users/{id}` | Update user profile | Admin |
| PUT | `/api/admin/users/{id}/role` | Change user role | Admin |
| POST | `/api/admin/users/{id}/suspend` | Suspend user (with reason) | Admin |
| POST | `/api/admin/users/{id}/activate` | Activate suspended user | Admin |
| POST | `/api/admin/users/{id}/unlock` | Unlock locked login | Admin |
| DELETE | `/api/admin/users/{id}` | Delete user | **Super Admin** |
| POST | `/api/admin/users/bulk` | Bulk suspend/activate/role change | Admin |
| GET | `/api/admin/users/export/csv` | Export users to CSV | Admin |
| GET | `/api/admin/audit` | Audit log (filterable) | Admin |
| GET | `/api/admin/notes` | List all user notes (paginated, searchable) | **Super Admin** |
| DELETE | `/api/admin/notes/{id}` | Delete a user note (moderation, logged) | **Super Admin** |
| PUT | `/api/schedule/{id}/presentation` | Presenter edits abstract/slides/description | Presenter / Admin |
| POST | `/api/papers` · GET `/api/papers/mine` | Submit a paper · my submissions | Any user |
| GET | `/api/papers/assigned` · PUT `/api/papers/{id}/review` | Papers to review · submit a review | Reviewer |
| GET | `/api/papers` · `/api/papers/reviewers` | List all · assignable reviewers (COI) | Review chair |
| POST | `/api/papers/{id}/assign` · `/decision` | Assign reviewers · accept/reject/revise | Review chair |
| GET | `/api/notes` | List my notes (optional `?schedule_item_id=`) | Any user |
| POST | `/api/notes` | Create a note (optionally linked to a session) | Any user |
| PUT | `/api/notes/{id}` | Update my note | Any user |
| DELETE | `/api/notes/{id}` | Delete my note | Any user |
| GET | `/api/sessions/{id}/questions` | List a session's Q&A | Any user |
| POST | `/api/sessions/{id}/questions` | Ask a question | Any user |
| GET | `/api/sessions/{id}/qa/stream` | Live Q&A event stream (SSE) | Any user |
| POST/DELETE | `/api/questions/{id}/upvote` | Upvote / remove upvote | Any user |
| PUT | `/api/questions/{id}/status` | Mark answered / hide / reopen | Chair+ |
| DELETE | `/api/questions/{id}` | Delete (author or chair+) | Author / Chair+ |
| POST | `/api/sessions/{id}/polls` | Create a poll (draft) | Chair+ |
| GET | `/api/sessions/{id}/polls` | List a session's polls | Any user |
| GET | `/api/sessions/{id}/polls/stream` | Live poll event stream (SSE) | Any user |
| POST | `/api/polls/{id}/vote` | Vote / submit a word | Any user |
| GET | `/api/polls/{id}/results` | Aggregated results | Any user |
| PUT | `/api/polls/{id}/status` | Open / close a poll | Chair+ |
| DELETE | `/api/polls/{id}` | Delete a poll | Chair+ |
| POST | `/api/admin/notifications/broadcast` | Send an announcement / emergency alert | Admin |
| GET | `/api/admin/notifications/broadcasts` | Broadcast history + daily count | Admin |
| GET | `/api/reactions/emojis` | Allowed reaction emojis | Any user |
| GET | `/api/certificates/{kind}/download` | Certificate PDF (serial + verification QR) | Any user (unlocked) |
| GET | `/api/certificates/verify/{serial}` · `/verify/{serial}` | Verify a certificate (JSON · page) | Public |
| GET | `/api/attendance/report/export` | CSV attendance report for institutions | Any user |
| GET | `/api/sponsors` · `/api/sponsors/{id}` | Sponsor directory · virtual booth | Any user |
| POST | `/api/sponsors/{id}/lead` | Submit a lead (requires opt-in consent) | Any user |
| POST/PUT/DELETE | `/api/sponsors` · `/api/sponsors/{id}` | Manage sponsors | Organizer |
| GET | `/api/sponsors/{id}/leads` · `/leads/export` | Read / export leads (PII, logged) | Organizer |
| GET | `/api/sponsors/analytics` | Booth visits, leads, conversion | Organizer |
| POST/GET | `/api/sessions/{id}/rating` | Rate a session (1–5 + comment + private speaker note) | Any user |
| GET | `/api/sessions/{id}/rating/summary` | Session ratings (private feedback organizers-only) | Speaker / Organizer |
| GET/POST | `/api/feedback/survey` | Post-event survey (unlocks Participation cert) | Any user |
| GET | `/api/feedback/my-talks` | Anonymized rating digest for my sessions | Speaker |
| GET | `/api/feedback/sentiment` · `/survey/export` | Sentiment dashboard · survey CSV | Organizer |
| GET | `/api/venue` · `/wifi-qr` · `/route` | Venue info + live floor plan · WiFi QR · navigation | Any user |
| PUT/POST | `/api/venue/settings` · `/api/venue/rooms` | Manage venue content & rooms | Organizer |
| GET | `/api/recordings` · `/{id}` · `/search?q=` | Recording catalogue · detail + transcript · transcript search | Any user |
| POST | `/api/recordings/{id}/consent` | Grant / deny recording consent | Speaker |
| POST | `/api/recordings` · `/{id}/publish` · `/{id}/transcript` | Create · publish (30-day window) · upload transcript | Organizer |
| GET | `/api/knowledge/graph` · `/topics` · `/thread/{topic}` | Topic map · trending · research thread | Any user |
| GET | `/api/knowledge/related/{kind}/{id}` · `/cross-pollination` | "Relevant because…" · adjacent topics | Any user |
| GET | `/api/knowledge/my-map` · `/my-map.pdf` | Personal knowledge map · PDF export | Any user |
| POST | `/api/companion/ask` | Natural-language question about the congress | Any user |
| GET | `/api/companion/briefing` · `/nudges` · `/serendipity` | Day briefing · proactive nudges · serendipity picks | Any user |
| GET | `/api/companion/prep/{id}` · `/summary/{id}` · `/guide` | Speaker prep · session summary · Dubai guide | Speaker+ · Any user |
| POST | `/api/sessions/{id}/reactions` | Send batched emoji reactions | Any user |
| GET | `/api/sessions/{id}/reactions/stream` | Live reaction burst stream (SSE) | Any user |

### Frontend Dashboard

- **Overview Tab** — Stats cards, role breakdown, recent activity
- **Users Tab** — Full CRUD table with search, filter, sort, pagination
- **Audit Tab** — Filterable audit log with severity indicators
- **Modals** — Create user, edit user, change role (with reason), suspend (with reason)
- **Bulk Actions** — Select multiple users for batch operations
- **CSV Export** — One-click user export

## Architecture

```
congress-app/
├── app/
│   ├── api/
│   │   ├── admin.py           # Admin REST API (RBAC-protected)
│   │   ├── admin_pages.py     # Admin dashboard page route
│   │   ├── auth.py            # Auth API (login tracking + audit)
│   │   └── pages.py           # Public page routes
│   ├── core/
│   │   ├── admin_security.py  # RBAC, rate limiter, login tracker
│   │   ├── audit_service.py   # Audit log writer
│   │   ├── config.py          # Environment settings
│   │   ├── database.py        # SQLAlchemy engine
│   │   └── security.py        # JWT, password hashing
│   ├── models/
│   │   ├── audit_log.py       # Immutable audit log model
│   │   └── user.py            # User model (8 roles)
│   ├── schemas/
│   │   ├── admin.py           # Admin request/response schemas
│   │   └── user.py            # Auth schemas
│   ├── templates/
│   │   ├── admin_dashboard.html  # Admin UI (3 tabs)
│   │   ├── nav.html              # Shared nav (with admin link)
│   │   └── ...                   # Other pages
│   ├── static/
│   │   ├── css/main.css
│   │   └── js/main.js
│   └── main.py
├── tests/
│   ├── conftest.py            # Test fixtures (SQLite DB, user factories)
│   ├── test_auth.py           # Auth endpoint tests (17 tests)
│   ├── test_admin.py          # Admin endpoint & RBAC tests (42 tests)
│   ├── test_security.py       # Security unit tests (38 tests)
│   └── test_schemas.py        # Schema validation tests (11 tests)
├── docker-compose.yml         # Full stack: app + PostgreSQL + Redis + pgAdmin
├── Dockerfile                 # Production container image
├── .dockerignore
├── entrypoint.sh              # Auto-creates super admin from env vars
├── seed_admin.py              # Interactive super admin setup script
├── .env
└── requirements.txt
```

## Setup

### Local Development

```bash
# 1. Create PostgreSQL database
sudo -u postgres psql
CREATE USER congress_user WITH PASSWORD 'congress_pass';
CREATE DATABASE dubai_congress OWNER congress_user;
\q

# 2. Install dependencies
pip install -r requirements.txt

# 3. Generate a proper secret key
openssl rand -hex 32  # Put in .env

# 4. Run
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 5. Open browser
# Landing: http://localhost:8000
# Admin:   http://localhost:8000/admin (requires admin role)
```

### Docker Compose (recommended)

Starts the full stack: **app + PostgreSQL + Redis** (pgAdmin is opt-in via the
`tools` profile).

> **Required in production:** compose defaults `ENVIRONMENT=production`, so the
> app **refuses to boot without a strong `SECRET_KEY`**. Generate one once:
> ```bash
> echo "SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')" >> .env
> ```
> (For pure local dev you can instead set `ENVIRONMENT=development` in `.env`,
> which permits the built-in fallback key.) Postgres/Redis are bound to
> `127.0.0.1` and never published to the internet; reach them via an SSH tunnel.
> To run pgAdmin: `docker compose --profile tools up -d pgadmin` (localhost only;
> set `PGADMIN_PASSWORD` in `.env`).

**Option A: Using `.env` file (recommended)**

Add the super admin credentials to your `.env` file:

```bash
echo 'SUPER_ADMIN_EMAIL=admin@example.com' >> .env
echo 'SUPER_ADMIN_PASSWORD=SecurePass123' >> .env
```

Then start normally — Docker Compose reads `.env` automatically:

```bash
docker compose up -d
```

**Option B: Inline environment variables**

```bash
SUPER_ADMIN_EMAIL=admin@example.com \
SUPER_ADMIN_PASSWORD=SecurePass123 \
docker compose up -d
```

> **Important:** The env vars must be set when running `docker compose up`. Running `docker compose up` without them will skip super admin creation.

**Common commands:**

```bash
# View logs (verify super admin creation)
docker compose logs app | grep -i "super admin"

# Follow live logs
docker compose logs -f app

# Stop all services
docker compose down

# Stop and remove data volumes (full reset)
docker compose down -v
```

**Troubleshooting login:**
- Check logs for `Created super admin:` or `Super admin ... updated` message
- If no message appears, the env vars were not passed — add them to `.env` and restart
- To reset everything: `docker compose down -v && docker compose up -d`

| Service | URL | Description |
|---------|-----|-------------|
| **App** | http://localhost:8000 | Congress application |
| **pgAdmin** | http://localhost:5050 | Database admin UI (login: `admin@congress.com` / `admin123`) |
| **PostgreSQL** | localhost:5432 | Database (user: `congress_user`, pass: `congress_pass`) |
| **Redis** | localhost:6379 | Cache/session store |

To connect pgAdmin to the database, add a server with host `db`, port `5432`, user `congress_user`, password `congress_pass`.

### Docker (standalone)

```bash
# Build the image
docker build -t congress-app .

# Run with auto super admin creation
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@db-host:5432/dubai_congress \
  -e SECRET_KEY=$(openssl rand -hex 32) \
  -e SUPER_ADMIN_EMAIL=admin@example.com \
  -e SUPER_ADMIN_PASSWORD=SecurePass123 \
  -e SUPER_ADMIN_NAME="Admin Name" \
  congress-app
```

#### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ENVIRONMENT` | No | `production` (compose default) or `development`. In production the app refuses to boot with a weak/default `SECRET_KEY`. |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes (prod) | JWT + session signing key (generate with `openssl rand -hex 32`). Required under `ENVIRONMENT=production`. |
| `ALGORITHM` | No | JWT algorithm (default: `HS256`) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Token expiry in minutes (default: `1440`) |
| `SUPER_ADMIN_EMAIL` | No | Auto-create super admin with this email on startup |
| `SUPER_ADMIN_PASSWORD` | No | Password for auto-created super admin |
| `SUPER_ADMIN_NAME` | No | Full name for auto-created super admin (default: `Super Admin`) |
| `SUPER_ADMIN_INSTITUTION` | No | Institution for auto-created super admin |
| `VAPID_PUBLIC_KEY` | No | Web Push VAPID public key (enables browser push) |
| `VAPID_PRIVATE_KEY` | No | Web Push VAPID private key — **keep secret** |
| `VAPID_SUBJECT` | No | Contact for push services (default: `mailto:admin@dubaicongress.example`) |
| `REDIS_URL` | No | Redis URL for realtime fan-out (live Q&A). Empty = in-process only (single worker). Docker sets `redis://redis:6379/0` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | No | Google OAuth credentials. Both set = Google sign-in enabled |
| `ORCID_CLIENT_ID` / `ORCID_CLIENT_SECRET` | No | ORCID OAuth credentials. Both set = ORCID sign-in enabled |
| `ORCID_ENV` | No | `sandbox` (default) or `production` — picks the ORCID host |
| `OAUTH_REDIRECT_BASE` | No | Public origin for callback URLs behind a proxy (e.g. `https://app.example.com`); empty = derive from request |

## Schedule Change Notifications

When an admin edits or cancels a schedule item, every user who **bookmarked**
that item is notified:

- **In-app feed** (always on): a per-user notification feed (`UserNotification`)
  surfaced as a toast on the next poll — no extra setup required.
- **Web Push** (optional): real browser push delivered even when the app tab is
  closed. The feed is always the source of truth and the fallback.

See [`docs/NOTIFICATIONS.md`](docs/NOTIFICATIONS.md) for the full architecture,
data model, and API.

## Social Login (Google & ORCID)

Users can sign in / sign up with **Google** or **ORCID** in addition to
email + password. Each provider's button appears only when its credentials are
configured, so nothing shows until you set it up. Full setup — registering the
apps, redirect URIs, sandbox vs production, and the account-linking policy — is
in [`docs/OAUTH.md`](docs/OAUTH.md). In short:

1. Register an app with each provider and set `GOOGLE_CLIENT_ID/SECRET` and/or
   `ORCID_CLIENT_ID/SECRET` in your `.env`.
2. Add the callback URL `<your-origin>/api/auth/oauth/{provider}/callback` to
   the provider's allowed redirect URIs.
3. Restart — the buttons light up.

Accounts link by **verified email**: a Google sign-in whose verified email
matches an existing account logs into that account (keeping password login);
otherwise a new attendee account is created. Links are stored in
`oauth_accounts`, so one user can attach both Google and ORCID.

## Notes

Any signed-in user can keep notes from the **Notes** page (`/notes`). A note can
stand alone (a personal jot) or be linked to a schedule session — the schedule
page has a per-session note button that deep-links to a pre-filled composer
(`/notes?session={id}`). Notes are private to their author.

**Super admins** can review every user's notes from the **User Notes** tab in
the admin dashboard (searchable by content, author name, or email) and delete
any note for moderation. Deletions are written to the audit log
(`note_delete`). Regular admins do not have access to notes.

## Abstracts & Paper Submission

A peer-review workflow at `/papers` (role-based tabs). See
[`docs/PAPERS.md`](docs/PAPERS.md) for details.

- **Authors** (any signed-in user) submit a paper (title, authors, category,
  abstract, optional full-paper link), track status, and — on a revision
  decision — respond to reviewers and resubmit (bounded to 2 rounds).
- **Reviewers** score papers assigned to them against a **structured rubric**
  (Originality, Significance, Methodology, Clarity — each 1–5; the overall score
  is the mean) and comment.
- **Review chairs** (`review_chair`/`admin`/`super_admin`) list all
  submissions, assign reviewers **manually or auto** (least-loaded, with a
  same-institution **COI guard** requiring explicit override), monitor
  completion/deadlines, record decisions (accept / reject / request revision),
  and may **override the score with a written justification** — all audit-logged
  and pushed to the author's notification feed.

Reviews are hidden from the author until a decision; the submitter's identity is
hidden from reviewers (light double-blind).

## Speakers (Program & Schedule)

A session can be linked to the **app account presenting it**: admins set a
"Presenter" by email when creating/editing a schedule item (`speaker_email`).
That user then gets speaker features on their own sessions:

- **My Talks** filter on the schedule (`/api/schedule?presenter=me`) — the
  sessions they present.
- **Self-service editing** (`PUT /api/schedule/{id}/presentation`): update the
  **abstract**, **description**, and a **slides/materials link** — but *not* the
  time, room, or title (those stay admin-controlled).
- **Q&A moderation** for their own session (mark answered / hide / delete),
  same powers a session chair has, scoped to sessions they present.

Slides links surface as a **📎 Slides** chip on the session card.

## Live Q&A

Each session has a **Live Q&A** page (`/qa/{session_id}`, reachable from the
Q&A button on every schedule card). Attendees submit questions and upvote
others'; the list re-sorts live (most upvoted first, answered sink to the
bottom). Session chairs, moderators, and admins can mark questions answered,
hide them, or delete any question — moderation is audit-logged
(`question_moderate`).

Updates are pushed in real time over **Server-Sent Events**
(`/api/sessions/{id}/qa/stream`). Fan-out goes through a small realtime layer
(`app/core/realtime.py`) that uses **Redis pub/sub** when `REDIS_URL` is set
(so it works across multiple workers/containers) and falls back to in-process
delivery otherwise. Docker Compose wires `REDIS_URL` to the bundled Redis
automatically. See [`docs/LIVE_QA.md`](docs/LIVE_QA.md) for details.

## Live Polls & Word Clouds

The session page has a **Polls** tab alongside Q&A. Chairs create polls
(single-choice, multiple-choice, or word cloud), open/close them, and delete
them; attendees vote or submit words while a poll is open. Results aggregate
from the stored responses (counts can't drift) and stream live over SSE.

A chrome-free **presenter view** (`/present/{session_id}`, opened via the
"Present ↗" button) shows the open poll on the big screen — animated bar charts
for choice polls, a frequency-sized word cloud for word-cloud polls — updating
live as votes land. Built on the same realtime layer as Q&A.

## Venue & Dubai Info

Interactive venue guide at `/venue` (feature flag `venue`), fully
**admin-managed** with Dubai defaults served until an organizer edits. See
[`docs/VENUE.md`](docs/VENUE.md).

- **Floor plan** — schematic SVG from admin-placed rooms with a **live
  overlay**: each room shows the session happening now (matched to
  `ScheduleItem.location`) and what's next.
- **Turn-by-turn navigation** between rooms (exit → stairs/elevator → heading →
  arrival side), generated from the room grid in **English and Arabic**.
- **WiFi one-tap connect** — QR in the `WIFI:` format plus copy buttons.
- **Dubai local info** (hotels, restaurants, pharmacies, ATMs), **transport**
  (metro/taxi/parking), and **emergency contacts** with tap-to-call.
- **English / العربية toggle** — RTL layout and per-field Arabic content.

## Feedback & Ratings

Inline session ratings, a post-event survey, and organizer sentiment at
`/feedback` (feature flag `feedback`). See [`docs/FEEDBACK.md`](docs/FEEDBACK.md).

- **Attendees** rate any session 1–5 stars (a ★ button on every schedule card)
  with an optional comment and an optional **private note about the speaker**,
  and complete the **post-event survey** — which unlocks a **Participation
  Certificate**.
- **Speakers** get an **anonymized** digest of the audience reaction to their
  own sessions (average, distribution, comments) — never the private speaker
  feedback.
- **Organizers** get a **real-time sentiment dashboard** (overall average,
  distribution, top/needs-attention sessions, recent comments, survey aggregate
  + CSV export) and can read the private speaker feedback.

## Sponsors & Exhibitors

A **Sponsor & Exhibitor Portal** at `/sponsors` (feature flag `sponsors`). See
[`docs/SPONSORS.md`](docs/SPONSORS.md).

- **Attendees** browse a tiered directory (Platinum / Gold / Silver / Bronze),
  open a **virtual booth** (about, promo video, brochure, team bios), and — with
  **explicit opt-in consent (DATA-06)** — share their contact details as a lead.
- **Organizers** (admin) create/manage sponsors, upload logos, read and export
  **leads** (PII, audit-logged), and see **analytics** — booth visits, unique
  visitors, leads, and conversion rate.
- Sponsor **logos surface across the app**: a rail on the home page and a
  "Sponsored by" band on the presenter/venue screen, driven by the public
  `GET /api/sponsors` list.

## Session Recordings

Consent-gated session video with searchable transcripts and slide-sync
playback at `/recordings` (feature flag `recordings`). See
[`docs/RECORDINGS.md`](docs/RECORDINGS.md).

- **Speaker consent gates everything** — the presenting speaker grants or
  denies; publishing without a grant is refused, and denying consent pulls a
  published recording immediately. Every decision is audit-logged.
- **Searchable transcripts with timestamps** — paste the platform's WebVTT/SRT
  export (voice tags and `Speaker:` prefixes become speaker labels); searching
  returns the exact second, and the player seeks straight there.
- **Slide-sync playback** — slide markers highlight the current slide as the
  video plays; tapping a slide or transcript line jumps the video.
- **Available 30 days post-event** (`RECORDING_RETENTION_DAYS`) — after that
  attendees no longer see it; organizers can extend the window.

## Knowledge Graph

A visual topic map linking talks, posters, papers and researchers at
`/knowledge` (feature flag `knowledge`), computed live from existing program
data — no extra data entry. See
[`docs/KNOWLEDGE_GRAPH.md`](docs/KNOWLEDGE_GRAPH.md).

- **Topic map** built from curated categories/interests, a domain phrase list
  (so "iPSC" and "induced pluripotent stem cells" are one node), and corpus
  keywords that appear in at least two items.
- **"If you liked A, B is relevant because…"** — related items each carry a
  plain-English reason and the shared topics.
- **Research threads** follow one topic across the days; **cross-pollination**
  surfaces adjacent topics you haven't engaged with and explains the link.
- **Trending topics** ranked by live Q&A and poster-comment volume.
- **Export your personal map as a PDF** (audit-logged).
- Privacy: only public program data — people appear if they present a session
  or opted into the directory (DATA-06); unaccepted papers and unapproved
  posters are excluded.

## AI Congress Companion

A congress assistant at `/companion` (feature flag `companion`) that answers in
natural language, nudges at the right moment, and suggests what to do next. See
[`docs/COMPANION.md`](docs/COMPANION.md).

- **Grounded, always available** — every answer is built from live congress
  data by a deterministic intent router: what's on now, what to see next, where
  a room is, the WiFi, CME progress, who works on a topic, food/pharmacy/ATM/
  transport, emergency numbers, recordings, posters, reviews.
- **Optional Claude integration** — free-form questions the router can't
  classify go to Claude (`COMPANION_MODEL`, default `claude-opus-5`) with the
  same facts as context and instructions never to invent program details. With
  no `ANTHROPIC_API_KEY` the companion runs fully offline; a failed call falls
  back to a rule-based answer rather than an error.
- **Proactive nudges** — bookmarked session starting soon (with its room), your
  talk in under two hours, a certificate one step away, unrated sessions,
  reviews owed, recording consent waiting, the post-event survey.
- **Energy-aware suggestions**, **serendipity mode** (a good thing outside your
  usual topics, with the connection explained), **speaker prep** (audience size,
  the questions already waiting, room and checklist) and **smart summaries**.

## Emoji Reactions

A reaction bar on the session page lets attendees tap 🔥 💡 🤔 👏 ❤️ during a
talk. Reactions are **ephemeral — nothing is written to the database**: the
client batches taps (~700ms) and POSTs counts; the server validates/clamps and
relays a "burst" over SSE. The **presenter view** aggregates a rolling 60-second
window and shows the live pulse — floating emojis plus a per-emoji tally — so
the speaker sees the room's energy without a single DB write. The attendee page
sends reactions only (it doesn't hold a reaction stream), keeping open
connections proportional to big screens, not the whole audience.

### Enabling Web Push

**Docker (automatic):** nothing to do. On first boot the container generates a
VAPID key pair and persists it to the `vapid_keys` volume, so push is enabled
out of the box and the public key stays stable across restarts. To reuse a
fixed pair across environments, set `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`
(and optionally `VAPID_SUBJECT`) in your `.env` and they take precedence.

**Local (manual):**

```bash
# 1. Generate a VAPID key pair
python gen_vapid_keys.py

# 2. Copy the printed VAPID_* lines into your .env (keep the private key secret)
# 3. Restart the app
```

With keys set, the browser registers `/static/sw.js` and subscribes once the
user grants notification permission (via Settings → notifications).

## Creating First Super Admin

### Option 1: Docker with `.env` file (recommended)

Add to your `.env` file:

```env
SUPER_ADMIN_EMAIL=admin@example.com
SUPER_ADMIN_PASSWORD=SecurePass123
SUPER_ADMIN_NAME=Admin Name
```

Then run `docker compose up -d`. The super admin is created/updated automatically on every startup. Check logs to confirm:

```bash
docker compose logs app | grep -i "super admin"
# Expected: "Created super admin: admin@example.com"
```

> **Note:** The entrypoint resets the super admin password on every startup to match the env var, so you can change the password by updating `.env` and restarting.

### Option 2: Interactive script (local development)

```bash
python seed_admin.py
```

Prompts for email, name, password, and institution interactively.

### Option 3: SQL (manual)

```sql
UPDATE users SET role = 'super_admin' WHERE email = 'your@email.com';
```

Once created, access `/admin` to manage all users from the dashboard.

## Role Permissions

| Action | Super Admin | Admin | Moderator & below |
|--------|:-----------:|:-----:|:-----------------:|
| View admin dashboard | Yes | Yes | No |
| Create users (attendee–moderator) | Yes | Yes | No |
| Create admin/super_admin users | Yes | No | No |
| Edit users below own role | Yes | Yes | No |
| Edit same-level or higher users | Yes | No | No |
| Change roles (attendee–moderator) | Yes | Yes | No |
| Assign admin/super_admin role | Yes | No | No |
| Suspend/activate lower users | Yes | Yes | No |
| Suspend same-level or higher | Yes | No | No |
| Delete users | Yes | No | No |
| Export CSV | Yes | Yes | No |
| View audit logs | Yes | Yes | No |

## Testing

```bash
# Install test dependencies
pip install pytest httpx

# Run all 124 tests
pytest tests/ -v
```

### Test Coverage

| File | Tests | Coverage |
|------|-------|---------|
| `test_auth.py` | 17 | Signup, login, logout, profile, password, login lockout |
| `test_admin.py` | 48 | User CRUD, RBAC (ROLE-01/02/04), suspend, bulk, export, audit |
| `test_security.py` | 38 | Password hashing, JWT, rate limiter, login tracker, RBAC helpers |
| `test_schemas.py` | 11 | Pydantic validation for all request schemas |
