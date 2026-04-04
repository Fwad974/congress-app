# Dubai Stem Cell Congress 2026 — Conference App

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

### Docker Deployment

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

## Creating First Super Admin

### Option 1: Docker (automatic)

Set `SUPER_ADMIN_EMAIL` and `SUPER_ADMIN_PASSWORD` environment variables when running the container. The super admin account is created automatically on startup.

### Option 2: Interactive script

```bash
python seed_admin.py
```

Prompts for email, name, password, and institution interactively.

### Option 3: SQL

```sql
UPDATE users SET role = 'super_admin' WHERE email = 'your@email.com';
```

Once created, access `/admin` to manage all users from the dashboard.

## Testing

```bash
# Install test dependencies
pip install pytest httpx

# Run all 118 tests
pytest tests/ -v
```

### Test Coverage

| File | Tests | Coverage |
|------|-------|---------|
| `test_auth.py` | 17 | Signup, login, logout, profile, password, login lockout |
| `test_admin.py` | 42 | User CRUD, RBAC (ROLE-01/02/04), suspend, bulk, export, audit |
| `test_security.py` | 38 | Password hashing, JWT, rate limiter, login tracker, RBAC helpers |
| `test_schemas.py` | 11 | Pydantic validation for all request schemas |
