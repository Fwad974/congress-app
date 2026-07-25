# Connect — Attendee Networking Directory

An opt-in directory that lets attendees find and contact peers.

## Model

- Nobody appears until they enable **Directory visibility** on `/connect`
  (`User.networking_visible`, off by default).
- Opting in shares name, role, institution, research interests, bio, ORCID and
  email with other authenticated attendees.
- Gated by the `connect` feature flag (API router + page).

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/connect/directory?search=` | List opted-in attendees (name/institution/bio search). |
| PUT | `/api/connect/visibility` | Toggle your own directory visibility. |

Only active, opted-in users are listed. Results are ordered by name.

## Privacy notes / roadmap

The current directory publishes the full profile bundle (including email) to
every logged-in attendee. A future **Connect v2** would replace this with a
connection request/accept flow so contact details are exchanged per connection
rather than published to all. Until then, keep the shared fields in mind and
treat opting in as consenting to share your email with all attendees.
