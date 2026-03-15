# Dubai Stem Cell Congress 2026 — Conference App

FastAPI + PostgreSQL web application for the Dubai Stem Cell Congress.

## Architecture

```
dubai-congress/
├── app/
│   ├── api/
│   │   ├── auth.py          # POST /api/auth/signup, /login, /logout, /me
│   │   └── pages.py         # GET /, /signup, /login, /home
│   ├── core/
│   │   ├── config.py        # Environment settings (Pydantic)
│   │   ├── database.py      # SQLAlchemy engine & session
│   │   └── security.py      # Password hashing, JWT, auth dependencies
│   ├── models/
│   │   └── user.py          # User SQLAlchemy model (roles, profile, interests)
│   ├── schemas/
│   │   └── user.py          # Pydantic request/response schemas
│   ├── templates/            # Jinja2 HTML templates
│   │   ├── base.html
│   │   ├── landing.html
│   │   ├── signup.html
│   │   ├── login.html
│   │   └── home.html
│   ├── static/
│   │   ├── css/main.css
│   │   └── js/main.js
│   └── main.py              # FastAPI app entry point
├── .env                      # Environment config
├── requirements.txt
└── README.md
```

## Setup

### 1. PostgreSQL Database

```bash
# Create database and user
sudo -u postgres psql
CREATE USER congress_user WITH PASSWORD 'congress_pass';
CREATE DATABASE dubai_congress OWNER congress_user;
\q
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Edit `.env` with your database URL and generate a proper secret key:

```bash
openssl rand -hex 32    # Generate SECRET_KEY
```

### 4. Run the Server

```bash
cd dubai-congress
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Open in Browser

- **Landing page:** http://localhost:8000
- **Sign up:** http://localhost:8000/signup
- **Sign in:** http://localhost:8000/login
- **Dashboard:** http://localhost:8000/home (requires login)

## API Endpoints

| Method | Endpoint          | Description                 |
|--------|-------------------|-----------------------------|
| POST   | /api/auth/signup  | Create account              |
| POST   | /api/auth/login   | Sign in (sets cookie)       |
| POST   | /api/auth/logout  | Clear auth cookie           |
| GET    | /api/auth/me      | Get current user profile    |

## User Roles

- `attendee` (default)
- `speaker`
- `reviewer`
- `session_chair`
- `review_chair`
- `moderator`
- `admin`
- `super_admin`

## Features Implemented

- [x] Landing page with congress info and CTAs
- [x] User registration with research interests
- [x] Login with JWT cookie auth
- [x] Password validation (8+ chars, uppercase, number)
- [x] Post-login personalized dashboard
- [x] Role-based user model (8 roles from spec)
- [x] Auto-redirect (logged-in users skip landing/login)
- [x] Toast notifications for success/error feedback
- [x] Responsive dark biotech design
- [x] SSO placeholders (Google, ORCID)

## Next Steps

- Paper submission & review workflow
- Session schedule CRUD
- AI matchmaking engine
- Live Q&A / polling during sessions
- Poster gallery with voting
- Push notifications
- Admin dashboard & permissions
- CME certificate generation
