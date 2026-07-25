# Feature Releases (admin control)

The **Releases** tab in the admin dashboard (`/admin`) lets an admin decide which
optional modules are live for attendees — a set of runtime **feature flags**. Flip
a module on to release it; flip it off to hide it while you finish preparing it.

## Behaviour

- **On (Live)** — the module's nav links show and its pages/APIs work for everyone.
- **Off (Hidden)** — nav links disappear, the pages redirect to Home, and the APIs
  return `403` for attendees. **Admins/super-admins can still preview** a hidden
  module so they can check it before release.
- Changes take effect immediately (an in-process cache is updated on every toggle),
  and each toggle is **audit-logged** (`feature_toggle`).

## Toggleable modules

| Key | Module |
|-----|--------|
| `papers` | Abstracts & Papers (submission, review, accepted showcase) |
| `posters` | Poster Gallery (voting, comments, scavenger hunt) |
| `notes` | My Notes |
| `qa` | Live Q&A |
| `polls` | Live Polls & word clouds (gates the API and the Polls tab) |
| `reactions` | Live emoji reactions during sessions |
| `connect` | Networking Directory (opt-in attendee directory) |
| `sponsors` | Sponsors & Exhibitors portal |
| `feedback` | Feedback & Ratings (session ratings + post-event survey) |
| `venue` | Venue & Dubai Info (floor plan, navigation, WiFi) |
| `recordings` | Session Recordings (consent-gated, transcripts) |
| `knowledge` | Knowledge Graph (topic map) |
| `companion` | AI Congress Companion |

All default to **on**, so existing behaviour is unchanged until an admin hides one.
Unknown keys fail open (never accidentally hidden).

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `GET /api/admin/features` | admin | List modules + on/off state |
| `PUT /api/admin/features/{key}` | admin | `{ "enabled": true\|false }` |

## Enforcement points

- **Nav + home** — `feature_enabled(key)` is a Jinja global; links in `nav.html`
  (top bar, dropdown, mobile menus) and the home page's quick actions / dashboard
  cards are wrapped in `{% if feature_enabled('…') %}`. The schedule page also
  hides its per-session Q&A / Notes buttons when those flags are off.
- **Pages** — `pages._feature_blocked(user, key)` redirects non-admins to `/home`
  (`/papers`, `/posters`, `/notes`, `/qa/{id}`, `/connect`, `/sponsors`,
  `/feedback`, `/venue`, `/recordings`, `/knowledge`, `/companion`).
- **APIs** — `feature_flags.require_feature(key)` is a router dependency on the
  papers, posters, notes, qa, polls, connect, sponsors, feedback, venue,
  recordings, knowledge, and companion routers (403 for non-admins when off).

## Files

| File | Role |
|------|------|
| `app/models/feature_flag.py` | `FeatureFlag` table |
| `app/core/feature_flags.py` | registry, cache, `is_enabled` / `set_enabled` / `require_feature` |
| `app/api/admin.py` | `GET/PUT /api/admin/features` |
| `app/templates/admin_dashboard.html` | Releases tab UI |
