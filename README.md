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
| **MOD-04** | Escalation: warn → 24h mute → suspend |
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
| GET | `/api/reactions/emojis` | Allowed reaction emojis | Any user |
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

Starts the full stack: **app + PostgreSQL + Redis + pgAdmin**.

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
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | JWT signing key (generate with `openssl rand -hex 32`) |
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
