# Content Moderation

A report → triage → resolve loop so moderators can keep user-generated content
clean. Sits on top of the existing **live** moderation (Q&A / polls) the
moderator role already had.

## Roles

| Who | Can |
|-----|-----|
| Any authenticated attendee | **Report** a Q&A question or poster comment |
| **Moderator** (`moderator` / `admin` / `super_admin`) | View the queue, **remove** content or **dismiss** reports; delete any poster comment; moderate any session's Q&A/polls |

`admin_security.is_moderator(user)` is the gate (`MODERATOR_ROLES`).

## Flow

1. An attendee taps **report** on a poster comment or Q&A question and (optionally)
   gives a reason → `POST /api/moderation/report` creates a `ContentReport`
   (one per user per item — re-reporting is a no-op).
2. The **Moderation** page (`/moderation`, nav link + live queue badge for
   moderators) shows the queue **grouped by content**: the offending text, its
   context (session / poster), the author, the number of reports, and every
   reason given — busiest first.
3. The moderator acts:
   - **Remove content** → deletes the underlying question/comment and marks
     every report on it `resolved` (`content_remove` audit).
   - **Dismiss reports** → content stays, reports marked `dismissed`
     (`content_approve` audit).
   Reports whose content already vanished are auto-resolved when the queue loads.

## Content types

Pluggable via `_RESOLVERS` in `app/api/moderation.py` — each type provides a
`preview(db, id)` and `remove(db, id)`. Shipped: `question`, `poster_comment`.
Adding another reportable type is one dict entry.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /api/moderation/report` | any | `{content_type, content_id, reason?}` |
| `GET /api/moderation/reports` | moderator | Open queue (grouped) |
| `GET /api/moderation/queue-count` | moderator | Open item count (nav badge) |
| `GET /api/moderation/stats` | moderator | open / resolved / dismissed + by type |
| `POST /api/moderation/reports/{id}/resolve` | moderator | `{action: remove\|dismiss}` |

## Files

| File | Role |
|------|------|
| `app/models/report.py` | `ContentReport` |
| `app/api/moderation.py` | report / queue / resolve + content resolvers |
| `app/schemas/moderation.py` | request/response schemas |
| `app/templates/moderation.html` | `/moderation` queue dashboard |
