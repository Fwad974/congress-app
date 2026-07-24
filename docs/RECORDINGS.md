# Session Recordings

Consent-gated session video with a searchable transcript and slide-sync
playback, available for a limited window after the congress. UI at
`/recordings` (feature flag `recordings`).

## The three gates

A recording is watchable only when **all three** hold:

1. **Speaker consent** — the session's presenting speaker (`ScheduleItem.speaker_id`)
   grants consent. Nothing is published without it, and *denying* consent on an
   already-published recording pulls it out of the catalogue immediately.
2. **Organizer publication** — an organizer publishes it. `POST /publish`
   refuses with 400 while consent is `pending`/`denied` or the video link is
   missing.
3. **The availability window** — publishing stamps `available_until =
   now + RECORDING_RETENTION_DAYS` (default **30 days**, per-publish override
   with `?days=`). After that the recording disappears for attendees; the
   organizer still sees it, flagged `expired`, and can extend the window with
   `PUT /api/recordings/{id}` (`available_until`).

Speakers always see their own recordings regardless of state — that's where
they act on consent. Organizers see everything with `?include_hidden=true`.

## Features (per the spec)

- **Video recording with speaker consent** — consent is a first-class field
  (`pending` / `granted` / `denied`) with the decider, the timestamp and an
  optional note, and every write is **audit-logged**. Creating a recording
  notifies the speaker that their decision is needed.
- **Searchable transcripts with timestamps** — `GET /api/recordings/search?q=`
  searches every transcript the caller may watch and returns the
  `start_seconds` of each hit, so the UI seeks the player straight to the
  moment. `GET /api/recordings/{id}?q=` filters one recording's transcript.
  Withheld/expired recordings drop out of attendee search results.
- **Slide-sync playback** — slide markers ("slide 4 starts at 12:30") drive a
  slide strip that highlights the current slide as the video plays; clicking a
  slide or a transcript line seeks the player.
- **Available 30 days post-event** — the window above.

## Getting a transcript in

`POST /api/recordings/{id}/transcript` accepts either structured `segments` or
a pasted `vtt` blob. The parser (`app/core/transcripts.py`) is deliberately
tolerant and handles what recording platforms actually export:

- `hh:mm:ss.mmm` and `mm:ss,mmm` cue times (WebVTT and SRT)
- optional cue numbers / identifiers
- WebVTT `<v Speaker>` voice tags and plain `Speaker:` prefixes → `speaker_label`
- unparseable blocks are skipped rather than failing the upload

Uploading replaces the previous transcript, so re-running a transcription is
safe.

## Data model (`app/models/recording.py`)

- **`SessionRecording`** — one per schedule item (unique): video/slides links,
  duration, `status` (draft/published/withheld), consent fields, publication
  and window timestamps, view counter.
- **`TranscriptSegment`** — `start_seconds`, `end_seconds`, `speaker_label`,
  `text`. Indexed by `(recording_id, start_seconds)`.
- **`SlideMarker`** — `at_seconds`, `slide_number` (unique per recording),
  title, image link.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `GET /api/recordings` | any | Catalogue (`?mine=true`, organizer `?include_hidden=true`) |
| `GET /api/recordings/search?q=` | any | Transcript search across available recordings |
| `GET /api/recordings/{id}?q=` | any | Detail + transcript (optionally filtered) + slides |
| `POST /api/recordings/{id}/view` | any | Count a playback (organizer/speaker previews don't count) |
| `POST /api/recordings/{id}/consent` | speaker | Grant / deny consent (audit-logged) |
| `POST /api/recordings` · `PUT /{id}` · `DELETE /{id}` | organizer | Manage recordings |
| `POST /api/recordings/{id}/publish?days=` · `/withhold` | organizer | Open / close the window |
| `POST /api/recordings/{id}/transcript` · `/slides` | organizer | Replace transcript / slide markers |

## Configuration

| Setting | Default | Meaning |
|---------|---------|---------|
| `RECORDING_RETENTION_DAYS` | `30` | Availability window opened on publish |

## Files

| File | Role |
|------|------|
| `app/models/recording.py` | `SessionRecording`, `TranscriptSegment`, `SlideMarker` |
| `app/schemas/recording.py` | request/response schemas + link validation |
| `app/core/transcripts.py` | WebVTT/SRT parsing, timestamp formatting, snippets |
| `app/api/recordings.py` | consent, publication window, search, slide sync |
| `app/templates/recordings.html` | `/recordings` (player, transcript, slides, manage) |
