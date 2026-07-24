# Venue & Dubai Info

Interactive floor plan with a live session overlay, turn-by-turn navigation,
WiFi one-tap connect, Dubai local info, transport, emergency contacts, and an
English/Arabic toggle. UI at `/venue` (feature flag `venue`).

**Everything is organizer-managed** — nothing is hardcoded in templates. The
code ships sensible Dubai defaults (DWTC, metro, 999/998/997, nearby hotels…)
that are served only until an admin saves their own version from the page's
**Manage** tab.

## Features (per the spec)

- **Interactive floor plan with room labels + current session overlay** — a
  schematic SVG rendered from admin-placed rooms (grid 0–100 per floor,
  1 unit ≈ 1 m). Room names are matched **case-insensitively** against
  `ScheduleItem.location`, so each room shows what's happening **now** (pulsing
  live marker) and what's **next**. Floor tabs; tap a room for details; the map
  auto-refreshes every 60 s.
- **Turn-by-turn navigation between rooms** — `GET /api/venue/route?from_room=&to_room=`
  generates walking steps from the grid coordinates: exit, stairs/elevator on
  floor changes, heading (left/right/straight) with an approximate distance,
  and which side the destination lands on — **in English and Arabic**, plus an
  optional admin-set "directions hint" ("next to the café").
- **WiFi one-tap connect** — SSID/password/security are admin-set; the page
  shows copy buttons and a **`WIFI:` QR code** (`GET /api/venue/wifi-qr`) that
  joins the network when scanned. Payload special characters are escaped; open
  (`nopass`) networks supported.
- **Dubai local info** — admin-managed places grouped by category
  (hotel / restaurant / pharmacy / atm / other) with distance and optional map
  link.
- **Transportation** — admin-managed entries (metro / taxi / parking / airport)
  with icons.
- **Emergency contacts** — admin-managed list (security / medical / general)
  rendered as tap-to-call `tel:` buttons.
- **Multi-language (English / عربي)** — a page-level toggle (persisted in
  localStorage) that flips the UI strings, switches the layout to RTL, and
  prefers the `…_ar` counterpart of every admin-entered field (falling back to
  English when Arabic isn't provided).

## Admin management

The **Manage** tab (admins only) edits, with every change **audit-logged**
(`settings_change`):

- **Rooms** — name (+ Arabic), kind (room/hall/facility), floor, grid
  x/y/w/h, directions hint. Room CRUD: `POST/PUT/DELETE /api/venue/rooms…`.
- **Venue & WiFi** — venue name/address (+ Arabic), map link, SSID, password,
  security.
- **Lists** — transport, emergency, and places as pipe-separated line editors
  (Arabic columns optional).

`PUT /api/venue/settings` is a **partial** update: only the sections provided
are replaced; everything else keeps its stored value (or the default).

## Data model (`app/models/venue.py`)

- **`VenueRoom`** — name/name_ar, kind, floor, x/y/w/h (grid), directions
  hints. Drives the floor plan, the overlay, and the route generator.
- **`VenueSetting`** — key → JSON for `general`, `wifi`, `transport`,
  `emergency`, `places`.

## API

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `GET /api/venue` | any | Everything: settings + rooms with now/next + floors |
| `GET /api/venue/wifi-qr` | any | WiFi one-tap-connect QR (SVG) |
| `GET /api/venue/route?from_room=&to_room=` | any | Turn-by-turn steps (EN + AR) |
| `PUT /api/venue/settings` | organizer | Partial settings update (audit-logged) |
| `POST /api/venue/rooms` · `PUT/DELETE /rooms/{id}` | organizer | Room CRUD (audit-logged) |

## Files

| File | Role |
|------|------|
| `app/models/venue.py` | `VenueRoom`, `VenueSetting` |
| `app/schemas/venue.py` | request/response schemas |
| `app/api/venue.py` | defaults, overlay, route generator, WiFi QR, admin CRUD |
| `app/templates/venue.html` | `/venue` page (map, navigate, info, manage, EN/AR) |
