# Content Moderation

Report + **auto-flag** → triage → resolve, an **upload-approval** workflow for
posters, and a **warn → 24h mute → suspend** escalation ladder for repeat
offenders. Sits on top of the existing **live** moderation (Q&A / polls) the
moderator role already had.

## Roles

| Who | Can |
|-----|-----|
| Any authenticated attendee | **Report** a Q&A question or poster comment |
| **Moderator** (`moderator` / `admin` / `super_admin`) | View the queue, remove content or dismiss reports; approve/reject pending posters; warn/mute/suspend users below their own level; moderate any session's Q&A/polls |

`admin_security.is_moderator(user)` is the gate; user-level actions additionally
require `can_manage_user(actor, target)` (you can only action users **below**
your role, never yourself).

## 1. Auto-flagging

New Q&A questions and poster comments are scanned by
`app/core/moderation_filter.py` (`scan(text)`) for **profanity**, **spam**
(promo phrases, shouting, character spam), and **external links**. A hit
auto-creates a **system** `ContentReport` (`source="auto"`, no reporter) so the
content still posts but surfaces in the moderator queue, badged **⚑
auto-flagged**. Auto-flags merge with human reports on the same item.

## 2. Report queue

1. An attendee taps **report**, or the filter auto-flags → a `ContentReport`.
2. The **Moderation** page (`/moderation`, nav link + live badge) shows the
   queue **grouped by content**: the text, its context, author, report count,
   auto-flag badge, and every reason — busiest first.
3. The moderator acts:
   - **Remove content** (optional reason) → deletes the question/comment,
     resolves every report on it (`content_remove` audit, reason logged).
   - **Dismiss reports** → content stays (`content_approve` audit).
   - **Action author…** → open the escalation panel (below).
   Reports whose content already vanished auto-resolve when the queue loads.

Content types are pluggable via `_RESOLVERS` in `app/api/moderation.py`
(`preview` + `remove` per type). Shipped: `question`, `poster_comment`.

## 3. Poster upload-approval workflow

When `POSTER_APPROVAL_REQUIRED=true`, posters uploaded by **non-organizers**
start `pending` and are hidden from the public gallery (visible only to their
owner, badged, and to moderators) until reviewed. Organizer uploads — and
everything when the flag is off — are `approved` immediately. Moderators see a
**Poster approvals** queue and approve/reject (reject takes a reason); the
author is notified either way. Poster `status` ∈ `approved | pending | rejected`.

## 4. Escalation ladder (MOD-04): warn → 24h mute → suspend

Each rung is recorded in `moderation_actions` with the moderator, a **required
reason**, and (for a mute) an expiry, then pushed to the target's notification
feed and audit-logged:

- **warn** — a recorded caution (`user_warn` audit).
- **mute** — a temporary posting ban: `users.muted_until = now + MUTE_HOURS`
  (default 24h). The user can browse/read but `ensure_can_post` returns **403**
  on Q&A questions, poster comments, poster creation, and reactions until it
  expires (`user_mute` audit).
- **suspend** — `is_active = False`. Suspended accounts are treated as logged
  out **everywhere immediately** (`get_current_user` returns 401 / pages
  redirect), not just blocked from posting (`user_suspend` audit).

`POST …/escalate` applies the **next** rung automatically
(`next_escalation`: warn if none prior → mute if warned → suspend if muted).
`POST …/action` applies a specific action, including the reversers **unmute**
and **unsuspend**.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /api/moderation/report` | any | `{content_type, content_id, reason?}` |
| `GET /api/moderation/reports` | moderator | Open queue (grouped, auto-flag badge, author id) |
| `GET /api/moderation/queue-count` | moderator | Open item count (nav badge) |
| `GET /api/moderation/stats` | moderator | open / resolved / dismissed / pending posters + by type |
| `POST /api/moderation/reports/{id}/resolve` | moderator | `{action: remove\|dismiss, reason?}` |
| `GET /api/moderation/posters` | moderator | Posters awaiting approval |
| `POST /api/moderation/posters/{id}/approve` · `/reject` | moderator | Approve / reject (reason) a poster |
| `GET /api/moderation/users/{id}/moderation` | moderator | A user's state + action history + next step |
| `POST /api/moderation/users/{id}/escalate` | moderator | Apply the next rung (`{reason}`) |
| `POST /api/moderation/users/{id}/action` | moderator | Apply a specific action (`{action, reason}`) |

## Files

| File | Role |
|------|------|
| `app/models/report.py` | `ContentReport` (+ `source` for auto-flags) |
| `app/models/moderation.py` | `ModerationAction` (escalation ledger) |
| `app/core/moderation_filter.py` | spam/profanity/link scanner |
| `app/core/moderation_service.py` | posting guard, escalation, auto-flag |
| `app/api/moderation.py` | report / queue / resolve / poster approval / escalation |
| `app/schemas/moderation.py` | request/response schemas |
| `app/templates/moderation.html` | `/moderation` dashboard |

Enforcement touch-points: `moderation_service.ensure_can_post` (Q&A, poster
comments, poster creation, reactions) and `security.get_current_user` (suspend).
