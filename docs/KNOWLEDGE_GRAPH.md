# Knowledge Graph

A visual topic map of the congress that links talks, posters, papers and
researchers — computed live from data that already exists, with **no extra data
entry and no refresh job**. UI at `/knowledge` (feature flag `knowledge`).

## Where topics come from

`app/core/knowledge.py` builds the index on every request, in three passes,
most trusted first:

1. **Curated fields** — a paper/poster `category` and a user's declared
   `research_interests` are topics as-is.
2. **A domain phrase list** (`TOPIC_PHRASES`) matched with **word boundaries**
   against titles, abstracts and descriptions. Synonyms collapse onto one node,
   so "induced pluripotent stem cells" and "iPSC" are the same topic — and
   `ema` never fires inside `hematopoietic`.
3. **Corpus keywords** — significant words appearing in **at least two** items,
   which keeps one-off wording out of the map. Each item keeps at most
   `MAX_ITEM_TOPICS` (8) topics so nodes stay readable.

A one-off *keyword* is dropped as noise; a one-off *curated* topic (category,
declared interest, domain phrase) is kept, because it is real and searchable.

## What's in the graph

| Node | Included when | Carries |
|------|---------------|---------|
| Session | any non-break schedule item | time, room, presenter, live Q&A volume |
| Paper | status `accepted` (the public program) | authors |
| Poster | status `approved` | authors, comment volume |
| Person | presents a session, or opted into the directory | institution, interests |

**Privacy:** only public program data is used. A person appears because they
present a session (their name is already in the program) or because they opted
into the networking directory (DATA-06) — and interests are read **only** for
opted-in users. Submitted-but-not-accepted papers and unapproved posters are
excluded.

## Features (per the spec)

- **Visual topic map** — `GET /graph` returns nodes + edges (topic↔item and
  topic↔topic co-occurrence). The page lays topics on a ring with their items
  orbiting the topics they belong to; tapping a topic focuses the map on it and
  its neighbours.
- **Session connections** — `GET /related/{kind}/{id}` answers "if you liked A,
  B is relevant **because**…": shared topics are scored by overlap (normalized,
  so broad nodes don't dominate), with a bonus for the same presenter, and each
  result carries a plain-English reason and the shared topic list.
- **Research thread** — `GET /thread/{topic}` follows one topic across the
  congress, grouping sessions **by day** in program order and listing the
  posters, papers and researchers on the same thread.
- **Cross-pollination** — `GET /cross-pollination` finds topics that co-occur
  with what you already follow but that you haven't engaged with, and explains
  the link ("Organoid and Bioprinting appear together in 3 items").
- **Trending topics** — `GET /topics` ranks by **discussion volume** (live Q&A
  questions + poster comments), then breadth.
- **Export your map as PDF** — `GET /my-map.pdf` renders the personal map
  (topics you follow with their items, the cross-pollination picks and the
  congress trend list) as a paginated A4 PDF. The export is **audit-logged**.

"What you follow" is derived from your bookmarks, the sessions you checked into,
and your profile's research interests.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `GET /api/knowledge/graph?topic=&limit=` | any | Nodes + edges, optionally focused |
| `GET /api/knowledge/topics` | any | Trending topics with item mix + discussion |
| `GET /api/knowledge/related/{kind}/{id}` | any | Related items with reasons |
| `GET /api/knowledge/thread/{topic}` | any | One topic across the days |
| `GET /api/knowledge/cross-pollination` | any | Adjacent topics you're missing |
| `GET /api/knowledge/my-map` · `/my-map.pdf` | any | Personal map · PDF export |

## Files

| File | Role |
|------|------|
| `app/core/knowledge.py` | index build, topics, related, threads, cross-pollination |
| `app/core/knowledge_pdf.py` | paginated PDF export of the personal map |
| `app/api/knowledge.py` | HTTP layer |
| `app/templates/knowledge.html` | `/knowledge` (map, trending, thread, my map) |
