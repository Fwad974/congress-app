# Abstracts & Paper Submission

A peer-review pipeline: **submit → assign reviewers → review → decide → revise**.
UI at `/papers` with role-based tabs (Submit, My Submissions, To Review, Manage).

## Roles

| Who | Can |
|-----|-----|
| **Author** — any authenticated user | Submit, track status, respond to reviewers, resubmit revisions, withdraw |
| **Reviewer** — `reviewer` / `review_chair` | Review papers assigned to them (score 1–5 + comments) |
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
- **REV-05:** at most **2 revision rounds** — once `round == 2`, a further
  revision request is refused (the chair must accept or reject).

## Data model (`app/models/paper.py`)

- **`Paper`** — author, title, authors (free text), category, abstract,
  `file_url`, `status`, `round`, `author_response`, `decision_comment`.
- **`Review`** — one per (paper, reviewer): `score` (1–5), `comments`,
  `submitted`. Unique on (paper, reviewer).

## Rules enforced

- **REV-01 (min reviewers):** the UI/flow expects ≥2; the API lets a chair
  assign any number ≥1 (assign more than one in a single call).
- **REV-02 (COI):** `GET /api/papers/reviewers` flags reviewers whose
  `institution` matches the author's; `assign` **blocks** them unless
  `override_coi: true`.
- **REV-05 (rounds):** capped at 2 (`MAX_REVISION_ROUNDS`).
- **REV-06 (no leaks):** reviews are only returned to the author *after* a
  decision, and only submitted ones, **anonymized** ("Reviewer 1", …).
- **Blind review:** the submitter's identity (`author_name`) is withheld from
  reviewers; chairs and the author see it.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /api/papers` | author | Submit |
| `GET /api/papers/mine` | author | My submissions |
| `PUT /api/papers/{id}` | author | Edit draft / resubmit revision |
| `POST /api/papers/{id}/withdraw` | author | Withdraw |
| `GET /api/papers/assigned` | reviewer | Papers to review |
| `PUT /api/papers/{id}/review` | reviewer | Save/submit a review |
| `GET /api/papers` | chair | All submissions (filter `status`, `category`) |
| `GET /api/papers/reviewers?paper_id=` | chair | Assignable reviewers + COI flags |
| `POST /api/papers/{id}/assign` | chair | Assign reviewers (`override_coi`) |
| `POST /api/papers/{id}/decision` | chair | `accept` / `reject` / `revision` |
| `GET /api/papers/{id}` | author / assigned reviewer / chair | View one |

Assignments notify the reviewer, and decisions notify the author, via the
in-app notification feed (`kind: "paper"`). Assign/decide are audit-logged
(`paper_decision`).

## Not yet (follow-ups)

- File **upload** (currently a `file_url` link) — needs object storage.
- Deadline reminders (REV-03) — needs a scheduler.
- Configurable single/double-blind (currently light double-blind).

## Files

| File | Role |
|------|------|
| `app/models/paper.py` | `Paper`, `Review`, `PaperStatus` |
| `app/schemas/paper.py` | request/response schemas |
| `app/api/papers.py` | all endpoints + serialization + COI/blind logic |
| `app/templates/papers.html` | role-based `/papers` page |
