# Social Login (OAuth / OIDC)

Sign in / sign up with **Google** or **ORCID** alongside email + password,
using the OAuth 2.0 Authorization Code flow (OpenID Connect). Built on
[Authlib](https://authlib.org); the OAuth step just establishes the same
`access_token` cookie the app already uses.

## Flow

1. User clicks **Google** / **ORCID** → `GET /api/auth/oauth/{provider}/login`
   redirects to the provider (state + nonce stored in a signed session cookie).
2. Provider redirects back to `/api/auth/oauth/{provider}/callback`.
3. Server exchanges the code, validates the ID token, reads the profile
   (`sub`, `email`, `email_verified`, `name`, `picture`).
4. `upsert_oauth_user()` finds or creates the local user and sets the cookie;
   the user lands on `/home`.

## Configuration

| Env var | Purpose |
|---------|---------|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Enable Google when both are set |
| `ORCID_CLIENT_ID` / `ORCID_CLIENT_SECRET` | Enable ORCID when both are set |
| `ORCID_ENV` | `sandbox` (default) or `production` |
| `OAUTH_REDIRECT_BASE` | Public origin for callbacks behind a proxy; empty = derive from request |

Each provider activates only when both its id and secret are set
(`enabled_providers()`), and the login/signup pages render a button only for
enabled providers.

### Google

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services →
   Credentials → **Create OAuth client ID** (Web application).
2. Authorized redirect URI:
   `https://YOUR_ORIGIN/api/auth/oauth/google/callback`
   (for local dev, `http://localhost:8000/api/auth/oauth/google/callback`).
3. Copy the client id/secret into `.env`.

Scopes: `openid email profile`. Google returns a verified email, so Google
users link by email and are marked verified.

### ORCID

1. Register at [ORCID Developer Tools](https://orcid.org/developer-tools)
   (start with the **sandbox** at `sandbox.orcid.org`).
2. Redirect URI: `https://YOUR_ORIGIN/api/auth/oauth/orcid/callback`.
3. Set `ORCID_CLIENT_ID/SECRET`; keep `ORCID_ENV=sandbox` until you go live,
   then switch to `production`.

Scope: `openid` — the ID token's `sub` is the ORCID iD (stored on
`user.orcid_id`). ORCID often does **not** share an email; when it doesn't, a
placeholder `sub@orcid.local` email is used and the account is keyed by ORCID
iD rather than linked by email.

## Account linking

Policy: **link by verified email**.

1. If the provider account is already linked (`oauth_accounts` row) → that user.
2. Else if the provider gives a **verified** email matching an existing user →
   log into that user and record the link (password login is preserved).
3. Else create a fresh attendee account keyed by the provider's `sub`.

Because links live in a separate `oauth_accounts` table, one user can attach
both Google and ORCID and still keep a password.

## Security notes

- State + nonce are stored in a signed session cookie (`SessionMiddleware`,
  keyed by `SECRET_KEY`); set `https_only=True` on that middleware in
  production.
- ID tokens are validated against the provider's JWKS (via Authlib OIDC
  discovery).
- `users.hashed_password` is nullable for OAuth-only accounts; password login
  is refused when no password is set.
- Only **verified** emails link to existing accounts, preventing account
  takeover via an unverified email claim.

## Files

| File | Role |
|------|------|
| `app/core/oauth.py` | Authlib registry, provider gating, `upsert_oauth_user()` |
| `app/api/auth.py` | `/oauth/providers`, `/oauth/{provider}/login`, `/callback` |
| `app/models/oauth.py` | `OAuthAccount` link table |
| `app/templates/login.html`, `signup.html` | provider buttons |
