# My Notes

Private, per-attendee note-taking, optionally linked to a schedule session.

- Notes are visible only to their author; ownership is enforced on every query.
- A note can be linked to a `schedule_item_id` so it shows in context on the
  session, or kept as a free-standing note.
- Gated by the `notes` feature flag (API router + page + nav link).
- Super Admins can review/delete notes for moderation via the admin dashboard
  (`GET /api/admin/notes`, `DELETE /api/admin/notes/{id}`), which is audit-logged.

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/notes` (optional `?schedule_item_id=`) | List my notes. |
| POST | `/api/notes` | Create a note (optionally linked to a session). |
| PUT | `/api/notes/{id}` | Update my note. |
| DELETE | `/api/notes/{id}` | Delete my note. |

All endpoints require authentication and operate only on the caller's own notes.
