# AI Congress Companion

A congress assistant at `/companion` (feature flag `companion`) that answers in
natural language, nudges at the right moment, and suggests what to do next —
all grounded in live congress data.

## The design rule: it always works

Every answer is built from facts assembled from the database (program, your
day, venue, posters, papers, recordings, certificates, the knowledge graph). A
deterministic intent router handles the questions attendees actually ask, so
those answers are **exact, instant, free and available offline**.

An optional Claude integration handles free-form questions the router can't
classify. If no API key is set, the SDK isn't installed, or the call fails, the
companion falls back to a rule-based answer — it never errors out and it never
answers from model memory.

```
question → intent router ──match──→ grounded answer (via: "rules")
              │
              └─no match→ topic lookup ──match──→ grounded answer
                              │
                              └─no match→ Claude (if configured) → answer (via: "claude")
                                              │
                                              └─unavailable/failed→ capability help
```

## What it answers

| Ask | Comes from |
|-----|-----------|
| "What's on right now?" / "what's next?" | live + upcoming program, your bookmarks starred |
| "What should I see next?" | knowledge-graph overlap with your topics, keynote weighting, Q&A buzz |
| "Where is Hall 2?" | venue rooms (floor + directions hint), links to turn-by-turn |
| "What's the WiFi?" | organizer-managed venue settings |
| "How am I doing on CME credits?" | live certificate progress |
| "Where can I eat / a pharmacy / an ATM / the metro?" | organizer-managed Dubai info |
| "I need medical help" | emergency contacts |
| "Who works on organoids?" | opted-in directory + session presenters, filtered by topic |
| "Any recordings?" / "posters?" / "my reviews?" | recordings catalogue, poster hunt, review queue |
| "Tell me about CRISPR" | knowledge-graph topic lookup |
| "I'm exhausted" | energy-aware pacing advice |

## Proactive features (per the spec)

- **Proactive nudges** (`GET /nudges`) — a bookmarked session starting within
  45 minutes (with its room), your own talk in under two hours, a certificate
  one step away, unrated sessions, reviews still owed, **recording consent
  waiting on you**, and the post-event survey. Sorted high → low urgency.
- **Energy-aware suggestions** — reads the day so far (sessions attended today,
  the gap to your next one, the hour) and suggests the right move: take the
  next slot off after a long day, use a 40-minute gap for the poster hall, pick
  one talk in the late-afternoon dip.
- **Serendipity mode** (`GET /serendipity`) — a good thing *outside* your usual
  lane, chosen through knowledge-graph cross-pollination, with the connection
  explained. Finished sessions are never suggested, and picks are deduplicated.
- **Speaker prep** (`GET /prep/{session_id}`) — audience size (bookmarks +
  check-ins), the **top questions already waiting** in live Q&A, the room and
  floor, related sessions, current rating, and a pre-walk-on checklist. Open to
  the session's speaker, its chair, and organizers only.
- **Smart summaries** (`GET /summary/{session_id}`) — what the session is, its
  topics, its rating, the room's top question, whether a recording is available,
  and what to see next.
- **Dubai guide** (`GET /guide`) — the organizer-managed venue, transport,
  emergency and places content, grouped by category. The WiFi **password is
  omitted** from this endpoint (ask the companion or open `/venue` for it).

## Configuration

| Setting | Default | Meaning |
|---------|---------|---------|
| `ANTHROPIC_API_KEY` | `""` | Blank = rule-based answers only (fully functional) |
| `COMPANION_MODEL` | `claude-opus-5` | Model used for free-form questions |
| `COMPANION_MAX_TOKENS` | `900` | Response cap |

The Anthropic SDK is an **optional** dependency (`pip install anthropic`).
When a key is configured, the call sends a compact factual brief plus the
question, with a system prompt that forbids inventing session titles, times,
rooms, names or numbers and tells the model to say so when the facts don't
cover the question. Refusals and errors fall through to the rule-based answer.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /api/companion/ask` | any | `{question, allow_llm}` → grounded answer + items + links |
| `GET /api/companion/briefing` | any | Day at a glance, energy advice, top nudges |
| `GET /api/companion/nudges` | any | Proactive prompts, urgency-sorted |
| `GET /api/companion/serendipity` | any | Picks outside your usual topics |
| `GET /api/companion/prep/{id}` | speaker / chair / organizer | Speaker prep |
| `GET /api/companion/summary/{id}` | any | Smart session summary |
| `GET /api/companion/guide` | any | Dubai guide |

## Files

| File | Role |
|------|------|
| `app/core/companion.py` | context builder, intent router, nudges, prep, Claude path |
| `app/api/companion.py` | HTTP layer |
| `app/templates/companion.html` | `/companion` (chat, my day, serendipity, prep) |
