# Live Polls & Word Clouds

Real-time polls run during a session, alongside Live Q&A on `/qa/{session_id}`
and on the big-screen `/present/{session_id}` view.

- Poll types: single choice, multiple choice, and **word cloud** (free text).
- Created and opened/closed by the session's **presenter or chair** (assignment
  based — same `can_moderate_session` check the Q&A moderation uses) or a global
  moderator/admin.
- Results stream live over SSE to every viewer.
- Gated by the `polls` feature flag: the flag gates the API router **and** the
  Polls tab in `qa.html`, so disabling it hides the whole feature.
- Word-cloud submissions are free text shown on the big screen, so they are
  subject to the MOD-04 mute guard; anonymous option votes are not.

## Endpoints

| Method | Endpoint | Description | Who |
|--------|----------|-------------|-----|
| POST | `/api/sessions/{id}/polls` | Create a poll (draft). | Presenter / chair / mod |
| GET | `/api/sessions/{id}/polls` | List a session's polls. | Any user |
| GET | `/api/sessions/{id}/polls/stream` | Live poll event stream (SSE). | Any user |
| POST | `/api/polls/{id}/vote` | Vote / submit a word. | Any user |
| GET | `/api/polls/{id}/results` | Aggregated results. | Any user |
| PUT | `/api/polls/{id}/status` | Open / close a poll. | Presenter / chair / mod |
| DELETE | `/api/polls/{id}` | Delete a poll. | Presenter / chair / mod |

Results are always recomputed from the stored responses, and word-cloud
submissions are capped per user.
