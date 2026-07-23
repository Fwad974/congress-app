# Poster Gallery

An interactive poster hall: attendees browse posters, **vote**, **comment**, and
play a **scavenger hunt** by checking in to posters around the venue.

## Roles

| Who | Can |
|-----|-----|
| **Presenter** — `speaker` / `session_chair` / `review_chair` / `admin` / `super_admin` | Create + manage their own posters, upload artwork |
| **Attendee** — any authenticated user | Browse, vote, comment, and check in (scavenger hunt) |
| **Admin** — `admin` / `super_admin` | Edit/delete any poster, moderate any comment |

## Features

- **Gallery** (`/posters`): responsive grid of poster cards (image thumbnail,
  board number, track, vote count, comment count, visited badge). Search, filter
  by track, and sort by **most voted / newest / board number**.
- **Voting**: one upvote per attendee per poster (toggle). Drives the default
  "most voted" ranking — a live people's-choice board.
- **Comments**: an attendee discussion thread per poster. Authors, the poster's
  presenter, and admins can delete a comment.
- **Poster detail**: full image/PDF, abstract, external link, votes, comments.
- **Presenter tools**: add/edit/delete a poster, upload or replace the image/PDF
  (`.png/.jpg/.jpeg/.webp/.gif/.pdf`, ≤ `MAX_UPLOAD_MB`), and see the poster's
  **scavenger-hunt code**.

## Scavenger hunt

Each poster gets a unique short **`hunt_code`** (shown to its presenter, meant to
be printed on the physical board). Attendees either tap **Visit** on a poster or
type a code into the hunt bar to **check in**. Progress is tracked toward
`POSTER_HUNT_GOAL` distinct posters (default 5); reaching it marks the hunt
**complete**. Check-ins are idempotent (one per poster/attendee).

**QR codes:** the presenter's detail view has a **🖨 Print QR** link →
`/posters/{id}/qr-print`, a printable sheet with the poster's QR
(`GET /api/posters/{id}/qr`, owner/admin only). The QR encodes
`/posters?checkin=CODE`, so scanning it with a phone camera opens the gallery
and checks the attendee in automatically. Generated with segno
(`app/core/qr.py`); the base URL comes from `OAUTH_REDIRECT_BASE` when set.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `GET /api/posters` | any | Gallery (`category`, `search`, `sort=votes\|recent\|board`) |
| `POST /api/posters` | presenter | Create a poster |
| `GET /api/posters/mine` | presenter | My posters |
| `GET /api/posters/hunt` | any | Scavenger-hunt progress |
| `POST /api/posters/hunt` | any | Check in by `code` |
| `GET /api/posters/{id}` | any | Poster detail + comments |
| `PUT /api/posters/{id}` | owner/admin | Edit |
| `DELETE /api/posters/{id}` | owner/admin | Delete (cascades votes/comments/visits) |
| `POST /api/posters/{id}/vote` · `DELETE …/vote` | any | Add / remove upvote |
| `POST /api/posters/{id}/comments` | any | Add a comment |
| `DELETE /api/posters/{id}/comments/{cid}` | author/owner/admin | Delete a comment |
| `POST /api/posters/{id}/visit` | any | Check in to this poster |
| `POST /api/posters/{id}/image` · `GET …/image` | owner (upload) / any (view) | Poster artwork |

The `hunt_code` is only returned to a viewer who can edit the poster.

## Config

| Setting | Default | Meaning |
|---------|---------|---------|
| `POSTER_HUNT_GOAL` | `5` | Posters to visit to complete the hunt |
| `UPLOAD_DIR` / `MAX_UPLOAD_MB` | `data/uploads` / `15` | Artwork storage (under `posters/`) + size cap |

## Files

| File | Role |
|------|------|
| `app/models/poster.py` | `Poster`, `PosterVote`, `PosterComment`, `PosterVisit` |
| `app/schemas/poster.py` | request/response schemas |
| `app/api/posters.py` | all endpoints + serialization + permissions |
| `app/core/poster_files.py` | image/PDF storage helper |
| `app/templates/posters.html` | `/posters` gallery page |
