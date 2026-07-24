"""
Tests for the Knowledge Graph: topic extraction from program data, the visual
map, "related because…" links, research threads, trending by discussion volume,
cross-pollination, the personal map and its PDF export.
"""
from datetime import datetime, timedelta, timezone

from app.core import knowledge as kg
from app.models.paper import Paper, PaperStatus
from app.models.poster import Poster, PosterComment
from app.models.qa import Question
from app.models.schedule import ScheduleBookmark, ScheduleItem, ScheduleType
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


def _session(db, title, description="", offset_hours=1, location="Hall 1",
             speaker_id=None, kind=ScheduleType.talk):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(title=title, description=description, type=kind,
                        location=location, speaker_id=speaker_id,
                        start_time=now + timedelta(hours=offset_hours),
                        end_time=now + timedelta(hours=offset_hours + 1), extra={})
    db.add(item); db.commit(); db.refresh(item)
    return item


def _poster(db, title, abstract="", category=None, code="H1"):
    poster = Poster(title=title, authors="Lee et al.", abstract=abstract,
                    category=category, hunt_code=code, status="approved")
    db.add(poster); db.commit(); db.refresh(poster)
    return poster


def _paper(db, author_id, title, abstract="", category=None,
           status=PaperStatus.accepted):
    paper = Paper(author_id=author_id, title=title, authors="A. Author",
                  abstract=abstract, category=category, status=status)
    db.add(paper); db.commit(); db.refresh(paper)
    return paper


def _with_interests(db, user, interests, visible=True):
    user.research_interests = interests
    user.networking_visible = visible
    db.commit(); db.refresh(user)
    return user


def _program(db, speaker_id=None):
    """A small but realistic program used by most tests."""
    a = _session(db, "Organoid models of the human liver",
                 "We build liver organoids from iPSC lines to study regeneration.",
                 1, "Hall 1", speaker_id)
    b = _session(db, "CRISPR gene editing in hematopoietic stem cells",
                 "Gene editing of HSCs ahead of clinical trials.", 3, "Hall 2")
    c = _session(db, "Cardiac repair with iPSC cardiomyocytes",
                 "Cardiac regeneration using iPSC-derived cardiomyocytes and organoids.",
                 26, "Hall 1", speaker_id)
    return a, b, c


# ─── Topic extraction ────────────────────────────────────────────
class TestExtraction:
    def test_phrase_topics_are_canonical(self):
        found = kg.phrase_topics("Induced pluripotent stem cells and organoids")
        assert "ipsc" in found and "stem cell" in found and "organoid" in found

    def test_phrase_match_is_word_bounded(self):
        # "ema" (the regulator) must not fire inside "hematopoietic".
        assert "regulatory" not in kg.phrase_topics("hematopoietic stem cells")
        assert "regulatory" in kg.phrase_topics("EMA and FDA approval pathways")

    def test_normalize_collapses_plurals_and_synonyms(self):
        assert kg.normalize_topic("Organoids") == "organoid"
        assert kg.normalize_topic("iPSCs") == "ipsc"
        assert kg.normalize_topic(" Gene Therapy ") == "gene therapy"

    def test_tokenize_drops_stopwords_and_short_words(self):
        tokens = kg.tokenize("The study results show novel organoid regeneration")
        assert "organoid" in tokens and "regeneration" in tokens
        assert "the" not in tokens and "study" not in tokens

    def test_label_for_special_cases(self):
        assert kg.label_for("ipsc") == "iPSC"
        assert kg.label_for("crispr") == "CRISPR"
        assert kg.label_for("organoid") == "Organoid"

    def test_index_covers_every_program_kind(self, client, db):
        speaker = make_user(db, email="k1@t.com", role=UserRole.speaker)
        author = make_user(db, email="k2@t.com", role=UserRole.attendee)
        _program(db, speaker.id)
        _poster(db, "Exosomes from mesenchymal stem cells",
                "Exosome cargo for wound healing.", "regenerative medicine")
        _paper(db, author.id, "Gene therapy for retinal degeneration",
               "AAV gene therapy in a retinal organoid model.", "gene therapy")
        index = kg.build_index(db)
        kinds = {e.kind for e in index.entities}
        assert {"session", "poster", "paper", "person"} <= kinds

    def test_breaks_are_not_in_the_graph(self, client, db):
        _session(db, "Coffee break", kind=ScheduleType.break_)
        _program(db)
        index = kg.build_index(db)
        assert not any(e.title == "Coffee break" for e in index.entities)

    def test_unapproved_posters_and_unaccepted_papers_excluded(self, client, db):
        author = make_user(db, email="k3@t.com", role=UserRole.attendee)
        _program(db)
        pending = _poster(db, "Pending poster on organoids", code="H9")
        pending.status = "pending"
        _paper(db, author.id, "Submitted organoid paper",
               status=PaperStatus.submitted)
        db.commit()
        index = kg.build_index(db)
        titles = {e.title for e in index.entities}
        assert "Pending poster on organoids" not in titles
        assert "Submitted organoid paper" not in titles

    def test_interests_only_read_when_opted_in(self, client, db):
        hidden = make_user(db, email="k4@t.com", role=UserRole.attendee)
        _with_interests(db, hidden, ["bioprinting"], visible=False)
        _program(db)
        index = kg.build_index(db)
        assert "bioprinting" not in index.by_topic


# ─── API: topics, graph, related ─────────────────────────────────
class TestTopicsAndGraph:
    def test_topics_endpoint(self, client, db):
        att = make_user(db, email="kt1@t.com", role=UserRole.attendee)
        _program(db)
        r = client.get("/api/knowledge/topics", cookies=auth_cookie(att))
        assert r.status_code == 200
        topics = [t["topic"] for t in r.json()["topics"]]
        assert "organoid" in topics and "ipsc" in topics

    def test_trending_ranked_by_discussion(self, client, db):
        att = make_user(db, email="kt2@t.com", role=UserRole.attendee)
        _, crispr, _ = _program(db)
        for i in range(3):
            db.add(Question(schedule_item_id=crispr.id, user_id=att.id,
                            text=f"Question {i}", upvotes=i))
        db.commit()
        rows = client.get("/api/knowledge/topics",
                          cookies=auth_cookie(att)).json()["topics"]
        assert rows[0]["discussion"] >= rows[-1]["discussion"]
        crispr_row = next(r for r in rows if r["topic"] == "crispr")
        assert crispr_row["discussion"] == 3

    def test_poster_comments_count_as_discussion(self, client, db):
        att = make_user(db, email="kt3@t.com", role=UserRole.attendee)
        _program(db)
        poster = _poster(db, "Bioprinting vascular scaffolds",
                         "Bioprinting of scaffolds.", "tissue engineering", "H2")
        db.add(PosterComment(poster_id=poster.id, user_id=att.id, body="Nice work"))
        db.commit()
        rows = client.get("/api/knowledge/topics",
                          cookies=auth_cookie(att)).json()["topics"]
        assert any(r["discussion"] >= 1 for r in rows)

    def test_graph_nodes_and_edges(self, client, db):
        att = make_user(db, email="kg1@t.com", role=UserRole.attendee)
        _program(db)
        payload = client.get("/api/knowledge/graph", cookies=auth_cookie(att)).json()
        assert payload["nodes"] and payload["edges"]
        assert any(n["kind"] == "topic" for n in payload["nodes"])
        assert any(n["kind"] == "session" for n in payload["nodes"])
        assert payload["topic_count"] >= 1

    def test_graph_focus_narrows_to_neighbours(self, client, db):
        att = make_user(db, email="kg2@t.com", role=UserRole.attendee)
        _program(db)
        payload = client.get("/api/knowledge/graph?topic=organoid",
                             cookies=auth_cookie(att)).json()
        assert payload["focus"] == "organoid"
        topics = {n["topic"] for n in payload["nodes"] if n["kind"] == "topic"}
        assert "organoid" in topics

    def test_related_explains_itself(self, client, db):
        att = make_user(db, email="kr1@t.com", role=UserRole.attendee)
        speaker = make_user(db, email="kr2@t.com", role=UserRole.speaker)
        first, _, _ = _program(db, speaker.id)
        r = client.get(f"/api/knowledge/related/session/{first.id}",
                       cookies=auth_cookie(att))
        assert r.status_code == 200
        related = r.json()["related"]
        assert related and related[0]["reason"].startswith("Also about")
        assert related[0]["shared_topics"]
        assert all(item["id"] != first.id or item["kind"] != "session"
                   for item in related)

    def test_related_bad_kind_and_missing_id(self, client, db):
        att = make_user(db, email="kr3@t.com", role=UserRole.attendee)
        _program(db)
        assert client.get("/api/knowledge/related/widget/1",
                          cookies=auth_cookie(att)).status_code == 400
        assert client.get("/api/knowledge/related/session/9999",
                          cookies=auth_cookie(att)).status_code == 404


# ─── Threads + cross-pollination ─────────────────────────────────
class TestThreads:
    def test_thread_groups_by_day(self, client, db):
        att = make_user(db, email="kh1@t.com", role=UserRole.attendee)
        _program(db)  # third session is ~26h later → a second day
        r = client.get("/api/knowledge/thread/organoid", cookies=auth_cookie(att))
        assert r.status_code == 200
        data = r.json()
        assert data["label"] == "Organoid"
        assert len(data["days"]) >= 2
        assert data["days"][0]["items"]

    def test_thread_includes_posters_and_papers(self, client, db):
        att = make_user(db, email="kh2@t.com", role=UserRole.attendee)
        author = make_user(db, email="kh3@t.com", role=UserRole.attendee)
        _program(db)
        _poster(db, "Organoid vascularization", "Organoid growth in scaffolds.",
                "organoid", "H3")
        _paper(db, author.id, "Organoid transplantation outcomes",
               "Organoid grafts in vivo.", "organoid")
        data = client.get("/api/knowledge/thread/organoid",
                          cookies=auth_cookie(att)).json()
        assert data["posters"] and data["papers"]

    def test_unknown_topic_404(self, client, db):
        att = make_user(db, email="kh4@t.com", role=UserRole.attendee)
        _program(db)
        assert client.get("/api/knowledge/thread/quantum-widgets",
                          cookies=auth_cookie(att)).status_code == 404

    def test_cross_pollination_uses_my_topics(self, client, db):
        att = _with_interests(db, make_user(db, email="kc1@t.com",
                                            role=UserRole.attendee), ["organoids"])
        first, _, _ = _program(db)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=first.id))
        db.commit()
        data = client.get("/api/knowledge/cross-pollination",
                          cookies=auth_cookie(att)).json()
        mine = {t["topic"] for t in data["my_topics"]}
        assert "organoid" in mine
        for suggestion in data["suggestions"]:
            assert suggestion["topic"] not in mine
            assert suggestion["because"]

    def test_cross_pollination_empty_for_new_user(self, client, db):
        att = make_user(db, email="kc2@t.com", role=UserRole.attendee)
        _program(db)
        data = client.get("/api/knowledge/cross-pollination",
                          cookies=auth_cookie(att)).json()
        assert data["my_topics"] == [] and data["suggestions"] == []


# ─── Personal map + PDF ──────────────────────────────────────────
class TestPersonalMap:
    def test_my_map_from_bookmarks(self, client, db):
        att = make_user(db, email="km1@t.com", role=UserRole.attendee)
        first, _, _ = _program(db)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=first.id))
        db.commit()
        data = client.get("/api/knowledge/my-map", cookies=auth_cookie(att)).json()
        assert data["holder"] == att.full_name
        assert any(t["topic"] == "organoid" for t in data["topics"])
        assert data["topics"][0]["items"]

    def test_my_map_empty_is_still_valid(self, client, db):
        att = make_user(db, email="km2@t.com", role=UserRole.attendee)
        _program(db)
        data = client.get("/api/knowledge/my-map", cookies=auth_cookie(att)).json()
        assert data["topics"] == [] and "trending" in data

    def test_pdf_export(self, client, db):
        att = make_user(db, email="km3@t.com", role=UserRole.attendee)
        first, _, _ = _program(db)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=first.id))
        db.commit()
        r = client.get("/api/knowledge/my-map.pdf", cookies=auth_cookie(att))
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF" and len(r.content) > 1000
        assert "attachment" in r.headers["content-disposition"]

    def test_pdf_export_is_audit_logged(self, client, db):
        from app.models.audit_log import AuditLog, AuditAction
        att = make_user(db, email="km4@t.com", role=UserRole.attendee)
        _program(db)
        client.get("/api/knowledge/my-map.pdf", cookies=auth_cookie(att))
        assert db.query(AuditLog).filter(
            AuditLog.action == AuditAction.export_data,
            AuditLog.target_type == "knowledge").count() == 1

    def test_pdf_export_with_no_program(self, client, db):
        att = make_user(db, email="km5@t.com", role=UserRole.attendee)
        r = client.get("/api/knowledge/my-map.pdf", cookies=auth_cookie(att))
        assert r.status_code == 200 and r.content[:4] == b"%PDF"


# ─── Page + feature flag ─────────────────────────────────────────
class TestGating:
    def test_page_renders(self, client, db):
        att = make_user(db, email="kp1@t.com", role=UserRole.attendee)
        r = client.get("/knowledge", cookies=auth_cookie(att))
        assert r.status_code == 200 and "Knowledge" in r.text

    def test_anonymous_redirected(self, client, db):
        r = client.get("/knowledge", follow_redirects=False)
        assert r.status_code == 302 and "/login" in r.headers["location"]

    def test_flag_off_blocks_attendee(self, client, db):
        from app.core import feature_flags
        admin = make_user(db, email="kp2@t.com", role=UserRole.admin)
        att = make_user(db, email="kp3@t.com", role=UserRole.attendee)
        feature_flags.set_enabled(db, "knowledge", False, admin.id)
        try:
            assert client.get("/knowledge", cookies=auth_cookie(att),
                              follow_redirects=False).status_code == 302
            assert client.get("/api/knowledge/topics",
                              cookies=auth_cookie(att)).status_code == 403
            assert client.get("/api/knowledge/topics",
                              cookies=auth_cookie(admin)).status_code == 200
        finally:
            feature_flags.set_enabled(db, "knowledge", True, admin.id)
