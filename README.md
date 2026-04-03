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
dubai-congress/
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
├── .env
└── requirements.txt
```

## Setup

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
cd dubai-congress
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 5. Open browser
# Landing: http://localhost:8000
# Admin:   http://localhost:8000/admin (requires admin role)
```

## Creating First Admin

After signup, manually promote a user to super_admin in the database:

```sql
UPDATE users SET role = 'super_admin' WHERE email = 'your@email.com';
```

Then access `/admin` to manage all other users from the dashboard.
