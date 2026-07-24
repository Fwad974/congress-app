"""
AI Congress Companion — the engine behind /companion.

Design: **the companion always works.** Every answer is grounded in live
congress data assembled here (program, the user's own day, venue, posters,
recordings, certificates, the knowledge graph). A deterministic intent router
handles the questions attendees actually ask — "what's on now", "where is Hall
2", "what's the WiFi", "what should I see next" — with exact data and no model
call, so it is fast, free and correct offline.

When an ``ANTHROPIC_API_KEY`` is configured, free-form questions that the
router can't classify are answered by Claude, given the same facts as context
and told to answer only from them. If the key is missing, the SDK isn't
installed, or the call fails, the companion degrades to a helpful rule-based
answer instead of erroring.

Beyond Q&A this module produces the proactive parts of the spec: nudges,
energy-aware suggestions, serendipity picks, speaker prep and smart summaries.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core import feature_flags, knowledge as kg
from app.core.config import get_settings
from app.models.attendance import SessionAttendance
from app.models.feedback import SessionRating, SurveyResponse
from app.models.paper import Paper, Review
from app.models.poster import Poster, PosterVisit
from app.models.qa import Question, QuestionStatus
from app.models.recording import SessionRecording
from app.models.schedule import ScheduleBookmark, ScheduleItem, ScheduleType
from app.models.user import User

logger = logging.getLogger(__name__)

# How far ahead "coming up" reaches, and how long a gap counts as a break.
SOON_MINUTES = 45
LONG_DAY_SESSIONS = 4  # attended today before we start suggesting a breather


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _fmt_time(dt: Optional[datetime]) -> str:
    dt = _as_utc(dt)
    return dt.strftime("%H:%M") if dt else ""


def _fmt_when(dt: Optional[datetime]) -> str:
    dt = _as_utc(dt)
    return dt.strftime("%a %d %b, %H:%M") if dt else "unscheduled"


def _session_brief(item: ScheduleItem) -> Dict:
    return {
        "kind": "session", "id": item.id, "title": item.title,
        "when": item.start_time, "location": item.location,
        "url": f"/schedule#session-{item.id}",
        "subtitle": (item.extra or {}).get("speaker") or "",
    }


# ─── Facts about right now ───────────────────────────────────────
def program(db: Session) -> List[ScheduleItem]:
    return (db.query(ScheduleItem)
            .filter(ScheduleItem.type != ScheduleType.break_)
            .order_by(ScheduleItem.start_time).all())


def current_and_next(db: Session, now: Optional[datetime] = None
                     ) -> Tuple[List[ScheduleItem], List[ScheduleItem]]:
    now = now or datetime.now(timezone.utc)
    live, upcoming = [], []
    for item in program(db):
        start, end = _as_utc(item.start_time), _as_utc(item.end_time)
        if start and end and start <= now <= end:
            live.append(item)
        elif start and start > now:
            upcoming.append(item)
    upcoming.sort(key=lambda i: _as_utc(i.start_time))
    return live, upcoming


def my_bookmarks(db: Session, user: User) -> List[ScheduleItem]:
    ids = [row.schedule_item_id for row in db.query(ScheduleBookmark)
           .filter(ScheduleBookmark.user_id == user.id).all()]
    if not ids:
        return []
    return (db.query(ScheduleItem).filter(ScheduleItem.id.in_(ids))
            .order_by(ScheduleItem.start_time).all())


def _attended_today(db: Session, user: User, now: datetime) -> int:
    rows = (db.query(SessionAttendance)
            .filter(SessionAttendance.user_id == user.id).all())
    today = now.date()
    return sum(1 for r in rows
               if _as_utc(r.created_at) and _as_utc(r.created_at).date() == today)


def build_context(db: Session, user: User) -> Dict:
    """The grounded facts every answer is built from."""
    now = datetime.now(timezone.utc)
    live, upcoming = current_and_next(db, now)
    bookmarks = my_bookmarks(db, user)
    attended = (db.query(SessionAttendance)
                .filter(SessionAttendance.user_id == user.id).count())
    return {
        "now": now,
        "live": live,
        "upcoming": upcoming[:8],
        "bookmarks": [b for b in bookmarks if _as_utc(b.start_time) and
                      _as_utc(b.start_time) >= now - timedelta(hours=2)],
        "attended": attended,
        "attended_today": _attended_today(db, user, now),
        "my_sessions": (db.query(ScheduleItem)
                        .filter(ScheduleItem.speaker_id == user.id)
                        .order_by(ScheduleItem.start_time).all()),
    }


def _venue_settings(db: Session) -> Dict:
    """Venue content, whatever the organizers have configured."""
    from app.api.venue import _get_setting
    return {key: _get_setting(db, key)
            for key in ("general", "wifi", "transport", "emergency", "places")}


# ─── Intent routing ──────────────────────────────────────────────
INTENTS: List[Tuple[str, Tuple[str, ...]]] = [
    ("wifi", ("wifi", "wi-fi", "wireless", "internet", "network password",
              "password")),
    ("emergency", ("emergency", "ambulance", "first aid", "first-aid",
                   "security", "police", "doctor", "medical", "hurt", "sick")),
    ("navigate", ("where is", "how do i get", "how to get", "directions",
                  "navigate", "which room", "what room", "find the room",
                  "floor plan", "map of the venue", "lost")),
    ("food", ("eat", "food", "lunch", "dinner", "breakfast", "restaurant",
              "coffee", "cafe", "hungry", "halal", "vegetarian", "vegan")),
    ("stay", ("hotel", "stay", "accommodation", "sleep", "room for the night")),
    ("health", ("pharmacy", "chemist", "medicine", "drugstore")),
    ("money", ("atm", "cash", "money", "exchange", "bank")),
    ("transport", ("metro", "taxi", "uber", "careem", "parking", "airport",
                   "transport", "bus", "how do i get to the venue", "drive")),
    ("certificate", ("certificate", "cme", "cpd", "credit", "credits",
                     "attendance record", "proof of attendance")),
    ("recording", ("recording", "recordings", "video", "watch again", "replay",
                   "missed the", "catch up", "transcript")),
    ("poster", ("poster", "posters", "poster hall", "scavenger")),
    ("paper", ("paper", "papers", "abstract", "manuscript", "review",
               "submission", "peer review")),
    ("people", ("who works on", "who is working on", "meet", "network",
                "networking", "connect with", "find people", "researchers",
                "colleague")),
    ("sponsor", ("sponsor", "sponsors", "exhibitor", "booth", "exhibition")),
    ("feedback", ("rate", "rating", "feedback", "survey", "review the session")),
    ("energy", ("tired", "exhausted", "break", "rest", "burnt out", "burned out",
                "overwhelmed", "too much", "breather")),
    ("myday", ("my schedule", "my day", "my sessions", "my bookmarks",
               "my agenda", "my talks", "what am i")),
    ("recommend", ("what should i", "recommend", "suggest", "worth seeing",
                   "must see", "must-see", "best session", "what's good",
                   "whats good", "anything interesting")),
    ("now", ("what's on", "whats on", "right now", "happening now",
             "currently", "on now")),
    ("next", ("next session", "what's next", "whats next", "coming up",
              "after this", "later today")),
    ("trending", ("trending", "hot topic", "buzz", "popular", "everyone "
                  "talking")),
    ("help", ("help", "what can you do", "how do you work", "who are you")),
]


def detect_intent(question: str) -> Optional[str]:
    text = f" {(question or '').lower().strip()} "
    for intent, phrases in INTENTS:
        if any(p in text for p in phrases):
            return intent
    return None


# ─── Rule-based answers ──────────────────────────────────────────
def _answer_now(db: Session, user: User, ctx: Dict) -> Dict:
    if ctx["live"]:
        lines = [f"**{i.title}** — {i.location or 'location TBA'}, until "
                 f"{_fmt_time(i.end_time)}" for i in ctx["live"]]
        text = "On right now:\n" + "\n".join(lines)
    elif ctx["upcoming"]:
        nxt = ctx["upcoming"][0]
        text = (f"Nothing is running this minute. Next up is **{nxt.title}** "
                f"at {_fmt_when(nxt.start_time)}"
                f"{f' in {nxt.location}' if nxt.location else ''}.")
    else:
        text = "The program has nothing scheduled right now."
    return {"answer": text, "kind": "now",
            "items": [_session_brief(i) for i in (ctx["live"] or ctx["upcoming"][:3])]}


def _answer_next(db: Session, user: User, ctx: Dict) -> Dict:
    if not ctx["upcoming"]:
        return {"answer": "That's the end of the program — nothing further is "
                          "scheduled.", "kind": "next", "items": []}
    booked = {b.id for b in ctx["bookmarks"]}
    lines = []
    for item in ctx["upcoming"][:4]:
        star = " ⭐ (bookmarked)" if item.id in booked else ""
        lines.append(f"**{_fmt_time(item.start_time)}** — {item.title}"
                     f"{f' · {item.location}' if item.location else ''}{star}")
    return {"answer": "Coming up:\n" + "\n".join(lines), "kind": "next",
            "items": [_session_brief(i) for i in ctx["upcoming"][:4]]}


def _answer_myday(db: Session, user: User, ctx: Dict) -> Dict:
    parts = []
    if ctx["my_sessions"]:
        parts.append("You're presenting: " + "; ".join(
            f"{s.title} ({_fmt_when(s.start_time)})" for s in ctx["my_sessions"]))
    if ctx["bookmarks"]:
        parts.append("Your bookmarks coming up:\n" + "\n".join(
            f"**{_fmt_time(b.start_time)}** — {b.title}"
            f"{f' · {b.location}' if b.location else ''}"
            for b in ctx["bookmarks"][:6]))
    else:
        parts.append("You haven't bookmarked anything yet — open the schedule "
                     "and tap the star on sessions you want to keep.")
    parts.append(f"Sessions checked into so far: {ctx['attended']}.")
    return {"answer": "\n\n".join(parts), "kind": "myday",
            "items": [_session_brief(b) for b in ctx["bookmarks"][:6]]}


def _answer_recommend(db: Session, user: User, ctx: Dict,
                      question: str = "") -> Dict:
    index = kg.build_index(db)
    mine = kg.user_topics(db, index, user)
    asked = set(kg.topics_of_text(index, question)) if question else set()
    wanted = asked or mine
    now = ctx["now"]
    scored = []
    for item in ctx["upcoming"]:
        entity = index.entity("session", item.id)
        topics = entity.topics if entity else set()
        overlap = topics & wanted
        score = len(overlap) * 2 + (entity.discussion if entity else 0) * 0.3
        if item.type == ScheduleType.keynote:
            score += 1.5
        scored.append((score, overlap, item))
    scored.sort(key=lambda row: (-row[0], _as_utc(row[2].start_time) or now))
    picks = [row for row in scored if row[0] > 0][:3] or scored[:3]
    if not picks:
        return {"answer": "There's nothing left on the program to recommend.",
                "kind": "recommend", "items": []}
    lines = []
    for score, overlap, item in picks:
        why = (f" — matches your interest in "
               f"{', '.join(kg.label_for(t) for t in sorted(overlap)[:2])}"
               if overlap else "")
        lines.append(f"**{item.title}** ({_fmt_when(item.start_time)}"
                     f"{f', {item.location}' if item.location else ''}){why}")
    lead = ("Based on what you follow, I'd go to:" if wanted
            else "Highlights still to come:")
    return {"answer": lead + "\n" + "\n".join(lines), "kind": "recommend",
            "items": [_session_brief(row[2]) for row in picks]}


def _answer_navigate(db: Session, user: User, ctx: Dict, question: str) -> Dict:
    from app.models.venue import VenueRoom
    rooms = db.query(VenueRoom).all()
    text_q = (question or "").lower()
    target = next((r for r in rooms if r.name.lower() in text_q), None)
    if not target:
        # Maybe they named a session instead of a room.
        for item in program(db):
            if item.title.lower() in text_q and item.location:
                target = next((r for r in rooms
                               if r.name.lower() == item.location.lower()), None)
                if target:
                    break
    if target:
        hint = f" {target.directions_hint}" if target.directions_hint else ""
        return {"answer": f"**{target.name}** is on floor {target.floor}."
                          f"{hint} Open the Venue page for turn-by-turn "
                          f"directions from where you are.",
                "kind": "navigate", "items": [],
                "links": [{"label": "Venue map", "url": "/venue"}]}
    if ctx["live"]:
        item = ctx["live"][0]
        return {"answer": f"Right now **{item.title}** is in "
                          f"{item.location or 'a room yet to be announced'}. "
                          f"The Venue page has the floor plan and "
                          f"turn-by-turn directions.",
                "kind": "navigate", "items": [_session_brief(item)],
                "links": [{"label": "Venue map", "url": "/venue"}]}
    return {"answer": "The Venue page has the interactive floor plan, live "
                      "room overlay and turn-by-turn directions between any "
                      "two rooms.",
            "kind": "navigate", "items": [],
            "links": [{"label": "Venue map", "url": "/venue"}]}


def _answer_wifi(db: Session, user: User, ctx: Dict) -> Dict:
    wifi = _venue_settings(db)["wifi"]
    if not wifi.get("ssid"):
        return {"answer": "WiFi details haven't been published yet.",
                "kind": "wifi", "items": []}
    note = f" {wifi.get('note')}" if wifi.get("note") else ""
    return {"answer": f"WiFi network **{wifi['ssid']}**, password "
                      f"**{wifi.get('password') or '(open network)'}**.{note} "
                      f"The Venue page has a QR that joins it in one tap.",
            "kind": "wifi", "items": [],
            "links": [{"label": "Venue & WiFi", "url": "/venue"}]}


def _answer_places(db: Session, category: str, kind: str, empty: str) -> Dict:
    places = [p for p in _venue_settings(db)["places"]
              if (p.get("category") or "") == category]
    if not places:
        return {"answer": empty, "kind": kind, "items": [],
                "links": [{"label": "Dubai info", "url": "/venue"}]}
    lines = []
    for place in places[:5]:
        distance = f" ({place.get('distance')})" if place.get("distance") else ""
        lines.append(f"**{place.get('name')}** — {place.get('details') or ''}"
                     f"{distance}")
    return {"answer": "\n".join(lines), "kind": kind, "items": [],
            "links": [{"label": "Dubai info", "url": "/venue"}]}


def _answer_transport(db: Session) -> Dict:
    rows = _venue_settings(db)["transport"]
    if not rows:
        return {"answer": "Transport details haven't been published yet.",
                "kind": "transport", "items": []}
    lines = [f"**{r.get('title')}** — {r.get('details') or ''}" for r in rows[:5]]
    return {"answer": "\n".join(lines), "kind": "transport", "items": [],
            "links": [{"label": "Getting here", "url": "/venue"}]}


def _answer_emergency(db: Session) -> Dict:
    rows = _venue_settings(db)["emergency"]
    lines = [f"**{r.get('label')}** — {r.get('phone')}" for r in rows[:6]]
    return {"answer": "Emergency contacts:\n" + "\n".join(lines) if lines
                      else "No emergency contacts are configured yet.",
            "kind": "emergency", "items": [],
            "links": [{"label": "All contacts", "url": "/venue"}]}


def _answer_certificate(db: Session, user: User) -> Dict:
    from app.api.certificates import cert_status
    status = cert_status(db, user)
    lines = []
    for cert in status.certificates:
        state = "unlocked ✅" if cert.unlocked else \
            f"{cert.progress}/{cert.goal} {cert.unit}"
        lines.append(f"**{cert.title}** — {state}")
    return {"answer": f"You've checked into {status.attended_sessions} "
                      f"sessions ({status.credits} credits).\n"
                      + "\n".join(lines),
            "kind": "certificate", "items": [],
            "links": [{"label": "My certificates", "url": "/certificates"}]}


def _answer_recording(db: Session, user: User) -> Dict:
    if not feature_flags.is_enabled("recordings"):
        return {"answer": "Session recordings aren't switched on for this "
                          "congress.", "kind": "recording", "items": []}
    from app.api.recordings import is_available
    recs = db.query(SessionRecording).all()
    live = [r for r in recs if is_available(r)]
    if not live:
        return {"answer": "No recordings are available yet — they appear once "
                          "the speaker consents and organizers publish them.",
                "kind": "recording", "items": [],
                "links": [{"label": "Recordings", "url": "/recordings"}]}
    items = {i.id: i for i in db.query(ScheduleItem).filter(
        ScheduleItem.id.in_([r.schedule_item_id for r in live])).all()}
    lines = [f"**{items[r.schedule_item_id].title}**" for r in live[:5]
             if r.schedule_item_id in items]
    return {"answer": f"{len(live)} recording{'s' if len(live) != 1 else ''} "
                      f"available:\n" + "\n".join(lines) +
                      "\n\nTranscripts are searchable, so you can jump to the "
                      "exact moment a topic came up.",
            "kind": "recording", "items": [],
            "links": [{"label": "Recordings", "url": "/recordings"}]}


def _answer_poster(db: Session, user: User) -> Dict:
    posters = db.query(Poster).filter(Poster.status == "approved").all()
    visited = db.query(PosterVisit).filter(PosterVisit.user_id == user.id).count()
    goal = get_settings().POSTER_HUNT_GOAL
    return {"answer": f"The poster hall has {len(posters)} posters. You've "
                      f"checked in to {visited} of them — the scavenger hunt "
                      f"completes at {goal}.",
            "kind": "poster", "items": [],
            "links": [{"label": "Poster gallery", "url": "/posters"}]}


def _answer_paper(db: Session, user: User) -> Dict:
    mine = db.query(Paper).filter(Paper.author_id == user.id).count()
    reviews = db.query(Review).filter(Review.reviewer_id == user.id,
                                      Review.submitted.is_(False)).count()
    bits = []
    if mine:
        bits.append(f"You have {mine} submission{'s' if mine != 1 else ''}.")
    if reviews:
        bits.append(f"{reviews} review{'s' if reviews != 1 else ''} are still "
                    f"waiting on you.")
    if not bits:
        bits.append("Abstracts and papers live under Papers — submit there and "
                    "track the review decision.")
    return {"answer": " ".join(bits), "kind": "paper", "items": [],
            "links": [{"label": "Papers", "url": "/papers"}]}


def _answer_people(db: Session, user: User, question: str) -> Dict:
    index = kg.build_index(db)
    topics = set(kg.topics_of_text(index, question))
    people = [e for e in index.entities if e.kind == "person"]
    if topics:
        people = [p for p in people if p.topics & topics] or people
    if not people:
        return {"answer": "No one has opted into the networking directory yet.",
                "kind": "people", "items": [],
                "links": [{"label": "Connect", "url": "/connect"}]}
    lines = [f"**{p.title}**{f' — {p.subtitle}' if p.subtitle else ''}"
             f"{' · ' + ', '.join(kg.label_for(t) for t in sorted(p.topics)[:3]) if p.topics else ''}"
             for p in people[:5]]
    lead = (f"Working on {', '.join(kg.label_for(t) for t in sorted(topics)[:2])}:"
            if topics else "People you can reach through the directory:")
    return {"answer": lead + "\n" + "\n".join(lines), "kind": "people",
            "items": [p.brief() for p in people[:5]],
            "links": [{"label": "Connect", "url": "/connect"}]}


def _answer_sponsor(db: Session) -> Dict:
    from app.models.sponsor import Sponsor
    sponsors = (db.query(Sponsor).filter(Sponsor.is_active.is_(True))
                .order_by(Sponsor.display_order).all())
    if not sponsors:
        return {"answer": "No sponsors or exhibitors are listed yet.",
                "kind": "sponsor", "items": []}
    lines = [f"**{s.name}** ({s.tier.title()})"
             f"{f' — booth {s.booth_number}' if s.booth_number else ''}"
             for s in sponsors[:6]]
    return {"answer": "Sponsors and exhibitors:\n" + "\n".join(lines),
            "kind": "sponsor", "items": [],
            "links": [{"label": "Sponsors", "url": "/sponsors"}]}


def _answer_feedback(db: Session, user: User, ctx: Dict) -> Dict:
    rated = {r.schedule_item_id for r in db.query(SessionRating)
             .filter(SessionRating.user_id == user.id).all()}
    attended = {a.schedule_item_id for a in db.query(SessionAttendance)
                .filter(SessionAttendance.user_id == user.id).all()}
    pending = attended - rated
    survey = db.query(SurveyResponse).filter(
        SurveyResponse.user_id == user.id).first()
    bits = []
    if pending:
        bits.append(f"{len(pending)} session{'s' if len(pending) != 1 else ''} "
                    f"you attended {'are' if len(pending) != 1 else 'is'} still "
                    f"unrated — one tap each on the schedule.")
    else:
        bits.append("You've rated everything you attended. Thank you.")
    if not survey:
        bits.append("The post-event survey is still open, and completing it "
                    "unlocks your Participation certificate.")
    return {"answer": " ".join(bits), "kind": "feedback", "items": [],
            "links": [{"label": "Feedback", "url": "/feedback"}]}


def _answer_trending(db: Session) -> Dict:
    index = kg.build_index(db)
    rows = kg.topic_summary(index)[:5]
    if not rows:
        return {"answer": "Not enough of the program is in yet to spot trends.",
                "kind": "trending", "items": []}
    lines = [f"**{r['label']}** — {r['count']} items, {r['discussion']} "
             f"questions and comments" for r in rows]
    return {"answer": "What the congress is talking about:\n" + "\n".join(lines),
            "kind": "trending", "items": [],
            "links": [{"label": "Knowledge map", "url": "/knowledge"}]}


def energy_suggestion(db: Session, user: User, ctx: Dict) -> Dict:
    """Energy-aware: read the day so far and suggest the right next move."""
    now = ctx["now"]
    hour = now.hour
    attended = ctx["attended_today"]
    gap_minutes = None
    if ctx["upcoming"]:
        start = _as_utc(ctx["upcoming"][0].start_time)
        gap_minutes = int((start - now).total_seconds() // 60)

    if attended >= LONG_DAY_SESSIONS:
        text = (f"You've been in {attended} sessions today — that's a full "
                f"day. Take the next slot off: the concourse café is quieter "
                f"than the halls, and the posters are a gentler way to keep "
                f"learning.")
        kind = "rest"
    elif gap_minutes is not None and gap_minutes >= 40:
        text = (f"You have {gap_minutes} minutes before your next session — "
                f"long enough for a proper coffee, a lap of the poster hall, "
                f"or a sponsor booth or two.")
        kind = "gap"
    elif hour >= 16:
        text = ("Late afternoon is when attention dips. Pick one talk you "
                "really want, and use the rest of the day for the poster hall "
                "and conversations — recordings cover the rest.")
        kind = "afternoon"
    elif hour < 10:
        text = ("Morning is your sharpest slot — spend it on the densest talk "
                "on your list and leave the browsing for later.")
        kind = "morning"
    else:
        text = ("You're pacing well. Keep one slot free today for the poster "
                "hall — that's where the conversations happen.")
        kind = "steady"
    return {"kind": kind, "text": text,
            "attended_today": attended, "gap_minutes": gap_minutes}


def _answer_energy(db: Session, user: User, ctx: Dict) -> Dict:
    suggestion = energy_suggestion(db, user, ctx)
    return {"answer": suggestion["text"], "kind": "energy", "items": []}


def _answer_help(db: Session, user: User) -> Dict:
    return {
        "answer": "Ask me anything about the congress — I answer from the live "
                  "program, so the times, rooms and numbers are always current."
                  "\n\nTry: *what's on now*, *what should I see next*, *where "
                  "is Hall 2*, *what's the WiFi*, *how am I doing on CME "
                  "credits*, *who works on organoids*, *I'm exhausted*, "
                  "*surprise me*.",
        "kind": "help", "items": []}


def _answer_topic_match(db: Session, user: User, question: str) -> Optional[Dict]:
    """No intent matched, but the question names a topic in the program."""
    index = kg.build_index(db)
    topics = kg.topics_of_text(index, question)
    if not topics:
        return None
    topic = topics[0]
    entities = index.by_topic.get(topic, [])
    sessions = [e for e in entities if e.kind == "session"][:4]
    others = [e for e in entities if e.kind != "session"][:3]
    lines = [f"**{e.title}** — {_fmt_when(e.when)}"
             f"{f' · {e.location}' if e.location else ''}" for e in sessions]
    lines += [f"**{e.title}** ({e.kind})" for e in others]
    if not lines:
        return None
    return {"answer": f"On **{kg.label_for(topic)}**:\n" + "\n".join(lines),
            "kind": "topic", "items": [e.brief() for e in sessions + others],
            "links": [{"label": "Knowledge map", "url": "/knowledge"}]}


# ─── Claude integration (optional) ───────────────────────────────
def llm_available() -> bool:
    """True when free-form questions can be sent to Claude."""
    if not get_settings().ANTHROPIC_API_KEY:
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


SYSTEM_PROMPT = (
    "You are the congress companion for {congress}, a scientific conference. "
    "Answer the attendee's question using ONLY the congress facts provided "
    "below. If the facts don't cover it, say so plainly and point them at the "
    "right page of the app instead of guessing. Never invent session titles, "
    "times, rooms, names or numbers. Keep answers under 120 words, warm and "
    "practical, and use the attendee's own framing. Times are UTC."
)


def _llm_facts(db: Session, user: User, ctx: Dict) -> str:
    """A compact, factual brief for the model — no PII beyond the program."""
    lines = [f"Attendee: {user.full_name} ({user.role.value}).",
             f"Current time (UTC): {ctx['now'].strftime('%A %d %B %Y, %H:%M')}.",
             f"Sessions attended so far: {ctx['attended']}."]
    if ctx["live"]:
        lines.append("Happening now: " + "; ".join(
            f"{i.title} in {i.location or 'TBA'} until {_fmt_time(i.end_time)}"
            for i in ctx["live"]))
    if ctx["upcoming"]:
        lines.append("Upcoming sessions: " + "; ".join(
            f"{i.title} at {_fmt_when(i.start_time)} in {i.location or 'TBA'}"
            for i in ctx["upcoming"][:8]))
    if ctx["bookmarks"]:
        lines.append("Their bookmarks: " + "; ".join(
            f"{b.title} at {_fmt_when(b.start_time)}" for b in ctx["bookmarks"][:6]))
    if ctx["my_sessions"]:
        lines.append("They are presenting: " + "; ".join(
            f"{s.title} at {_fmt_when(s.start_time)}" for s in ctx["my_sessions"]))
    venue = _venue_settings(db)
    general, wifi = venue["general"], venue["wifi"]
    lines.append(f"Venue: {general.get('venue_name')} — {general.get('address')}.")
    if wifi.get("ssid"):
        lines.append(f"WiFi SSID {wifi['ssid']}, password "
                     f"{wifi.get('password') or 'none (open)'}.")
    if venue["emergency"]:
        lines.append("Emergency: " + "; ".join(
            f"{e.get('label')} {e.get('phone')}" for e in venue["emergency"][:4]))
    if venue["places"]:
        lines.append("Nearby: " + "; ".join(
            f"{p.get('name')} ({p.get('category')}, {p.get('distance')})"
            for p in venue["places"][:6]))
    index = kg.build_index(db)
    trending = kg.topic_summary(index)[:6]
    if trending:
        lines.append("Trending topics: " + ", ".join(
            f"{t['label']} ({t['count']} items)" for t in trending))
    lines.append("App pages: /schedule, /posters, /papers, /connect, /venue, "
                 "/certificates, /feedback, /recordings, /knowledge.")
    return "\n".join(lines)


def ask_claude(db: Session, user: User, ctx: Dict, question: str) -> Optional[str]:
    """Free-form answer from Claude, grounded in the facts. None on any failure."""
    settings = get_settings()
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        congress = f"{settings.CONGRESS_NAME} {settings.CONGRESS_YEAR}"
        response = client.messages.create(
            model=settings.COMPANION_MODEL,
            max_tokens=settings.COMPANION_MAX_TOKENS,
            system=SYSTEM_PROMPT.format(congress=congress),
            thinking={"type": "adaptive"},
            messages=[{
                "role": "user",
                "content": f"Congress facts:\n{_llm_facts(db, user, ctx)}\n\n"
                           f"Attendee question: {question}",
            }],
        )
        if getattr(response, "stop_reason", None) == "refusal":
            return None
        text = "".join(block.text for block in response.content
                       if getattr(block, "type", "") == "text").strip()
        return text or None
    except Exception as exc:  # network, auth, rate limit, SDK change…
        logger.warning("Companion LLM call failed: %s", exc)
        return None


# ─── The public entry point ──────────────────────────────────────
def answer(db: Session, user: User, question: str, *,
           allow_llm: bool = True) -> Dict:
    """Answer one question. Always returns something useful."""
    question = (question or "").strip()
    ctx = build_context(db, user)
    if not question:
        result = _answer_help(db, user)
        result["via"] = "rules"
        return result

    intent = detect_intent(question)
    handlers = {
        "now": lambda: _answer_now(db, user, ctx),
        "next": lambda: _answer_next(db, user, ctx),
        "myday": lambda: _answer_myday(db, user, ctx),
        "recommend": lambda: _answer_recommend(db, user, ctx, question),
        "navigate": lambda: _answer_navigate(db, user, ctx, question),
        "wifi": lambda: _answer_wifi(db, user, ctx),
        "food": lambda: _answer_places(
            db, "restaurant", "food",
            "No food options are listed yet — the venue concourse usually has "
            "a food court."),
        "stay": lambda: _answer_places(
            db, "hotel", "stay", "No hotels are listed yet."),
        "health": lambda: _answer_places(
            db, "pharmacy", "health", "No pharmacies are listed yet."),
        "money": lambda: _answer_places(
            db, "atm", "money", "No ATMs are listed yet."),
        "transport": lambda: _answer_transport(db),
        "emergency": lambda: _answer_emergency(db),
        "certificate": lambda: _answer_certificate(db, user),
        "recording": lambda: _answer_recording(db, user),
        "poster": lambda: _answer_poster(db, user),
        "paper": lambda: _answer_paper(db, user),
        "people": lambda: _answer_people(db, user, question),
        "sponsor": lambda: _answer_sponsor(db),
        "feedback": lambda: _answer_feedback(db, user, ctx),
        "trending": lambda: _answer_trending(db),
        "energy": lambda: _answer_energy(db, user, ctx),
        "help": lambda: _answer_help(db, user),
    }
    if intent in handlers:
        result = handlers[intent]()
        result["via"] = "rules"
        result["intent"] = intent
        return result

    topical = _answer_topic_match(db, user, question)
    if topical:
        topical["via"] = "rules"
        topical["intent"] = "topic"
        return topical

    if allow_llm and llm_available():
        text = ask_claude(db, user, ctx, question)
        if text:
            return {"answer": text, "kind": "freeform", "items": [],
                    "via": "claude", "intent": None}

    fallback = _answer_help(db, user)
    fallback["answer"] = ("I couldn't match that to the program. " +
                          fallback["answer"])
    fallback["via"] = "rules"
    fallback["intent"] = "fallback"
    return fallback


# ─── Proactive: nudges, briefing, serendipity ────────────────────
def nudges(db: Session, user: User) -> List[Dict]:
    """Timely, specific prompts — the "tap on the shoulder" part of the spec."""
    ctx = build_context(db, user)
    now = ctx["now"]
    out: List[Dict] = []

    # A bookmarked session about to start (and where it is).
    for item in ctx["bookmarks"]:
        start = _as_utc(item.start_time)
        if not start:
            continue
        minutes = (start - now).total_seconds() / 60
        if 0 <= minutes <= SOON_MINUTES:
            out.append({
                "kind": "starting", "urgency": "high",
                "title": f"{item.title} starts in {int(minutes)} min",
                "body": f"You bookmarked this."
                        f"{f' It is in {item.location}.' if item.location else ''}",
                "link": "/schedule"})

    # Speaking soon → prep.
    for item in ctx["my_sessions"]:
        start = _as_utc(item.start_time)
        if start and 0 <= (start - now).total_seconds() / 60 <= 120:
            out.append({
                "kind": "speaker_prep", "urgency": "high",
                "title": f"You present in {int((start - now).total_seconds() // 60)} min",
                "body": f"'{item.title}' — open speaker prep for your room, "
                        f"audience size and the questions already waiting.",
                "link": "/companion"})

    # Certificate within reach.
    from app.api.certificates import cert_status
    status = cert_status(db, user)
    for cert in status.certificates:
        # "One more" only makes sense once they're actually on the ladder —
        # don't nudge an attendee about the speaker certificate.
        if not cert.unlocked and cert.progress > 0 and cert.goal - cert.progress == 1:
            out.append({
                "kind": "certificate", "urgency": "medium",
                "title": f"One more to unlock your {cert.title}",
                "body": f"{cert.progress}/{cert.goal} {cert.unit}. "
                        f"Scan the QR at the door of your next session.",
                "link": "/certificates"})

    # Unrated sessions.
    rated = {r.schedule_item_id for r in db.query(SessionRating)
             .filter(SessionRating.user_id == user.id).all()}
    attended = {a.schedule_item_id for a in db.query(SessionAttendance)
                .filter(SessionAttendance.user_id == user.id).all()}
    if len(attended - rated) >= 2:
        out.append({
            "kind": "rate", "urgency": "low",
            "title": f"{len(attended - rated)} sessions still unrated",
            "body": "One tap each — speakers get the anonymized summary.",
            "link": "/schedule"})

    # Reviews due.
    pending = db.query(Review).filter(Review.reviewer_id == user.id,
                                      Review.submitted.is_(False)).count()
    if pending:
        out.append({
            "kind": "review", "urgency": "medium",
            "title": f"{pending} review{'s' if pending != 1 else ''} waiting",
            "body": "Papers assigned to you haven't been submitted yet.",
            "link": "/papers"})

    # Recording consent pending on one of their talks.
    if feature_flags.is_enabled("recordings") and ctx["my_sessions"]:
        ids = [s.id for s in ctx["my_sessions"]]
        waiting = db.query(SessionRecording).filter(
            SessionRecording.schedule_item_id.in_(ids),
            SessionRecording.consent_state == "pending").count()
        if waiting:
            out.append({
                "kind": "consent", "urgency": "medium",
                "title": "Recording consent needed",
                "body": f"{waiting} of your talks were recorded and can't be "
                        f"shared until you decide.",
                "link": "/recordings"})

    # Post-event survey.
    if feature_flags.is_enabled("feedback"):
        done = db.query(SurveyResponse).filter(
            SurveyResponse.user_id == user.id).first()
        if not done and ctx["attended"] >= 2:
            out.append({
                "kind": "survey", "urgency": "low",
                "title": "The post-event survey unlocks a certificate",
                "body": "Two minutes, and it shapes next year's program.",
                "link": "/feedback"})

    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda n: order.get(n["urgency"], 3))
    return out


def briefing(db: Session, user: User) -> Dict:
    """The day at a glance, plus what to do about it."""
    ctx = build_context(db, user)
    now = ctx["now"]
    today = [i for i in program(db)
             if _as_utc(i.start_time) and _as_utc(i.start_time).date() == now.date()]
    greeting = ("Good morning" if now.hour < 12 else
                "Good afternoon" if now.hour < 18 else "Good evening")
    return {
        "greeting": f"{greeting}, {user.full_name.split(' ')[0]}",
        "date": now.strftime("%A %d %B %Y"),
        "sessions_today": len(today),
        "live": [_session_brief(i) for i in ctx["live"]],
        "next": [_session_brief(i) for i in ctx["upcoming"][:3]],
        "my_bookmarks": [_session_brief(b) for b in ctx["bookmarks"][:4]],
        "presenting": [_session_brief(s) for s in ctx["my_sessions"]],
        "attended": ctx["attended"],
        "energy": energy_suggestion(db, user, ctx),
        "nudges": nudges(db, user)[:4],
    }


def serendipity(db: Session, user: User) -> Dict:
    """Serendipity mode — something good that isn't in your usual lane."""
    index = kg.build_index(db)
    mine = kg.user_topics(db, index, user)
    suggestions = kg.cross_pollination(index, mine, limit=3)
    now = datetime.now(timezone.utc)
    picks: List[Dict] = []
    seen = set()
    for suggestion in suggestions:
        for item in suggestion["items"]:
            key = (item["kind"], item["id"])
            if key in seen:
                continue
            when = _as_utc(item.get("when"))
            if item["kind"] == "session" and when and when < now:
                continue  # a finished talk isn't a suggestion
            seen.add(key)
            picks.append({**item, "because": suggestion["because"],
                          "topic": suggestion["label"]})
            break
    if not picks:
        # No adjacency yet — offer the liveliest thing outside their topics.
        pool = [e for e in index.entities
                if e.kind in ("session", "poster") and not (e.topics & mine)]
        pool.sort(key=lambda e: (-e.discussion, e.title))
        picks = [{**e.brief(),
                  "because": "Outside your usual topics, and the congress is "
                             "talking about it",
                  "topic": kg.label_for(sorted(e.topics)[0]) if e.topics else ""}
                 for e in pool[:2]]
    return {"picks": picks,
            "my_topics": [kg.label_for(t) for t in sorted(mine)]}


def speaker_prep(db: Session, user: User, session_id: int) -> Dict:
    """Everything a presenter wants in the ten minutes before they walk on."""
    item = db.query(ScheduleItem).filter(ScheduleItem.id == session_id).first()
    if not item:
        return {}
    audience = db.query(ScheduleBookmark).filter(
        ScheduleBookmark.schedule_item_id == item.id).count()
    checked_in = db.query(SessionAttendance).filter(
        SessionAttendance.schedule_item_id == item.id).count()
    questions = (db.query(Question)
                 .filter(Question.schedule_item_id == item.id,
                         Question.status == QuestionStatus.open)
                 .order_by(Question.upvotes.desc()).limit(5).all())
    index = kg.build_index(db)
    entity = index.entity("session", item.id)
    related = kg.related_to(index, entity, limit=4) if entity else []
    room = None
    if item.location:
        from app.models.venue import VenueRoom
        room = next((r for r in db.query(VenueRoom).all()
                     if r.name.strip().lower() == item.location.strip().lower()),
                    None)
    ratings = db.query(SessionRating).filter(
        SessionRating.schedule_item_id == item.id).all()
    return {
        "session": _session_brief(item),
        "starts_in_minutes": int(((_as_utc(item.start_time) or datetime.now(
            timezone.utc)) - datetime.now(timezone.utc)).total_seconds() // 60),
        "audience": {"bookmarked": audience, "checked_in": checked_in},
        "room": {"name": room.name, "floor": room.floor,
                 "hint": room.directions_hint} if room else
                ({"name": item.location} if item.location else None),
        "top_questions": [{"id": q.id, "text": q.text, "upvotes": q.upvotes}
                          for q in questions],
        "related": related,
        "ratings": {"count": len(ratings),
                    "average": round(sum(r.stars for r in ratings) / len(ratings), 2)
                    if ratings else None},
        "checklist": [
            "Slides loaded and in presentation mode",
            f"Room: {item.location or 'check the venue map'}",
            f"{audience} attendee{'s' if audience != 1 else ''} bookmarked this",
            f"{len(questions)} question{'s' if len(questions) != 1 else ''} "
            f"already waiting in live Q&A",
            "Recording consent decided" if feature_flags.is_enabled("recordings")
            else "Ask the chair about timing signals",
        ],
    }


def session_summary(db: Session, user: User, session_id: int) -> Dict:
    """A smart summary of one session: what it is, its buzz, what's next."""
    item = db.query(ScheduleItem).filter(ScheduleItem.id == session_id).first()
    if not item:
        return {}
    questions = (db.query(Question)
                 .filter(Question.schedule_item_id == item.id,
                         Question.status != QuestionStatus.hidden)
                 .order_by(Question.upvotes.desc()).limit(5).all())
    ratings = db.query(SessionRating).filter(
        SessionRating.schedule_item_id == item.id).all()
    index = kg.build_index(db)
    entity = index.entity("session", item.id)
    recording = db.query(SessionRecording).filter(
        SessionRecording.schedule_item_id == item.id).first()
    from app.api.recordings import is_available
    lines = [f"{item.title} — {_fmt_when(item.start_time)}"
             f"{f' in {item.location}' if item.location else ''}."]
    if item.description:
        lines.append(item.description.strip()[:400])
    if entity and entity.topics:
        lines.append("Topics: " + ", ".join(
            kg.label_for(t) for t in sorted(entity.topics)[:5]) + ".")
    if ratings:
        lines.append(f"Rated {round(sum(r.stars for r in ratings) / len(ratings), 1)}"
                     f"/5 by {len(ratings)} attendee"
                     f"{'s' if len(ratings) != 1 else ''}.")
    if questions:
        lines.append(f"The room asked {len(questions)} question"
                     f"{'s' if len(questions) != 1 else ''}, top of the list: "
                     f"\"{questions[0].text[:160]}\"")
    return {
        "session": _session_brief(item),
        "summary": " ".join(lines),
        "topics": sorted(entity.topics) if entity else [],
        "top_questions": [{"text": q.text, "upvotes": q.upvotes} for q in questions],
        "rating": round(sum(r.stars for r in ratings) / len(ratings), 2)
        if ratings else None,
        "recording_available": bool(recording and is_available(recording)),
        "related": kg.related_to(index, entity, limit=4) if entity else [],
    }


def dubai_guide(db: Session) -> Dict:
    """The Dubai guide, straight from what organizers configured."""
    venue = _venue_settings(db)
    grouped: Dict[str, List[Dict]] = {}
    for place in venue["places"]:
        grouped.setdefault(place.get("category") or "other", []).append(place)
    return {
        "venue": venue["general"],
        "wifi": {k: v for k, v in venue["wifi"].items() if k != "password"},
        "transport": venue["transport"],
        "emergency": venue["emergency"],
        "places": grouped,
    }
