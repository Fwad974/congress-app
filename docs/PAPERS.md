# Abstracts & Paper Submission

A peer-review pipeline: **submit → assign reviewers → review → decide → revise**.
UI at `/papers` with role-based tabs (Submit, My Submissions, To Review, Manage).

## Roles

| Who | Can |
|-----|-----|
| **Author** — any authenticated user | Submit, track status, respond to reviewers, resubmit revisions, withdraw |
| **Reviewer** — `reviewer` / `review_chair` | Accept/decline/recuse assignments, then review (score 1–5 + comments) |
| **Review chair** — `review_chair` / `admin` / `super_admin` | List all, assign reviewers, decide |

## Lifecycle

```
submitted ──assign──▶ under_review ──decide──┬─▶ accepted
     ▲                                        ├─▶ rejected
     └──────── resubmit ◀── revision_requested┘   (author withdraw → withdrawn)
```

- **Decision** is `accept` / `reject` / `revision`. A revision returns the paper
  to the author; they edit + add a **response to reviewers** and resubmit, which
  bumps the round and re-opens it for the reviewers.
- **REV-05:** at most **2 revision rounds** (`MAX_REVISION_ROUNDS`). `round`
  starts at 1 for the original submission, so revisions are refused once
  `round - 1 == 2`; the chair must then accept or reject.
- Reviewers can only score/respond while the paper is **submitted / under
  review** — reviews freeze once a decision is made. Decisions and reviewer
  assignment are refused on **terminal** papers (accepted / rejected /
  withdrawn), so a decision can't be silently flipped.

### Reviewer assignment lifecycle

When a chair assigns a reviewer, the review starts as **`invited`**. The
reviewer's **To Review** tab shows a workload summary and per-paper actions:

- **Accept** → `accepted`. Saving or submitting a review also auto-accepts.
- **Decline** → `declined`. **Recuse (COI)** → `recused`. Both take an optional
  reason, clear any in-progress review, block further scoring, and notify the
  review chairs. The reviewer can reverse either with "Take it back".

Chairs see each reviewer's response state on the assignment chips
(`✓ accepted`, `✗ declined`, `⊘ recused`, `… invited`).

### Accepted-papers showcase (proceedings)

The **Accepted Papers** tab (the default tab, visible to *every* attendee)
lists all `accepted` submissions as read-only cards grouped by track, with a
search box and track filter. It reuses `_serialize`, which returns a
public-safe view to a non-author/non-chair viewer — no reviews, scores, or
internal identities leak. Accepted papers' uploaded manuscripts become
downloadable by any authenticated attendee (the download endpoint special-cases
`status == accepted`); non-accepted papers stay restricted to author/reviewer/chair.

## Data model (`app/models/paper.py`)

- **`Paper`** — author, title, authors (free text), category, abstract,
  `file_url` (optional external link), `file_name`/`stored_file` (uploaded
  manuscript), `status`, `round`, `author_response`, `decision_comment`.
- **`Review`** — one per (paper, reviewer): `score` (1–5), `comments`,
  `submitted`, plus an assignment lifecycle `state`
  (`invited` → `accepted` / `declined` / `recused`) and `response_reason`.
  Unique on (paper, reviewer).

## Rules enforced

- **REV-01 (min reviewers):** the UI/flow expects ≥2; the API lets a chair
  assign any number ≥1 (assign more than one in a single call).
- **REV-02 (COI):** `GET /api/papers/reviewers` flags reviewers whose
  `institution` matches the author's; `assign` **blocks** them unless
  `override_coi: true`.
- **REV-05 (rounds):** capped at 2 (`MAX_REVISION_ROUNDS`).
- **REV-06 (no leaks):** reviews are only returned to the author *after* a
  decision, and only submitted ones, **anonymized** ("Reviewer 1", …).
- **Blind review:** both the submitter's account name (`author_name`) **and**
  the free-text `authors` list are withheld from reviewers while the paper is
  under review; chairs and the author always see them, and accepted papers show
  authors publicly. Score aggregates (`avg_score`, review counts) go to the
  chair always and the author only after a decision — never to a reviewer.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /api/papers` | author | Submit |
| `GET /api/papers/mine` | author | My submissions |
| `PUT /api/papers/{id}` | author | Edit draft / resubmit revision |
| `POST /api/papers/{id}/withdraw` | author | Withdraw |
| `POST /api/papers/{id}/file` | author | Upload/replace manuscript (PDF/Word) |
| `GET /api/papers/{id}/file` | author / assigned reviewer / chair | Download manuscript |
| `DELETE /api/papers/{id}/file` | author | Remove manuscript |
| `GET /api/papers/accepted` | any attendee | Accepted-papers showcase (proceedings) |
| `GET /api/papers/assigned` | reviewer | Papers to review |
| `POST /api/papers/{id}/respond` | reviewer | Accept / decline / recuse an assignment |
| `PUT /api/papers/{id}/review` | reviewer | Save/submit a review |
| `GET /api/papers` | chair | All submissions (filter `status`, `category`) |
| `GET /api/papers/reviewers?paper_id=` | chair | Assignable reviewers + COI flags |
| `POST /api/papers/{id}/assign` | chair | Assign reviewers (`override_coi`) |
| `POST /api/papers/{id}/decision` | chair | `accept` / `reject` / `revision` |
| `GET /api/papers/{id}` | author / assigned reviewer / chair | View one |

Assignments notify the reviewer, and decisions notify the author, via the
in-app notification feed (`kind: "paper"`). Assign/decide are audit-logged
(`paper_decision`).

## Manuscript upload (PDF / Word)

Authors can attach a manuscript file alongside (or instead of) the `file_url`
link. Files are stored on disk under `UPLOAD_DIR` (default `data/uploads`,
which resolves under the persisted `/app/data` volume in Docker) with an opaque
uuid-based name — the original (attacker-controlled) filename never touches the
filesystem, avoiding path traversal and collisions.

- **Formats:** `.pdf`, `.doc`, `.docx`. **Size:** ≤ `MAX_UPLOAD_MB` (default 15).
- **Editing window:** upload/replace/remove only while the paper is
  `submitted` or `revision_requested` (locked once under review / decided).
- **Access:** the download endpoint is authenticated — only the author, an
  assigned reviewer, or a chair can fetch it. Files are **never** served from
  `/static`.
- Helper: `app/core/paper_files.py` (`is_allowed`, `save_bytes`, `path_for`,
  `delete_file`). Config: `UPLOAD_DIR`, `MAX_UPLOAD_MB` (`app/core/config.py`).

## Not yet (follow-ups)

- Object storage (S3/GCS) instead of local disk for multi-node deployments.
- Deadline reminders (REV-03) — needs a scheduler.
- Configurable single/double-blind (currently light double-blind).

## Files

| File | Role |
|------|------|
| `app/models/paper.py` | `Paper`, `Review`, `PaperStatus` |
| `app/schemas/paper.py` | request/response schemas |
| `app/api/papers.py` | all endpoints + serialization + COI/blind logic |
| `app/core/paper_files.py` | manuscript storage helper (validate/save/serve) |
| `app/templates/papers.html` | role-based `/papers` page |
