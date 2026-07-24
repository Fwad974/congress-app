# Application Review — Features, UI, UX, Completeness, Stability

Date: 2026-07-24 · Branch: `claude/app-review-features-ux-sbob9a`
Scope: whole application — 14,289 lines of Python, 5,470 lines of templates,
760 lines of CSS/JS, 7,604 lines of tests, 15 docs files.

**Note on UI findings: no colour or palette changes are proposed anywhere in
this document.** Contrast items are reported only where a value is objectively
broken (e.g. a background token used as text colour, or an undefined variable);
the fix in each case is to use the palette correctly, not to re-theme it.

---

## 1. How this review was carried out

| Method | What it covered |
|---|---|
| Full test suite | `752 passed` in 12m53s (30 files) — zero failures |
| Live instance | App booted on SQLite with seeded users (attendee / speaker / super-admin) and a 3-session program |
| Browser sweep | 33 full-page screenshots via Playwright/Chromium — every page, as attendee + admin, desktop (1440px) + mobile (390px) |
| Runtime probes | Auth, RBAC, IDOR, XSS, CSV injection, uploads, races, SSE, DB pool, lockout, feature flags |
| Static review | 8 parallel dimension reviewers (UI, UX, a11y/mobile/i18n, JS, two API halves, core/DB, completeness) |
| Adversarial verification | Every P1/P2 claim re-checked by an independent verifier instructed to refute it |

199 raw findings → 92 P1/P2 verified (85 confirmed, 7 severity-adjusted) →
plus 107 P3. Eight claims were **refuted by live testing** and are listed in
§7 so they are not re-raised.

**Headline: the app is feature-rich and the backend is largely sound. Two
defects are release-blocking, and the biggest systemic gaps are accessibility
and design-system duplication.**

---

## 2. P1 — Release blockers

### P1-1 · Stored XSS: attendee → admin session takeover

`esc()` (copy-pasted into 16 templates, e.g. `admin_dashboard.html:303`) is
`d.textContent = s; return d.innerHTML` — it escapes `&`, `<`, `>` but **not
`"`**. At `admin_dashboard.html:404` the result is interpolated into a
**double-quoted** `onclick` attribute:

```js
`<div class="bc-ur-item" onclick="addPerson(${u.id},'${esc(u.full_name).replace(/'/g,"\\'")}')">`
```

Single quotes are escaped; double quotes are not.

**Verified end-to-end on the running app:**

1. Attendee registers with `full_name` = `x" onmouseover="window.__pwned=1" y="` → **HTTP 201**
2. Value is stored and returned verbatim by `/api/admin/users?search=`
3. Admin opens Broadcast tab → types the name → rendered markup:
   `<div class="bc-ur-item" onclick="addPerson(5,'x" onmouseover="window.__pwned=1" y="')">`
4. Hovering the row → **attacker's JavaScript executed in the admin's authenticated session**

The same `esc()`-into-attribute pattern appears in `posters.html:171`,
`papers.html:135/254/369`, and `recordings.html` — where it also fails to
validate the URL scheme, so a stored `javascript:` URL executes on click.

**Fix:** make `esc()` escape `"` and `'` as well, add a separate `escAttr()` /
`safeUrl()` for attribute and href contexts, and prefer `addEventListener` +
`dataset` over building `onclick` strings.

### P1-2 · Organizers cannot manage recordings at all

`recordings.html:229`:

```js
try{sessions=await api('/api/schedule');}catch(e){}
const free=(sessions||[]).filter(s=>!taken.has(s.id));
```

`GET /api/schedule` returns `{items:[…], total:n}` (verified live), not an
array. `.filter` is not a function on an object, and the throw is **outside**
the `try`, so `loadManage()` aborts.

**Verified in-browser as super-admin:** opening the Manage tab logs
`(sessions || []).filter is not a function`; the session dropdown has **0
options** and `manageList` renders **0 bytes**. The entire organizer half of
the Recordings feature — create, publish, upload transcript, add slides — is
unreachable through the UI.

**Fix:** `sessions = (await api('/api/schedule')).items || []`.

---

## 3. P2 — Significant

### 3.1 Security & correctness

| # | Finding | Evidence |
|---|---|---|
| 1 | **`.env` committed to git** with `SECRET_KEY` + DB credentials; `.gitignore` does not exclude it. `ENVIRONMENT` defaults to `development`, so a bare-metal deploy runs on the public signing key. | `git ls-files .env` → tracked; `.env:5` |
| 2 | **Admin CSV export lacks formula-injection escaping** that every sibling export has (`certificates.py:51`, `feedback.py:226`, `sponsors.py:312`). Verified: registered `=cmd\|'/c calc'!A1` → appears verbatim in the exported CSV. | `admin.py:472-478` |
| 3 | **Remote lockout DoS**: 10 bad passwords permanently locks *any* account, unlock is manual-only. Trivially targets the super-admin. Tracker is per-process in-memory. | `auth.py:55`, `admin_security.py:94-134` |
| 4 | **Timed suspension is fake**: `duration_hours` is accepted and audit-logged, but no expiry column exists — verified, the user row shows only `is_active:false`. Every suspension is permanent. | `admin.py:319` |
| 5 | **`bulk_action` lies**: unknown actions (including the documented `delete`) return `{"success":1,"failed":0}` while doing nothing. Verified live. | `admin.py:445` |
| 6 | **`sort_by` is an unvalidated `getattr`** — `sort_by=hashed_password` returns 200 (sorts by password hash); `sort_by=__class__` returns 500. | `admin.py:137` |
| 7 | **Poster image endpoint bypasses the approval gate** — pending/rejected poster files are downloadable by id enumeration. | `posters.py:363` |
| 8 | **Auth cookie never sets `Secure`**; `https_only=False` is hardcoded with a "set True in production" comment nothing enforces (unlike `SECRET_KEY`, which *is* enforced at boot). Cookie `max_age` is hardcoded 86400 and ignores `ACCESS_TOKEN_EXPIRE_MINUTES`. | `auth.py:39,70,158`, `main.py:370` |
| 9 | **`--forwarded-allow-ips "*"`** trusts `X-Forwarded-For` from anyone while compose publishes the port directly — client IP fully spoofable, and audit IPs derive from it. | `Dockerfile:36` |
| 10 | **Check-in race → 500.** Concurrent check-ins hit the unique constraint uncaught. Verified: 3 parallel requests → one **HTTP 500**. An attendee double-tapping at the door gets an error page. | `certificates.py:196-201` |
| 11 | **Uploads buffered fully before the size check.** Verified: a 60 MB POST is accepted end-to-end (`uploaded=60000202`) before 413. No `Content-Length` pre-check. | `papers.py:456-461`, `posters.py:346`, `sponsors.py:360` |
| 12 | **`change_password` 500s for OAuth-only accounts** (`hashed_password` is None). | `auth.py:184` |
| 13 | **No LLM timeout.** No `timeout=` on any provider client; Anthropic's SDK default is 600 s, and `/api/companion/ask` is sync-def, so hung calls pin threadpool workers. No rate limit on the endpoint either. | `llm.py:155,182,224`, `companion.py:59` |
| 14 | **Connect directory is unpaginated and exposes every opted-in email** in one response — one request scrapes all attendee PII. | `connect.py:29,49,68` |

### 3.2 Stability & scale

- **No migration story.** `create_all` + 7 hand-written `_migrate_*` functions
  (`main.py:91-268`, Postgres-only). `alembic==1.13.2` is in requirements but
  there is no `alembic.ini` or `migrations/`. `_sync_pg_enum` covers 3 of 6
  enum types, so adding a `UserRole`/`PaperStatus`/`QuestionStatus` member
  breaks existing production databases.
- **DB pool is unconfigured** (`database.py:7`) → SQLAlchemy defaults of
  5 + 10 overflow = **15 connections**, no `pool_pre_ping`, no `pool_recycle`.
  Verified: with a deliberately tiny pool, a burst of simultaneous connections
  exhausts it and requests fail with `QueuePool limit … timed out`. For a
  congress where hundreds of devices connect at once, 15 is thin, and stale
  connections after a Postgres restart surface as user-facing 500s.
- **Feature-flag cache is per-process** (`feature_flags.py:48,85`) — toggles
  silently fail to propagate under `--workers > 1`. Startup migrations race
  the same way (`main.py:314`).
- **`is_enabled()` fails open** on unknown keys — a typo'd flag silently
  enables a feature (`feature_flags.py:71`).
- **Redis pub/sub reader dies permanently on any error** while publishes keep
  "succeeding" — cross-worker realtime goes silently dead until restart
  (`realtime.py:76`).
- **Push fan-out is serial with no per-send timeout** — an emergency alert to
  a full congress takes minutes and can hang on one bad endpoint
  (`push_service.py:126`).
- **`build_index()` rescans the whole database on every knowledge/companion
  request**, with no caching, several times per LLM-path `/ask`
  (`knowledge.py:242`; called from 8 companion + 7 knowledge sites).
- **N+1 queries**: poster gallery runs 5 queries per poster with no
  pagination (`posters.py:130`); feedback sentiment and moderation queue have
  the same shape.
- **Sync DB queries on the event loop**: `get_current_user_optional` is
  `async def` but runs a blocking SQLAlchemy query — on **every** request
  (`security.py:51`).
- **Cascade deletes destroy peer-review data**: deleting a user cascades away
  others' reviews and issued-certificate verifiability, and orphans uploaded
  files (`models/paper.py:45`).

### 3.3 Accessibility — the largest systemic gap

Verified against Chrome's own accessibility tree, not just by reading markup:

| Page | Form controls | With an accessible name |
|---|---:|---:|
| /settings | 9 | **0** |
| /profile | 4 | **0** |
| /venue | 23 | **0** |
| /recordings | 5 | **0** |
| /qa/1 | 5 | 1 |
| /login | 3 | 1 |

**Zero `for=` attributes and zero `aria-label`s on inputs exist in the entire
app.** Every `<label>` is a sibling, so screen readers announce inputs unnamed.

- **13 clickable `<div>`s with no `tabindex`/`role`** — including Q&A upvote
  (`qa.html:225`), poll voting (`qa.html:347`), poster detail, sponsor booth,
  and notification items. **Keyboard and screen-reader users cannot upvote a
  question or vote in a live poll** — the app's signature audience features.
- **Star ratings are click-only `<span>`s** and the survey hard-requires a
  star value — so a keyboard-only user can never complete the survey that
  gates the Participation Certificate (`feedback.html:73`).
- **Research-interest chips are click-only `<span>`s** — cannot be selected
  at signup or in profile by keyboard (`signup.html:32`).
- **Only 6 `aria-*` attributes exist across all 28 templates.** No
  `aria-live` anywhere, so SSE-delivered questions, poll openings, and the
  live/reconnecting status are silent to screen readers.
- **8 tabbed pages have no tab semantics** (no `role="tab"`/`aria-selected`).
- **5 hand-rolled modal systems**, none with `role="dialog"`, focus trapping,
  or Escape handling — while `main.js`'s own `_uiDialog` does all three
  correctly and is right there to reuse.
- **No skip-to-content link** — keyboard users traverse 15+ nav controls on
  every page.
- **Objective contrast failures**: `--w3` (28% white) body text and the 35%
  white mobile bottom-nav labels measure ~2.3:1–3:1. `admin_dashboard.html:52`
  uses `var(--emerald)` (`#0a3d2e`, a background token) as **text colour**, and
  `var(--b)` is referenced but **never defined**, so those borders silently
  vanish. *(All fixable by using existing tokens correctly — no palette change.)*
- **Recorded talks have no captions track** even though WebVTT transcripts
  already exist in the system (`recordings.html:142`).
- **Touch targets under 40px**: schedule card action buttons are 30–32px on
  the primary mobile surface (`schedule.html:46`).

### 3.4 UI consistency — heavy duplication

`main.css` is only 232 lines; the real styling lives in ~1,090 lines of
per-template `<style>` blocks. Measured duplication:

- The **same page-background gradient is copy-pasted into 18 templates**
  under 16 different class names (`.home-page`, `.page-wrap`, `.cn-page`, …).
- The **pill button is re-implemented 14 times** (`pg-btn`, `pp-btn`, `qa-btn`,
  `sched-btn`, `fb-btn`, `cp-btn`, `kg-btn`, `rc-btn`, `sp-btn`, `vn-btn`,
  `notes-btn`, `md-btn`, `toolbar-btn`, `.checkin button`) with padding
  drifting across 8×15 / 9×16 / 10×18 / 10×20 px and font-size .8/.82/.84rem
  — while `main.css`'s own `.btn`/`.btn-sm` go unused by all of them.
- `main.css` itself ships **two byte-identical primary buttons**, `.btn-gold`
  and `.btn-primary`; nothing uses `.btn-primary`.
- `esc()` is copy-pasted into 16 templates (which is why the P1 XSS has 16
  places to fix), and the global `api()` helper is shadowed with **three
  incompatible signatures**.
- Empty states duplicated in ~14 page-scoped classes; stat tiles 5+ times;
  tab bars in 7+ templates; three different toggle-switch implementations.
- **`recordings.html` is the only page using native `alert()`/`confirm()`** —
  12 call sites — while every other page uses `showToast`/`confirmDialog`.
- **Naming drift for the same destination**: `/knowledge` is "Topics" in the
  topnav, "Knowledge map" in the dropdown and mobile menu, "Knowledge Map" as
  the page title. Two logout affordances are visible at once, labelled
  "Logout" and "Sign Out".
- **`.badge-green` and `.badge-teal` both render gold** — the class names lie
  about what they do (`main.css:114`).

### 3.5 UX flows

- **No password reset.** "Forgot password?" is `href="#"` — a dead link styled
  identically to the working "Register now" beside it. No reset route, no
  email delivery of any kind exists in the codebase.
- **No return-URL on login redirects** — a deep link or an expired session
  always dumps the user at `/home`, losing what they were doing
  (`pages.py:60`). A poster-hunt QR scanned while logged out loses the
  check-in entirely (`pages.py:355`); the signup path loses it too.
- **No text search over the program** — client or server — for an advertised
  80+ session congress (`schedule.html:91`).
- **Broadcast to the entire attendee base sends with no confirmation step**
  (`admin_dashboard.html:434`).
- **Silent failures**: poster image upload failure shows nothing
  (`posters.html:170`); recordings fetch errors are masked as friendly empty
  states (`recordings.html:128`); several pages leave skeletons or "Loading…"
  up forever on error. Speaker recording-consent actions give no success
  feedback at all.
- **SSE poll re-render destroys in-progress input** — a word-cloud answer
  being typed is wiped when an update arrives (`qa.html:398`).
- **Several write actions don't disable their button**, allowing double-submit
  (`qa.html:377`).
- **Filtered-empty schedule shows "No schedule yet"**, implying the program is
  empty rather than that the filter matched nothing (`schedule.html:337`).
- **Tabs keep no URL/history state** — the back button exits the page and no
  tab is deep-linkable (8 pages).
- **Home dashboard omits Feedback, Venue, and Notes tiles** that exist in nav.

### 3.6 Feature completeness

**Fixed since the previous review** — verified in current code:
suspended-user access (`security.py:55`), certificate serial + QR + public
`/verify/{serial}` with frozen `issued_at`, the MOD-04 warn→mute→suspend
ladder, OAuth buttons now driven server-side, and the README test table
(752 tests / 30 files is accurate).

**Still open:**

- **Presenters have no UI for their own session.** `pages.py:120-123` computes
  `can_moderate` from *roles*; the API authorizes by *assignment*
  (`can_moderate_session`). Verified live: the speaker who **is** `speaker_id`
  of session 1 successfully created a poll via the API (**201**), but their
  `/qa/1` renders `const CAN_MOD = false` — no "+ New poll" form
  (`qa.html:133`), no "Present ↗" link (`qa.html:116`). **There is no UI path
  for a presenter to run a poll on their own talk.**
- **MOD-04 mute is only partially enforced.** `ensure_can_post` guards Q&A,
  posters, comments, reactions — but not polls, feedback, papers, or notes.
  Verified with a muted account: session rating with a public comment → **200**,
  paper submission → **201**, note → **201**, and a **free-text word-cloud
  submission → 200**, which then renders on the big-screen `/present` view.
- **`/present/{id}` has no authorization and no feature gate.** Verified: any
  attendee gets **200**, and it still returns 200 with the `polls` flag off.
- **Reactions have no feature flag** at all (`main.py:391`), unlike qa/polls.
  The `polls` flag gates the API but there is no page gate.
- **`session_chair` is still effectively a dead role**; `chair_id` is assigned
  from an arbitrary email with no role check (`schedule.py:33`).
- **AUTH-01 (2FA) and AUTH-02 (30-min admin timeout) are still advertised in
  the README but not implemented.** `SESSION_TIMEOUT` is dead code;
  `two_factor_enabled` appears only in a response schema. *(AUTH-03 and MOD-04
  are real — lockout verified live: 5 failures → 429 for 15 minutes.)*
- **Orphaned endpoints with no UI**: `DELETE /api/auth/account` (GDPR-relevant;
  it also never clears the auth cookie), `POST /api/papers/{id}/withdraw`,
  `POST /api/notifications/push/unsubscribe` (users can never turn push off),
  `GET /api/auth/oauth/providers` (now dead), plus companion `/nudges`,
  `/summary/{id}`, `/guide`, `/providers` and knowledge `/cross-pollination`.
- **Presenters/chairs are never notified when assigned** to a session.
- **Reporters and content authors get no notification when a report is
  resolved**; only 2 content types are reportable (question, poster_comment) —
  session ratings, notes, profiles, and word-cloud words have no report path.
- **Flow dead-ends**: accepted papers never reach a schedule slot; poster
  presenters earn no certificate; hunt completion has no reward hook.

**Genuinely missing table-stakes** (each verified absent by search): password
reset · any email delivery · ICS/calendar export · server-side program search ·
speaker directory/profile pages · GDPR data export · bulk schedule import ·
offline schedule (the service worker is push-only, so the program is
unreachable on flaky venue WiFi) · no web app manifest, so the PWA is not
installable · `sw.js` has no `pushsubscriptionchange` handler, so push dies
silently on key rotation.

### 3.7 Documentation drift

- README **contradicts itself on tests**: the Testing section correctly says
  752 tests / 30 files, but the architecture tree (`README.md:157-162`) still
  lists the ancient 4-file suite with 17/42/38/11 counts.
- "Admin UI (3 tabs)" — there are **6**. "Role hierarchy with 8 levels" —
  8 roles, **6 distinct levels**. Static tree omits `notifications.js`
  and `sw.js`.
- README API table still omits whole shipped routers: posters, moderation,
  connect, notifications feed/push, schedule CRUD, feature flags.
- `FEATURE_RELEASES.md` lists 6 of 12 registered flags.
- **No docs at all** for `connect`, `notes`, or `polls`. Four docs files exist
  that the README never links.
- README claims sponsor logos appear on "presenter/venue screens" — the venue
  page has no sponsor band.

---

## 4. What the application does well

1. **Test discipline is real.** 752 tests across 30 files, all passing, run in
   CI on every push against an isolated SQLite database — no Postgres or Redis
   needed. The suite is 7,604 lines against 14,289 lines of app code.
2. **Output escaping holds where it matters most.** Injected `<img
   src=x onerror=…>` payloads in schedule titles and Q&A text render as
   literal text — verified in-browser, `window.__xss` never set, zero injected
   elements. The three `|safe` uses all take server-controlled constants.
3. **Authorization is deep and consistent.** Hierarchical
   `can_manage_user`/`can_assign_role`, self-demotion and self-suspension
   blocked, last-super-admin protected. Verified live: a speaker gets 403 on
   every `/api/admin/*`, and note ownership isolation holds (admin editing
   another user's note → 404). Papers implement genuine blind review with COI
   handling.
4. **Idempotent user actions are backed by real unique constraints** —
   `uq_poster_vote`, `uq_session_attendance`, `uq_session_rating`,
   `uq_question_vote`, `uq_schedule_bookmark`, `uq_issued_cert_user_kind` and
   more — rather than hoping. Counts are recomputed from source rows instead of
   drifting counters.
5. **File uploads are traversal-safe by construction**: UUID storage names so
   the attacker-controlled filename never touches disk, extension allowlists,
   `abspath`+`commonpath` containment, SVG excluded for logos, and files served
   only through authenticated endpoints.
6. **The SSE layer is well built**: bounded per-subscriber queues with
   drop-on-full back-pressure, guaranteed cleanup via an async context
   manager, keepalives, disconnect detection, and events published only after
   commit. My hypothesis that streams pin DB connections was **disproved** by
   testing — the design is sound.
7. **The LLM integration is genuinely optional and fail-safe.** Every failure
   mode — missing key, uninstalled SDK, refusal, safety block, network error —
   collapses to `None` with a deterministic rules-based fallback, plus an
   explicit `LLM_PROVIDER=off` kill switch. The companion answered in 0.01 s
   with no provider configured.
8. **The production secret gate is a real fail-fast**: the app refuses to boot
   with the known dev `SECRET_KEY` (or any key < 32 chars) when
   `ENVIRONMENT=production`.
9. **Audit logging is thorough**: every mutating admin action is logged
   atomically with the change, IPs are hashed, PII exports logged per DATA-03,
   and the table is append-only by design.
10. **`main.js` sets the right example** — `showToast` builds with
    `textContent`, and `_uiDialog` implements `role="dialog"`, `aria-modal`,
    focus restore, Escape, and a focus trap. The gap is that the templates
    don't reuse it.

---

## 5. Recommended order of work

**Before the next release**
1. Fix `esc()` to escape quotes + add `escAttr()`/`safeUrl()`; hoist it out of
   the 16 templates into `main.js` (P1-1).
2. Fix `loadManage()` to read `.items` (P1-2) — one line restores the whole
   organizer recordings flow.
3. `git rm --cached .env`, add it to `.gitignore`, rotate `SECRET_KEY`.
4. Add `_csv_safe` to the admin CSV export.
5. Catch `IntegrityError` on check-in (and the other check-then-insert sites).

**High value, small diffs**
6. Compute the Q&A page's `can_moderate` with `can_moderate_session` — one
   change gives presenters their own session's controls.
7. Add `ensure_can_post` to polls, feedback, papers, notes.
8. Authorize and feature-gate `/present/{id}`; add a `reactions` flag.
9. Add `for=`/`id` pairs to every form control, and `role="button"` +
   `tabindex="0"` + key handlers to the 13 clickable divs (upvote and poll
   voting first).
10. Validate `sort_by` against a column allowlist; either enforce
    `duration_hours` or drop it; make `bulk_action` reject unknown actions.
11. Set `Secure` on cookies behind TLS; derive `max_age` from
    `ACCESS_TOKEN_EXPIRE_MINUTES`.
12. Add timeouts to LLM clients and a rate limit to `/api/companion/ask`.

**Structural**
13. Adopt Alembic (it is already a dependency) and retire the hand-rolled
    `_migrate_*` functions.
14. Configure the DB pool (`pool_size`, `max_overflow`, `pool_pre_ping`,
    `pool_recycle`).
15. Extract the duplicated page background, buttons, empty states, tab bars,
    and modals into `main.css` — using the existing tokens, no colour changes.
16. Move the feature-flag cache behind Redis (or re-read per request) so
    multi-worker deployments stay consistent.
17. Password reset + email delivery; ICS export; program search; offline
    schedule caching in the service worker.
18. Reconcile the README with reality: drop or implement AUTH-01/AUTH-02, fix
    the test tree, tab count, role levels, and the missing router rows.

---

## 6. Claims tested and **refuted** — do not re-raise

| Claim | Disproof |
|---|---|
| SSE streams pin a pooled DB connection for their lifetime → app-wide outage | Instrumented the live pool: with 3 streams open, **checked-out = 0** and ordinary requests served in 0.01 s. FastAPI 0.115 closes `yield` dependencies *before* the body streams. (Pool *sizing* remains a real but separate P2.) |
| Stored XSS in schedule titles / Q&A text | Payloads render as literal text; `window.__xss` never set; 0 injected elements. |
| IDOR on notes | Admin `PUT`/`DELETE` on another user's note → **404**. |
| Attendees can reach `/api/admin/*` | Speaker role → **403** on stats, users, audit. |
| Home page renders blank below the fold | Full-page-capture artifact; computed opacity is 1 for every tile in a real viewport. |
| `javascript:` URLs accepted in sponsor/recording links | Rejected server-side by `_clean_url` (`schemas/sponsor.py:11-17`). *(Venue `map_url` is the one field lacking this check — real, P3.)* |
| Division-by-zero in feedback/sponsor aggregations | All guarded (`feedback.py:61,184`, `sponsors.py:170`). |
| Upload path traversal | UUID names + `commonpath` containment (`paper_files.py:47-54`). |

---

## 7. Appendix — P3 items

107 verified P3 findings are recorded in the review data. The recurring themes:

- **Consistency**: mixed Title Case/sentence case, three "Loading…" spellings,
  browser-tab titles mixing `congress_full`/`congress_short`, back-affordance
  split across two patterns (3 pages circular button, 14 pages bare "←"),
  three danger reds and two success greens hardcoded while the tokens go
  unused, cross-page class leakage (templates using classes defined in other
  templates' `<style>` blocks).
- **Robustness**: unguarded top-level `localStorage` in `venue.html:162`
  (throws in privacy mode and kills the whole page script), debounced searches
  with no request sequencing (stale results can overwrite fresh ones),
  fire-and-forget async calls inside synchronous `try/catch` that cannot catch
  their rejection, admin `api()` calling `r.json()` unconditionally so a 204
  reads as an error.
- **Data**: wordcloud per-user cap is count-then-insert with no constraint,
  recording view counter is read-modify-write (concurrent playbacks lose
  counts), transcript search has no SQL limit and doesn't escape LIKE
  wildcards, `hash_ip` is unsalted SHA-256 truncated to 16 hex chars (trivially
  reversible over IPv4 — and the raw IP is stored in the audit description
  anyway), `log_action()` commits the caller's in-flight transaction.
- **i18n**: Arabic mode flips `dir` but never sets `lang="ar"`; all chrome
  outside the venue page is hardcoded English with dates pinned to `en-GB`.
