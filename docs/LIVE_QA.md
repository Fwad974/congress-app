# Live Q&A

Attendees ask and upvote questions during a session; chairs/moderators curate
them; everyone's view stays live via Server-Sent Events.

## Concepts

- A **session** is a `ScheduleItem`. Q&A is scoped per session.
- A **Question** belongs to a session and an author, has a `status`
  (`open` / `answered` / `hidden`) and a denormalized `upvotes` count.
- A **QuestionVote** is one upvote by one user (unique per question+user); the
  count is recomputed from these rows on every change so it can't drift.

## Roles

| Action | Who |
|--------|-----|
| Ask, upvote, list | any authenticated user |
| Delete own question | the author |
| Mark answered / hide / reopen, delete any | session_chair, review_chair, moderator, admin, super_admin |

Moderation actions are written to the audit log as `question_moderate`.
Attendees never receive `hidden` questions; moderators can opt to see them
("show hidden" toggle on the page).

## Realtime layer

`app/core/realtime.py` exposes a single `broadcaster`:

- `await broadcaster.publish(channel, data)` — publish a JSON message.
- `async with broadcaster.subscribe(channel) as queue:` — receive messages.

Channel for a session's Q&A: `rt:qa:{session_id}` (see `qa_channel()`).

Two modes, identical API:

- **Redis** (`REDIS_URL` set): publishes go to Redis pub/sub; a reader task
  fans messages back to local subscribers in every worker/container.
- **In-process** (default / `REDIS_URL` empty): direct local delivery. Fine for
  a single worker and for tests.

The broadcaster connects in the app lifespan (`main.py`) and degrades to
in-process automatically if Redis is unreachable.

### SSE stream

`GET /api/sessions/{id}/qa/stream` returns a `text/event-stream`. Each event is
`data: <json>\n\n`; comment lines (`: keepalive`) are sent every ~15s to keep
the connection warm. Event payloads:

```jsonc
{ "type": "created", "question": { ...QuestionResponse... } }
{ "type": "updated", "question": { ... } }   // new upvote count or status
{ "type": "deleted", "id": 42, "session_id": 7 }
```

`has_upvoted`/`is_mine` are per-user and are NOT included in broadcasts; the
client tracks its own upvote set and merges by question id.

## API

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/sessions/{id}/questions` | sorted: open→answered, then upvotes desc, newest |
| POST | `/api/sessions/{id}/questions` | body `{text}`; 2s per-user cooldown |
| GET | `/api/sessions/{id}/qa/stream` | SSE |
| POST | `/api/questions/{id}/upvote` | idempotent |
| DELETE | `/api/questions/{id}/upvote` | |
| PUT | `/api/questions/{id}/status` | body `{status}`; chair+ |
| DELETE | `/api/questions/{id}` | author or chair+ |

## Notes & limits

- Submissions are rate-limited per user (`_SUBMIT_COOLDOWN`, default 2s) to
  curb spam; questions cap at 500 chars.
- Upvote counts are authoritative server-side (recounted), so optimistic client
  updates always reconcile.
- Scaling: with more than one worker/container, set `REDIS_URL` or clients on
  different workers won't see each other's events.

## Files

| File | Role |
|------|------|
| `app/models/qa.py` | `Question`, `QuestionVote`, `QuestionStatus` |
| `app/schemas/qa.py` | request/response schemas |
| `app/api/qa.py` | REST + SSE endpoints |
| `app/core/realtime.py` | broadcaster (Redis / in-process) |
| `app/templates/qa.html` | live Q&A page |
