# Feature Completeness Review — Dubai Stem Cell Congress App

Date: 2026-07-24 · Branch: `claude/feature-completeness-review-gurl92`

This review covers **all roles, flows, and features**: the full automated test
suite, a live end-to-end smoke test of every major flow against a running
instance, and four systematic audits (RBAC, backend↔frontend wiring,
docs-vs-code, and flow completeness).

---

## 1. Verification performed

### Automated tests

```
447 passed, 0 failed (21 test files)
```

| File | Tests | File | Tests |
|------|------:|------|------:|
| test_admin.py | 50 | test_features.py | 17 |
| test_papers.py | 46 | test_schemas.py | 16 |
| test_security.py | 43 | test_qa.py | 15 |
| test_schedule.py | 35 | test_moderation.py | 14 |
| test_posters.py | 27 | test_review_chair.py | 13 |
| test_notifications.py | 26 | test_certificates.py | 13 |
| test_broadcasts.py | 24 | test_speaker.py | 11 |
| test_notes.py | 22 | test_oauth.py | 11 |
| test_auth.py | 21 | test_session_chair.py | 10 |
| test_polls.py | 17 | test_connect.py | 9 |
| | | test_reactions.py | 7 |

### Live smoke test (running instance, SQLite + conftest ARRAY shim)

All of the following were exercised against a booted server and behaved
correctly, including negative RBAC checks:

- Signup / login / cookie session; protected pages 302 → `/login` when logged out.
- Admin creates schedule item with presenter (`speaker_email`); attendee
  bookmarks; admin reschedules → bookmarker receives feed notification. ✔
- Presenter self-edits abstract/slides (`PUT /api/schedule/{id}/presentation`)
  and moderates own session's Q&A via API. ✔
- Q&A: ask, upvote, mark answered. Polls: create (chair), open, vote, results
  aggregate. Reactions: batched POST accepted. ✔
- Notes CRUD linked to a session. ✔
- Attendance: session QR → `attend_code` → check-in → certificate progress
  updates (`/api/certificates/status`). ✔
- Posters: attendee create correctly 403; organizer create, vote, comment,
  hunt code issued. ✔
- Moderation: report poster comment → appears in queue with preview →
  moderator removes → content deleted, report resolved. ✔
- Papers: submit → chair assigns reviewer (blind: authors hidden) → reviewer
  scores → chair decision `accept` → author notified in feed → paper appears
  in `/api/papers/accepted` proceedings. ✔
- Broadcast announcement lands in attendee feed. ✔
- Feature flags: disabling `papers` → API 403, page 302; re-enable restores. ✔
- RBAC negatives: attendee blocked (403) from all `/api/admin/*` and
  moderation queue; `queue-count` safely returns `{"count": 0}` for
  non-moderators (nav badge, no data leak). ✔

**Bottom line: what is built works.** The findings below are about what is
*advertised but not built*, *built but not reachable*, or *inconsistent
between layers*.

---

## 2. Confirmed defects (severity-ordered)

### P1 — Suspended users keep full access until their token expires

Verified live: after `POST /api/admin/users/1/suspend`, the suspended user's
existing session still posted a Q&A question (**201**) and read `/api/auth/me`
(**200**). Only fresh logins are blocked (403). With 24 h tokens
(`ACCESS_TOKEN_EXPIRE_MINUTES=1440`), a suspended user retains up to a day of
full write access.

- Cause: `get_current_user` (`app/core/security.py`) never checks
  `user.is_active`.
- Fix: reject inactive users in `get_current_user` (one check covers every
  endpoint).

### P1 — Three advertised security rules are not implemented

The README "Implementation" table (README.md:14–19) and the
`app/api/admin.py` docstring claim controls that do not exist:

| Claim | Reality |
|-------|---------|
| **AUTH-01** "2FA flagged for admin roles" | `two_factor_enabled` column (`user.py:42`) and `ADMIN_ROLES` set (`admin_security.py:47`) exist but are consumed nowhere. No TOTP, no enrollment, no login check. |
| **AUTH-02** "Admin session timeout 30 min" | `SESSION_TIMEOUT` dict (`admin_security.py:51`) is dead code. Everyone gets the same 24 h token and 24 h cookie (`auth.py:39-42,70-73`). |
| **MOD-04** "Escalation warn → 24h mute → suspend" | No warn, no mute anywhere in the codebase. Only binary suspend exists, and it is not linked to the moderation queue. |

Either implement them or remove the claims — as written the README
misrepresents the security posture.

### P2 — Role vs. assignment mismatch breaks the presenter/chair UI

Backend session-moderation permission is **assignment-based**
(`can_moderate_session`, `admin_security.py:224-233`: live-moderator role OR
the row's `speaker_id`/`chair_id`). The Q&A page gate is **role-based**
(`pages.py:113-116`). The two disagree in both directions:

- **A presenter with role `speaker`/`attendee` sees no moderation controls,
  no poll-creation form, and no "Present ↗" link** on their own session
  (`qa.html` hides them when `can_moderate` is false) — even though every
  corresponding API call would succeed. `present.html` is display-only, so
  there is **no UI path at all** for a role-`speaker` presenter to create or
  open a poll.
- **An unassigned `session_chair` or `review_chair` sees all the controls**,
  but every moderation/poll call returns 403 (`qa.py:213`, `polls.py:54`).

Fix: compute the page flag with the same `can_moderate_session` helper the
API uses.

### P2 — `session_chair` is effectively a dead role

No endpoint grants anything by the `session_chair` role; moderation flows
entirely through per-session `chair_id` assignment, and `chair_id` is set
from an arbitrary email **with no role check** (`schedule.py:33-41`). The
role's only real effect is poster-creation rights (`posters.py:34`). Docs and
README ("Chair+") imply the role itself carries session powers — it doesn't.

### P3 — Feature-flag and gating inconsistencies

- `polls` flag gates the API but has **no page-level gate**; polls UI lives in
  `qa.html` (gated by `qa` flag only), so disabling `polls` alone hides nothing.
- `/present/{session_id}` (`pages.py:123-129`) has **no feature gate and no
  authorization beyond login** — any attendee can open any session's big-screen
  view.
- **Reactions** have no feature flag and no `require_feature` on the router,
  unlike sibling qa/polls.
- `is_enabled()` **fails open** for unknown keys (`feature_flags.py:57-58`) —
  a typo'd key silently enables a feature.

### P3 — Built backend capabilities with no UI (orphaned endpoints)

| Endpoint | Impact |
|----------|--------|
| `DELETE /api/auth/account` | Account self-deletion exists but is unreachable — no button anywhere. (GDPR-relevant.) |
| `POST /api/papers/{id}/withdraw` | Authors cannot withdraw a submission from the UI (statuses/CSS for "withdrawn" already exist in `papers.html`). |
| `POST /api/notifications/push/unsubscribe` | Users can never turn push off once enabled; `sw.js` also lacks a `pushsubscriptionchange` handler (breaks on key rotation). |
| `GET /api/auth/oauth/providers` | Never called — login/signup hardcode Google + ORCID buttons, so a disabled provider still shows its button. |
| `GET /api/auth/me`, `GET/POST /api/auth/logout` (JSON), `GET /api/papers/{id}`, `GET /api/polls/{id}` | Dead code; harmless but unmaintained surface. |

*(Zero broken frontend→backend calls were found — all 120+ UI call sites
resolve to real routes with matching methods.)*

### P3 — Portability: app boots only on PostgreSQL

`users.research_interests = Column(ARRAY(String))` (`user.py:31`) cannot be
created on SQLite; tests only pass because `tests/conftest.py:47` patches the
column type. Fine if Postgres-only is intended, but worth a note — a
JSON-typed column would remove the divergence between test and runtime
schemas.

---

## 3. Flow-by-flow completeness

| # | Flow | Verdict |
|---|------|---------|
| 1 | **Attendee lifecycle** (signup → schedule → bookmark → notify → check-in → live session → notes → certificate) | **Works end-to-end.** Thin spots: certificate is print-to-PDF only, and `attend_code` is a shared session-wide secret (forwardable; no time-window check against `start_time`/`end_time`). |
| 2 | **Speaker/presenter** | **Breaks at the UI** (P2 above). Also: presenters are never notified of their assignment — no feed entry when `speaker_id`/`chair_id` is set. |
| 3 | **Paper author** (submit → review → decision → revision ≤ 2 rounds) | **Works through decision**, well-guarded (COI override, blind review, notifications). **Dead-ends after acceptance**: no link to the schedule, no speaker certificate, no camera-ready step — accepted papers only surface as read-only proceedings. |
| 4 | **Posters** (create → browse → vote → comment → hunt) | **Works.** Missing: judging/awards (only popular vote), attendee self-service poster creation, hunt completion has no reward hook, poster presenters earn no certificate. |
| 5 | **Moderation** (report → queue → remove/dismiss) | **Works**, audit-logged. Missing the back half: content author is never notified, reporter gets no closure, no appeal state, no path from queue to user-level action (warn/mute/suspend). Only questions and poster comments are reportable — not notes, profiles, or word-cloud words. |
| 6 | **Connect / networking** | **Directory only.** Opt-in publishes the full bundle (name, interests, **email**) to every logged-in attendee via `mailto:` — no request/accept flow, no granular privacy, no saved connections. |
| 7 | **Certificates** | **Self-serve computation works** (attendance / CME / speaker). No admin issuance or override, no revocation, **no verification artifact** (no serial, no QR, no `/verify` endpoint), and `issued_on` re-stamps as "now" on every view (`pages.py:199`). |

Cross-cutting root cause for flows 2/3/4/7: **role (`UserRole`) and
assignment (FKs: `speaker_id`, `chair_id`, `presenter_id`, paper acceptance)
are two disconnected systems.** APIs authorize by assignment; page gates and
certificate logic key on role. Unifying "is this user a presenter of
anything?" closes several gaps at once.

---

## 4. Opportunities (prioritized roadmap)

**Quick wins (small diffs, high value)**
1. Check `is_active` in `get_current_user` (fixes the P1).
2. Use `can_moderate_session` for the Q&A page's `can_moderate` flag.
3. Notify presenters/chairs when assigned to a session.
4. Notify content authors (and reporters) when a report is resolved.
5. Wire the existing withdraw / account-delete / push-unsubscribe endpoints
   into the UI; drive OAuth buttons from `/api/auth/oauth/providers`.
6. Stamp a stable `issued_on` (first-unlock date) and a serial on certificates.

**Medium**
7. Real escalation ladder: warn (notification) → timed mute (enforced at
   post endpoints) → suspend, actionable from the moderation queue — this is
   what MOD-04 already promises.
8. Certificate verification: persist issued certificates with serial + public
   `/verify/{serial}` page + QR on the printout.
9. Time-window attendance check-in (reject check-ins outside session hours).
10. "Promote accepted paper to schedule slot" admin action (sets
    `speaker_id`, which also unlocks the speaker certificate).
11. Poll controls on `/present/{id}` for the assigned presenter/chair.

**Larger**
12. Connect v2: connection request/accept with per-connection contact
    exchange instead of publishing email to all attendees.
13. Poster judging (judge assignments, rubric scores, awards) + hunt reward.
14. Real 2FA (TOTP) for admin roles, or drop the AUTH-01 claim.
15. Role-differentiated token lifetime (30 min admin), or drop AUTH-02.

---

## 5. Documentation drift to fix

- ~~README claims **124 tests / 4 files**; actual **447 tests / 21 files**~~
  — **fixed**: the README table now lists every test file with authoritative
  counts (**717 tests / 29 files** as of the Recordings / Knowledge Graph /
  Companion release).
- README API table omits entire shipped routers: certificates/attendance,
  posters, moderation, connect, notification feed/push, feature flags.
- README role-permission table covers only admin-dashboard roles; the four
  mid-tier roles (speaker, reviewer, session_chair, review_chair) with real
  capabilities are absent. "Chair+" rows are inaccurate (see P2).
- "8 levels" → 8 roles, **6 distinct levels** (review_chair = session_chair
  = 50, speaker = reviewer = 20).
- Connect feature has no docs at all (no `docs/CONNECT.md`, no README section).
- `docs/FEATURE_RELEASES.md` omits `connect` from its enforcement list even
  though the code gates it.
- Dead code to prune: `ADMIN_ROLES`, `SESSION_TIMEOUT`, `require_moderator`
  (`admin_security.py`).
