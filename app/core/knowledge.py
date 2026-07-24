"""
Knowledge graph engine.

Builds a topic map of the congress out of what is already in the database —
sessions, accepted papers, approved posters and the researchers presenting
them — with no extra data entry. Topics come from three sources, most trusted
first:

1. **Curated fields** — a paper/poster ``category`` and a user's declared
   ``research_interests`` are topics as-is.
2. **A domain phrase list** (``TOPIC_PHRASES``) matched against titles and
   abstracts, so "induced pluripotent stem cells" and "iPSC" land on one node.
3. **Corpus keywords** — significant words that appear in at least two items,
   which keeps one-off wording out of the map.

Everything downstream (related items, research threads, trending topics,
cross-pollination, the personal map) is derived from that index, so the graph
stays correct as the program changes without any refresh job.

Privacy: only public program data is used. People appear if they present a
session or opted into the networking directory (DATA-06); their interests are
only read when they opted in.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.models.paper import Paper, PaperStatus
from app.models.poster import Poster, PosterComment
from app.models.qa import Question, QuestionStatus
from app.models.schedule import ScheduleItem, ScheduleType, ScheduleBookmark
from app.models.attendance import SessionAttendance
from app.models.user import User

# Words that carry no topical signal in an abstract.
STOPWORDS = {
    "about", "after", "again", "against", "along", "also", "among", "and",
    "another", "any", "are", "around", "aspects", "based", "because", "been",
    "before", "being", "below", "between", "both", "but", "can", "case",
    "cases", "could", "current", "data", "day", "days", "demonstrate",
    "developed", "different", "does", "during", "each", "effect", "effects",
    "either", "from", "further", "given", "group", "groups", "had", "has",
    "have", "here", "high", "higher", "how", "however", "into", "introduction",
    "its", "itself", "keynote", "large", "level", "levels", "like", "long",
    "look", "low", "made", "main", "major", "make", "many", "may", "method",
    "methods", "might", "more", "most", "much", "must", "new", "next", "non",
    "not", "note", "novel", "now", "number", "observed", "one", "only",
    "other", "others", "our", "out", "over", "overview", "panel", "paper",
    "part", "patients", "per", "possible", "poster", "potential", "present",
    "presentation", "presented", "previous", "provide", "recent", "report",
    "research", "result", "results", "review", "same", "section", "seen",
    "series", "session", "several", "she", "should", "show", "showed",
    "shown", "shows", "significant", "since", "small", "some", "study",
    "studies", "such", "system", "systems", "take", "talk", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "those",
    "three", "through", "time", "times", "two", "under", "update", "use",
    "used", "uses", "using", "very", "was", "way", "welcome", "were", "what",
    "when", "where", "which", "while", "who", "why", "will", "with", "within",
    "without", "work", "works", "would", "year", "years", "you", "your",
}

# Domain phrases collapsed onto one canonical topic key. Order matters only
# for readability — matching tries every entry.
TOPIC_PHRASES: Dict[str, Tuple[str, ...]] = {
    "stem cell": ("stem cell", "stem cells"),
    "ipsc": ("ipsc", "ipscs", "induced pluripotent", "pluripotent stem cell"),
    "mesenchymal": ("mesenchymal", "msc", "mscs"),
    "hematopoietic": ("hematopoietic", "haematopoietic", "hsc", "hscs"),
    "organoid": ("organoid", "organoids", "organ-on-chip", "organ on a chip"),
    "crispr": ("crispr", "cas9", "crispr-cas9"),
    "gene editing": ("gene editing", "genome editing", "base editing"),
    "gene therapy": ("gene therapy", "gene therapies"),
    "cell therapy": ("cell therapy", "cell therapies", "cellular therapy"),
    "regenerative medicine": ("regenerative medicine", "regeneration"),
    "tissue engineering": ("tissue engineering", "scaffold", "scaffolds"),
    "bioprinting": ("bioprinting", "3d printing", "bioprinted"),
    "clinical trial": ("clinical trial", "clinical trials", "phase i",
                       "phase ii", "phase iii", "first-in-human"),
    "immunotherapy": ("immunotherapy", "immuno-oncology", "checkpoint"),
    "car-t": ("car-t", "car t", "cart cell", "chimeric antigen"),
    "bone marrow": ("bone marrow", "marrow transplant"),
    "transplantation": ("transplantation", "transplant", "graft", "allograft"),
    "single-cell": ("single-cell", "single cell", "scrna", "sc-rna"),
    "exosome": ("exosome", "exosomes", "extracellular vesicle"),
    "biomanufacturing": ("biomanufacturing", "bioprocess", "gmp",
                         "good manufacturing practice", "scale-up"),
    "biobank": ("biobank", "biobanking", "cell bank"),
    "differentiation": ("differentiation", "differentiated", "lineage"),
    "microenvironment": ("microenvironment", "niche", "stroma"),
    "senescence": ("senescence", "aging", "ageing", "senescent"),
    "neuroscience": ("neural", "neuro", "neurodegenerative", "parkinson",
                     "alzheimer", "spinal cord"),
    "cardiac": ("cardiac", "cardiomyocyte", "heart", "myocardial"),
    "diabetes": ("diabetes", "islet", "beta cell", "insulin"),
    "oncology": ("oncology", "cancer", "tumor", "tumour", "leukemia",
                 "leukaemia"),
    "ophthalmology": ("retina", "retinal", "cornea", "ocular"),
    "dermatology": ("skin", "wound healing", "burn"),
    "ethics": ("ethics", "ethical", "consent", "embryo"),
    "regulatory": ("regulatory", "regulation", "fda", "ema", "approval",
                   "reimbursement"),
    "ai": ("machine learning", "artificial intelligence", "deep learning",
           "ai", "computational"),
    "biomarker": ("biomarker", "biomarkers", "diagnostic"),
    "microbiome": ("microbiome", "microbiota"),
}

# Pretty labels for keys whose title-case form would be wrong.
TOPIC_LABELS = {
    "ipsc": "iPSC", "crispr": "CRISPR", "car-t": "CAR-T", "ai": "AI",
    "single-cell": "Single-cell", "msc": "MSC",
}

_WORD_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")
MIN_DF = 2          # a keyword must appear in at least this many items
MAX_ITEM_TOPICS = 8  # keep each node readable


def _when_key(entity: "Entity") -> datetime:
    """Sort key for program order. SQLite hands back naive datetimes, so
    normalize everything to aware UTC before comparing."""
    value = entity.when
    if value is None:
        return datetime.max.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def label_for(topic: str) -> str:
    if topic in TOPIC_LABELS:
        return TOPIC_LABELS[topic]
    return topic[:1].upper() + topic[1:]


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens, lightly singularized, stopwords removed."""
    out = []
    for raw in _WORD_RE.findall((text or "").lower()):
        word = _singular(raw.strip("-"))
        if len(word) < 4 or word in STOPWORDS or _singular(word) in STOPWORDS:
            continue
        out.append(word)
    return out


# Whole-word matchers, so "ema" doesn't fire on "hematopoietic" and "ai"
# doesn't fire on "trial".
_PHRASE_RE = {
    key: re.compile("|".join(
        rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])" for p in phrases))
    for key, phrases in TOPIC_PHRASES.items()
}


def phrase_topics(text: str) -> Set[str]:
    """Topics from the curated domain phrase list."""
    haystack = (text or "").lower()
    return {key for key, pattern in _PHRASE_RE.items() if pattern.search(haystack)}


def normalize_topic(value: str) -> str:
    """A curated field (category / interest / query) becomes a topic key.

    Values that name a known domain phrase collapse onto its canonical key, so
    a "organoids" interest and an "Organoid models…" title land on one node.
    """
    value = re.sub(r"\s+", " ", (value or "").strip().lower())[:60]
    if not value:
        return ""
    for key, phrases in TOPIC_PHRASES.items():
        if value == key or value in phrases:
            return key
    return _singular(value) if " " not in value else value


@dataclass
class Entity:
    kind: str            # session | paper | poster | person
    id: int
    title: str
    subtitle: str = ""
    when: Optional[datetime] = None
    location: Optional[str] = None
    url: str = ""
    topics: Set[str] = field(default_factory=set)
    discussion: int = 0  # Q&A questions / poster comments — "buzz"

    def brief(self) -> Dict:
        return {
            "kind": self.kind, "id": self.id, "title": self.title,
            "subtitle": self.subtitle, "when": self.when,
            "location": self.location, "url": self.url,
            "topics": sorted(self.topics), "discussion": self.discussion,
        }


@dataclass
class GraphIndex:
    entities: List[Entity]
    by_topic: Dict[str, List[Entity]]
    co_occurrence: Dict[Tuple[str, str], int]

    def topic_keys(self) -> List[str]:
        return sorted(self.by_topic, key=lambda t: (-len(self.by_topic[t]), t))

    def entity(self, kind: str, entity_id: int) -> Optional[Entity]:
        return next((e for e in self.entities
                     if e.kind == kind and e.id == entity_id), None)


def _assign_keywords(raw_texts: Dict[Tuple[str, int], str],
                     curated: Dict[Tuple[str, int], Set[str]]) -> Dict[Tuple[str, int], Set[str]]:
    """Second pass: promote corpus keywords that appear in ≥ MIN_DF items."""
    per_item_tokens = {key: set(tokenize(text)) for key, text in raw_texts.items()}
    document_freq: Dict[str, int] = {}
    for tokens in per_item_tokens.values():
        for token in tokens:
            document_freq[token] = document_freq.get(token, 0) + 1
    out: Dict[Tuple[str, int], Set[str]] = {}
    for key, tokens in per_item_tokens.items():
        topics = set(curated.get(key, set()))
        extra = sorted((t for t in tokens if document_freq.get(t, 0) >= MIN_DF),
                       key=lambda t: (-document_freq[t], t))
        for token in extra:
            if len(topics) >= MAX_ITEM_TOPICS:
                break
            topics.add(token)
        out[key] = topics
    return out


def build_index(db: Session) -> GraphIndex:
    """Build the whole graph from current program data."""
    entities: List[Entity] = []
    raw_texts: Dict[Tuple[str, int], str] = {}
    curated: Dict[Tuple[str, int], Set[str]] = {}

    # ── Sessions (the program itself) ──
    sessions = [s for s in db.query(ScheduleItem).all()
                if s.type != ScheduleType.break_]
    speaker_ids = {s.speaker_id for s in sessions if s.speaker_id}
    accounts = {}
    if speaker_ids:
        accounts = {u.id: u for u in db.query(User).filter(User.id.in_(speaker_ids)).all()}
    q_counts: Dict[int, int] = {}
    for question in db.query(Question).filter(
            Question.status != QuestionStatus.hidden).all():
        q_counts[question.schedule_item_id] = q_counts.get(
            question.schedule_item_id, 0) + 1

    for item in sessions:
        extra = item.extra or {}
        speaker = (accounts[item.speaker_id].full_name
                   if item.speaker_id in accounts else extra.get("speaker") or "")
        text = " ".join(filter(None, [item.title, item.description or "",
                                      str(extra.get("track") or ""),
                                      str(extra.get("keywords") or "")]))
        key = ("session", item.id)
        raw_texts[key] = text
        topics = phrase_topics(text)
        if extra.get("track"):
            topics.add(normalize_topic(str(extra["track"])))
        curated[key] = {t for t in topics if t}
        entities.append(Entity(
            kind="session", id=item.id, title=item.title,
            subtitle=speaker, when=item.start_time, location=item.location,
            url=f"/schedule#session-{item.id}",
            discussion=q_counts.get(item.id, 0)))

    # ── Accepted papers (public part of the program) ──
    for paper in db.query(Paper).filter(Paper.status == PaperStatus.accepted).all():
        key = ("paper", paper.id)
        text = " ".join(filter(None, [paper.title, paper.abstract or "",
                                      paper.category or ""]))
        raw_texts[key] = text
        topics = phrase_topics(text)
        if paper.category:
            topics.add(normalize_topic(paper.category))
        curated[key] = {t for t in topics if t}
        entities.append(Entity(
            kind="paper", id=paper.id, title=paper.title,
            subtitle=paper.authors or "", url="/papers",
            when=paper.created_at))

    # ── Approved posters ──
    poster_comments: Dict[int, int] = {}
    for comment in db.query(PosterComment).all():
        poster_comments[comment.poster_id] = poster_comments.get(
            comment.poster_id, 0) + 1
    for poster in db.query(Poster).filter(Poster.status == "approved").all():
        key = ("poster", poster.id)
        text = " ".join(filter(None, [poster.title, poster.abstract or "",
                                      poster.category or ""]))
        raw_texts[key] = text
        topics = phrase_topics(text)
        if poster.category:
            topics.add(normalize_topic(poster.category))
        curated[key] = {t for t in topics if t}
        entities.append(Entity(
            kind="poster", id=poster.id, title=poster.title,
            subtitle=poster.authors or "", url=f"/posters#poster-{poster.id}",
            when=poster.created_at,
            discussion=poster_comments.get(poster.id, 0)))

    # ── People: session presenters + networking opt-ins (DATA-06) ──
    people: Dict[int, User] = {uid: u for uid, u in accounts.items()}
    for user in db.query(User).filter(User.networking_visible.is_(True)).all():
        people[user.id] = user
    session_topics_by_speaker: Dict[int, Set[str]] = {}
    for item in sessions:
        if item.speaker_id:
            session_topics_by_speaker.setdefault(item.speaker_id, set()).update(
                curated.get(("session", item.id), set()))
    for uid, user in people.items():
        key = ("person", uid)
        interests = list(user.research_interests or []) if user.networking_visible else []
        raw_texts[key] = " ".join([user.bio or ""] + interests)
        topics = set(session_topics_by_speaker.get(uid, set()))
        for interest in interests:
            normalized = normalize_topic(interest)
            if normalized:
                topics.add(normalized)
            topics |= phrase_topics(interest)
        curated[key] = topics
        entities.append(Entity(
            kind="person", id=uid, title=user.full_name,
            subtitle=user.institution or "",
            url="/connect" if user.networking_visible else "/schedule"))

    # ── Keyword pass + attach topics ──
    resolved = _assign_keywords(raw_texts, curated)
    curated_all: Set[str] = set()
    for topics in curated.values():
        curated_all |= topics
    by_topic: Dict[str, List[Entity]] = {}
    for entity in entities:
        entity.topics = {t for t in resolved.get((entity.kind, entity.id), set()) if t}
        for topic in entity.topics:
            by_topic.setdefault(topic, []).append(entity)

    # A one-off *keyword* is noise on the map; a one-off curated topic (a
    # category, a declared interest, a domain phrase) is real and searchable,
    # so it stays even when only one item carries it.
    by_topic = {t: items for t, items in by_topic.items()
                if len(items) >= 2 or t in curated_all}
    live = set(by_topic)
    for entity in entities:
        entity.topics &= live

    co_occurrence: Dict[Tuple[str, str], int] = {}
    for entity in entities:
        topics = sorted(entity.topics)
        for i, a in enumerate(topics):
            for b in topics[i + 1:]:
                co_occurrence[(a, b)] = co_occurrence.get((a, b), 0) + 1

    return GraphIndex(entities=entities, by_topic=by_topic,
                      co_occurrence=co_occurrence)


# ─── Derived views ───────────────────────────────────────────────
def topic_summary(index: GraphIndex) -> List[Dict]:
    """Every topic with its size, item mix and discussion volume."""
    out = []
    for topic, items in index.by_topic.items():
        kinds: Dict[str, int] = {}
        for item in items:
            kinds[item.kind] = kinds.get(item.kind, 0) + 1
        out.append({
            "topic": topic, "label": label_for(topic), "count": len(items),
            "kinds": kinds,
            "discussion": sum(i.discussion for i in items),
            "sessions": kinds.get("session", 0),
        })
    out.sort(key=lambda t: (-t["discussion"], -t["count"], t["topic"]))
    return out


def related_to(index: GraphIndex, entity: Entity, limit: int = 8) -> List[Dict]:
    """Items worth seeing next, each with a plain-English reason."""
    scored = []
    for other in index.entities:
        if other.kind == entity.kind and other.id == entity.id:
            continue
        shared = entity.topics & other.topics
        if not shared:
            continue
        # Overlap, normalized so a broad node doesn't dominate everything.
        union = entity.topics | other.topics
        score = len(shared) + len(shared) / max(1, len(union))
        if other.kind == "person":
            score -= 0.4  # prefer content over people in "what's next"
        top = sorted(shared, key=lambda t: len(index.by_topic.get(t, [])))[:3]
        names = ", ".join(label_for(t) for t in top)
        reason = f"Also about {names}" if len(top) > 1 else f"Also about {names}"
        if entity.subtitle and entity.subtitle == other.subtitle:
            score += 0.5
            reason += f" — same presenter ({other.subtitle})"
        scored.append((score, other, reason, sorted(shared)))
    scored.sort(key=lambda row: (-row[0], row[1].title))
    out = []
    for score, other, reason, shared in scored[:limit]:
        brief = other.brief()
        brief["reason"] = reason
        brief["shared_topics"] = [label_for(t) for t in shared]
        brief["score"] = round(score, 2)
        out.append(brief)
    return out


def thread_for(index: GraphIndex, topic: str) -> Dict:
    """A research thread: every item on a topic, in program order."""
    items = index.by_topic.get(topic, [])
    sessions = sorted([e for e in items if e.kind == "session"], key=_when_key)
    days: List[Dict] = []
    for session in sessions:
        when = session.when
        day = when.strftime("%A %d %B") if when else "Unscheduled"
        if not days or days[-1]["day"] != day:
            days.append({"day": day, "items": []})
        days[-1]["items"].append(session.brief())
    return {
        "topic": topic,
        "label": label_for(topic),
        "days": days,
        "papers": [e.brief() for e in items if e.kind == "paper"],
        "posters": [e.brief() for e in items if e.kind == "poster"],
        "people": [e.brief() for e in items if e.kind == "person"],
        "discussion": sum(e.discussion for e in items),
    }


def user_topics(db: Session, index: GraphIndex, user: User) -> Set[str]:
    """What this attendee is already into: interests, bookmarks, attendance."""
    topics: Set[str] = set()
    for interest in (user.research_interests or []):
        normalized = normalize_topic(interest)
        if normalized in index.by_topic:
            topics.add(normalized)
        topics |= (phrase_topics(interest) & set(index.by_topic))

    session_ids = {row.schedule_item_id for row in db.query(ScheduleBookmark)
                   .filter(ScheduleBookmark.user_id == user.id).all()}
    session_ids |= {row.schedule_item_id for row in db.query(SessionAttendance)
                    .filter(SessionAttendance.user_id == user.id).all()}
    for entity in index.entities:
        if entity.kind == "session" and entity.id in session_ids:
            topics |= entity.topics
    return topics


def cross_pollination(index: GraphIndex, mine: Set[str],
                      limit: int = 6) -> List[Dict]:
    """Adjacent topics — they co-occur with what you follow, but you haven't
    engaged with them yet. This is the "step one room over" suggestion."""
    scores: Dict[str, int] = {}
    links: Dict[str, str] = {}
    for (a, b), weight in index.co_occurrence.items():
        for near, far in ((a, b), (b, a)):
            if near in mine and far not in mine:
                if weight > scores.get(far, 0):
                    links[far] = near
                scores[far] = scores.get(far, 0) + weight
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    out = []
    for topic, weight in ranked:
        items = index.by_topic.get(topic, [])
        bridge = links.get(topic, "")
        out.append({
            "topic": topic, "label": label_for(topic), "strength": weight,
            "because": f"{label_for(bridge)} and {label_for(topic)} appear "
                       f"together in {weight} item{'s' if weight != 1 else ''}"
                       if bridge else f"Connected to your topics",
            "items": [e.brief() for e in sorted(
                items, key=lambda e: (-e.discussion, e.title))[:3]],
        })
    return out


def personal_map(db: Session, index: GraphIndex, user: User) -> Dict:
    """The user's own slice of the graph, ready to render or export."""
    mine = user_topics(db, index, user)
    nodes = []
    for topic in sorted(mine, key=lambda t: -len(index.by_topic.get(t, []))):
        items = index.by_topic.get(topic, [])
        nodes.append({
            "topic": topic, "label": label_for(topic), "count": len(items),
            "items": [e.brief() for e in sorted(items, key=_when_key)[:6]],
        })
    return {
        "holder": user.full_name,
        "topics": nodes,
        "cross_pollination": cross_pollination(index, mine),
        "trending": topic_summary(index)[:5],
    }


def graph_payload(index: GraphIndex, focus: Optional[str] = None,
                  limit: int = 60) -> Dict:
    """Nodes + edges for the visual map, optionally focused on one topic."""
    if focus and focus in index.by_topic:
        topics = {focus}
        for (a, b), _ in index.co_occurrence.items():
            if a == focus:
                topics.add(b)
            elif b == focus:
                topics.add(a)
    else:
        topics = set(index.topic_keys()[:limit])

    nodes = []
    for topic in sorted(topics):
        items = index.by_topic.get(topic, [])
        nodes.append({"id": f"topic:{topic}", "kind": "topic",
                      "label": label_for(topic), "topic": topic,
                      "weight": len(items),
                      "discussion": sum(i.discussion for i in items)})
    seen_entities = set()
    edges = []
    for topic in topics:
        for entity in index.by_topic.get(topic, []):
            node_id = f"{entity.kind}:{entity.id}"
            if node_id not in seen_entities:
                seen_entities.add(node_id)
                nodes.append({"id": node_id, "kind": entity.kind,
                              "label": entity.title, "subtitle": entity.subtitle,
                              "url": entity.url, "weight": len(entity.topics),
                              "discussion": entity.discussion})
            edges.append({"source": node_id, "target": f"topic:{topic}",
                          "weight": 1})
    for (a, b), weight in index.co_occurrence.items():
        if a in topics and b in topics and weight >= 2:
            edges.append({"source": f"topic:{a}", "target": f"topic:{b}",
                          "weight": weight, "kind": "topic-link"})
    return {"nodes": nodes, "edges": edges,
            "focus": focus if focus in index.by_topic else None}


def find_entity(index: GraphIndex, kind: str, entity_id: int) -> Optional[Entity]:
    return index.entity(kind, entity_id)


def topics_of_text(index: GraphIndex, text: str) -> List[str]:
    """Which known topics a free-text string mentions (used by the companion)."""
    hits = phrase_topics(text) & set(index.by_topic)
    tokens = set(tokenize(text))
    for topic in index.by_topic:
        if topic in tokens or topic in (text or "").lower():
            hits.add(topic)
    return sorted(hits)


def iter_entities(index: GraphIndex, kinds: Iterable[str]) -> List[Entity]:
    wanted = set(kinds)
    return [e for e in index.entities if e.kind in wanted]
