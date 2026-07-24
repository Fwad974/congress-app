"""
Tests for the AI Congress Companion: intent routing over live congress data,
proactive nudges, the day briefing with energy-aware advice, serendipity mode,
speaker prep, smart summaries, the Dubai guide, and the optional LLM path
(Claude / Gemini / OpenAI, including that it degrades cleanly when absent).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core import companion
from app.core.llm import LLMResult
from app.models.attendance import SessionAttendance
from app.models.feedback import SessionRating
from app.models.qa import Question
from app.models.schedule import ScheduleBookmark, ScheduleItem, ScheduleType
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user


def _session(db, title, description="", offset_hours=1, location="Hall 1",
             speaker_id=None, chair_id=None, kind=ScheduleType.talk):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(title=title, description=description, type=kind,
                        location=location, speaker_id=speaker_id, chair_id=chair_id,
                        start_time=now + timedelta(hours=offset_hours),
                        end_time=now + timedelta(hours=offset_hours + 1), extra={})
    db.add(item); db.commit(); db.refresh(item)
    return item


def _live_session(db, title="Live keynote", location="Hall 1", speaker_id=None):
    now = datetime.now(timezone.utc)
    item = ScheduleItem(title=title, type=ScheduleType.keynote, location=location,
                        speaker_id=speaker_id,
                        start_time=now - timedelta(minutes=10),
                        end_time=now + timedelta(minutes=50), extra={})
    db.add(item); db.commit(); db.refresh(item)
    return item


def _ask(client, user, question, allow_llm=False):
    r = client.post("/api/companion/ask",
                    json={"question": question, "allow_llm": allow_llm},
                    cookies=auth_cookie(user))
    assert r.status_code == 200, r.text
    return r.json()


# ─── Intent routing ──────────────────────────────────────────────
class TestIntents:
    def test_detect_intent_table(self):
        assert companion.detect_intent("What's on right now?") == "now"
        assert companion.detect_intent("what's next?") == "next"
        assert companion.detect_intent("where is Hall 2") == "navigate"
        assert companion.detect_intent("what's the wifi password") == "wifi"
        assert companion.detect_intent("I'm exhausted") == "energy"
        assert companion.detect_intent("zzzz") is None

    def test_now_reports_live_session(self, client, db):
        att = make_user(db, email="c1@t.com", role=UserRole.attendee)
        _live_session(db, "Opening keynote", "Hall 3")
        data = _ask(client, att, "What's on right now?")
        assert data["intent"] == "now" and "Opening keynote" in data["answer"]
        assert "Hall 3" in data["answer"]

    def test_now_falls_forward_when_nothing_live(self, client, db):
        att = make_user(db, email="c2@t.com", role=UserRole.attendee)
        _session(db, "Later talk", offset_hours=3)
        data = _ask(client, att, "anything happening now?")
        assert "Later talk" in data["answer"]

    def test_next_marks_bookmarks(self, client, db):
        att = make_user(db, email="c3@t.com", role=UserRole.attendee)
        item = _session(db, "Bookmarked talk", offset_hours=2)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=item.id)); db.commit()
        data = _ask(client, att, "what's next?")
        assert "Bookmarked talk" in data["answer"] and "bookmarked" in data["answer"]

    def test_myday_summarizes(self, client, db):
        att = make_user(db, email="c4@t.com", role=UserRole.attendee)
        item = _session(db, "My pick", offset_hours=2)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=item.id)); db.commit()
        data = _ask(client, att, "show me my schedule")
        assert data["intent"] == "myday" and "My pick" in data["answer"]

    def test_recommend_prefers_my_topics(self, client, db):
        att = make_user(db, email="c5@t.com", role=UserRole.attendee)
        organoid = _session(db, "Organoid vascularization",
                            "Liver organoids and regeneration.", 2)
        _session(db, "Grant writing workshop", "How to write a grant.", 3)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=organoid.id))
        _session(db, "Organoid transplantation outcomes",
                 "Organoid grafts in vivo regeneration.", 4)
        db.commit()
        data = _ask(client, att, "what should I see next?")
        assert data["intent"] == "recommend"
        assert "Organoid transplantation outcomes" in data["answer"]

    def test_recommend_without_history_still_answers(self, client, db):
        att = make_user(db, email="c6@t.com", role=UserRole.attendee)
        _session(db, "Only talk", offset_hours=2)
        data = _ask(client, att, "recommend something")
        assert "Only talk" in data["answer"] and data["items"]

    def test_navigate_finds_named_room(self, client, db):
        from app.models.venue import VenueRoom
        att = make_user(db, email="c7@t.com", role=UserRole.attendee)
        db.add(VenueRoom(name="Hall 2", floor=2, x=10, y=10, w=20, h=15,
                         directions_hint="next to the café")); db.commit()
        data = _ask(client, att, "where is Hall 2?")
        assert "floor 2" in data["answer"] and "café" in data["answer"]
        assert any(link["url"] == "/venue" for link in data["links"])

    def test_navigate_without_rooms_points_at_venue_page(self, client, db):
        att = make_user(db, email="c8@t.com", role=UserRole.attendee)
        data = _ask(client, att, "how do I get to the poster hall?")
        assert data["intent"] == "navigate" and data["links"]

    def test_wifi_answer(self, client, db):
        att = make_user(db, email="c9@t.com", role=UserRole.attendee)
        data = _ask(client, att, "what's the wifi password?")
        assert "DSCC-" in data["answer"]

    def test_wifi_reflects_admin_changes(self, client, db):
        admin = make_user(db, email="c10@t.com", role=UserRole.admin)
        att = make_user(db, email="c11@t.com", role=UserRole.attendee)
        client.put("/api/venue/settings",
                   json={"wifi": {"ssid": "Congress-Net", "password": "letmein1",
                                  "security": "WPA"}}, cookies=auth_cookie(admin))
        data = _ask(client, att, "wifi please")
        assert "Congress-Net" in data["answer"] and "letmein1" in data["answer"]

    def test_local_info_intents(self, client, db):
        att = make_user(db, email="c12@t.com", role=UserRole.attendee)
        assert _ask(client, att, "where can I eat?")["intent"] == "food"
        assert _ask(client, att, "nearest pharmacy?")["intent"] == "health"
        assert _ask(client, att, "I need an ATM")["intent"] == "money"
        assert _ask(client, att, "which hotel is closest?")["intent"] == "stay"
        assert _ask(client, att, "how do I take the metro?")["intent"] == "transport"

    def test_emergency_lists_numbers(self, client, db):
        att = make_user(db, email="c13@t.com", role=UserRole.attendee)
        data = _ask(client, att, "someone is sick, I need medical help")
        assert data["intent"] == "emergency" and "999" in data["answer"]

    def test_certificate_progress(self, client, db):
        att = make_user(db, email="c14@t.com", role=UserRole.attendee)
        item = _session(db, "Attended talk", offset_hours=-3)
        db.add(SessionAttendance(schedule_item_id=item.id, user_id=att.id)); db.commit()
        data = _ask(client, att, "how am I doing on CME credits?")
        assert data["intent"] == "certificate" and "1 sessions" in data["answer"]

    def test_people_search_by_topic(self, client, db):
        att = make_user(db, email="c15@t.com", role=UserRole.attendee)
        speaker = make_user(db, email="c16@t.com", role=UserRole.speaker,
                            full_name="Dr Organoid")
        _session(db, "Organoid models of the liver",
                 "Liver organoids from iPSC lines.", 2, speaker_id=speaker.id)
        _session(db, "Organoid regeneration", "Organoid regeneration studies.", 4)
        data = _ask(client, att, "who works on organoids?")
        assert data["intent"] == "people" and "Dr Organoid" in data["answer"]

    def test_topic_question_without_intent(self, client, db):
        att = make_user(db, email="c17@t.com", role=UserRole.attendee)
        _session(db, "CRISPR gene editing in HSCs", "Gene editing of HSCs.", 2)
        data = _ask(client, att, "Tell me about CRISPR")
        assert data["intent"] == "topic" and "CRISPR gene editing" in data["answer"]

    def test_unmatched_question_falls_back(self, client, db):
        att = make_user(db, email="c18@t.com", role=UserRole.attendee)
        data = _ask(client, att, "qwerty zxcvb")
        assert data["intent"] == "fallback" and data["via"] == "rules"
        assert "couldn't match" in data["answer"]

    def test_empty_question_rejected(self, client, db):
        att = make_user(db, email="c19@t.com", role=UserRole.attendee)
        r = client.post("/api/companion/ask", json={"question": "   "},
                        cookies=auth_cookie(att))
        assert r.status_code == 422

    def test_help_lists_capabilities(self, client, db):
        att = make_user(db, email="c20@t.com", role=UserRole.attendee)
        data = _ask(client, att, "what can you do?")
        assert data["intent"] == "help" and "CME" in data["answer"]


# ─── Energy awareness ────────────────────────────────────────────
class TestEnergy:
    def test_long_day_suggests_a_break(self, client, db):
        att = make_user(db, email="ce1@t.com", role=UserRole.attendee)
        for i in range(5):
            item = _session(db, f"Talk {i}", offset_hours=-6 + i)
            db.add(SessionAttendance(schedule_item_id=item.id, user_id=att.id))
        db.commit()
        data = _ask(client, att, "I'm exhausted")
        assert "full day" in data["answer"] or "day" in data["answer"]

    def test_gap_suggestion_mentions_minutes(self, client, db):
        att = make_user(db, email="ce2@t.com", role=UserRole.attendee)
        _session(db, "Later talk", offset_hours=2)
        suggestion = companion.energy_suggestion(
            db, att, companion.build_context(db, att))
        assert suggestion["kind"] == "gap" and suggestion["gap_minutes"] >= 60

    def test_energy_always_returns_text(self, client, db):
        att = make_user(db, email="ce3@t.com", role=UserRole.attendee)
        suggestion = companion.energy_suggestion(
            db, att, companion.build_context(db, att))
        assert suggestion["text"] and suggestion["kind"]


# ─── Nudges ──────────────────────────────────────────────────────
class TestNudges:
    def test_bookmarked_session_starting_soon(self, client, db):
        att = make_user(db, email="cn1@t.com", role=UserRole.attendee)
        now = datetime.now(timezone.utc)
        item = ScheduleItem(title="Starting soon", type=ScheduleType.talk,
                            location="Hall 9", start_time=now + timedelta(minutes=20),
                            end_time=now + timedelta(minutes=80), extra={})
        db.add(item); db.commit(); db.refresh(item)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=item.id)); db.commit()
        nudges = client.get("/api/companion/nudges",
                            cookies=auth_cookie(att)).json()["nudges"]
        starting = [n for n in nudges if n["kind"] == "starting"]
        assert starting and "Hall 9" in starting[0]["body"]
        assert starting[0]["urgency"] == "high"

    def test_speaker_prep_nudge(self, client, db):
        speaker = make_user(db, email="cn2@t.com", role=UserRole.speaker)
        now = datetime.now(timezone.utc)
        item = ScheduleItem(title="My talk", type=ScheduleType.talk,
                            speaker_id=speaker.id,
                            start_time=now + timedelta(minutes=45),
                            end_time=now + timedelta(minutes=105), extra={})
        db.add(item); db.commit()
        nudges = client.get("/api/companion/nudges",
                            cookies=auth_cookie(speaker)).json()["nudges"]
        assert any(n["kind"] == "speaker_prep" for n in nudges)

    def test_unrated_sessions_nudge(self, client, db):
        att = make_user(db, email="cn3@t.com", role=UserRole.attendee)
        for i in range(3):
            item = _session(db, f"Past talk {i}", offset_hours=-4 + i)
            db.add(SessionAttendance(schedule_item_id=item.id, user_id=att.id))
        db.commit()
        nudges = client.get("/api/companion/nudges",
                            cookies=auth_cookie(att)).json()["nudges"]
        assert any(n["kind"] == "rate" for n in nudges)

    def test_rated_sessions_produce_no_rate_nudge(self, client, db):
        att = make_user(db, email="cn4@t.com", role=UserRole.attendee)
        for i in range(3):
            item = _session(db, f"Past talk {i}", offset_hours=-4 + i)
            db.add(SessionAttendance(schedule_item_id=item.id, user_id=att.id))
            db.add(SessionRating(schedule_item_id=item.id, user_id=att.id, stars=5))
        db.commit()
        nudges = client.get("/api/companion/nudges",
                            cookies=auth_cookie(att)).json()["nudges"]
        assert not any(n["kind"] == "rate" for n in nudges)

    def test_consent_nudge_for_speaker(self, client, db):
        admin = make_user(db, email="cn5@t.com", role=UserRole.admin)
        speaker = make_user(db, email="cn6@t.com", role=UserRole.speaker)
        item = _session(db, "Recorded talk", offset_hours=-2, speaker_id=speaker.id)
        client.post("/api/recordings",
                    json={"schedule_item_id": item.id,
                          "video_url": "https://cdn.example.com/a.mp4"},
                    cookies=auth_cookie(admin))
        nudges = client.get("/api/companion/nudges",
                            cookies=auth_cookie(speaker)).json()["nudges"]
        assert any(n["kind"] == "consent" for n in nudges)

    def test_no_speaker_certificate_nudge_for_attendee(self, client, db):
        att = make_user(db, email="cn7@t.com", role=UserRole.attendee)
        nudges = client.get("/api/companion/nudges",
                            cookies=auth_cookie(att)).json()["nudges"]
        assert not any("Speaker Certificate" in n["title"] for n in nudges)

    def test_nudges_sorted_by_urgency(self, client, db):
        att = make_user(db, email="cn8@t.com", role=UserRole.attendee)
        now = datetime.now(timezone.utc)
        soon = ScheduleItem(title="Soon", type=ScheduleType.talk,
                            start_time=now + timedelta(minutes=10),
                            end_time=now + timedelta(minutes=70), extra={})
        db.add(soon); db.commit(); db.refresh(soon)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=soon.id))
        for i in range(3):
            item = _session(db, f"Past {i}", offset_hours=-5 + i)
            db.add(SessionAttendance(schedule_item_id=item.id, user_id=att.id))
        db.commit()
        nudges = client.get("/api/companion/nudges",
                            cookies=auth_cookie(att)).json()["nudges"]
        order = {"high": 0, "medium": 1, "low": 2}
        assert [order[n["urgency"]] for n in nudges] == sorted(
            order[n["urgency"]] for n in nudges)


# ─── Briefing, serendipity, prep, summary, guide ─────────────────
class TestProactive:
    def test_briefing_shape(self, client, db):
        att = make_user(db, email="cb1@t.com", role=UserRole.attendee)
        _live_session(db, "Now talk")
        data = client.get("/api/companion/briefing", cookies=auth_cookie(att)).json()
        assert data["greeting"].startswith(("Good morning", "Good afternoon",
                                            "Good evening"))
        assert data["live"] and data["energy"]["text"]
        assert "llm_enabled" in data

    def test_serendipity_avoids_my_topics(self, client, db):
        att = make_user(db, email="cb2@t.com", role=UserRole.attendee)
        organoid = _session(db, "Organoid models", "Liver organoids.", 2)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=organoid.id))
        _session(db, "CRISPR editing clinic", "Gene editing clinical trials.", 3)
        _session(db, "Bioprinting scaffolds", "Bioprinting tissue scaffolds.", 4)
        db.commit()
        data = client.get("/api/companion/serendipity",
                          cookies=auth_cookie(att)).json()
        assert "Organoid models" not in [p["title"] for p in data["picks"]]

    def test_serendipity_picks_are_unique(self, client, db):
        att = make_user(db, email="cb3@t.com", role=UserRole.attendee)
        for i in range(4):
            _session(db, f"Cell therapy trial {i}",
                     "Cell therapy clinical trial results.", 2 + i)
        data = client.get("/api/companion/serendipity",
                          cookies=auth_cookie(att)).json()
        keys = [(p["kind"], p["id"]) for p in data["picks"]]
        assert len(keys) == len(set(keys))

    def test_speaker_prep_for_own_session(self, client, db):
        speaker = make_user(db, email="cb4@t.com", role=UserRole.speaker)
        att = make_user(db, email="cb5@t.com", role=UserRole.attendee)
        item = _session(db, "My keynote", "Organoids.", 2, speaker_id=speaker.id)
        db.add(ScheduleBookmark(user_id=att.id, schedule_item_id=item.id))
        db.add(Question(schedule_item_id=item.id, user_id=att.id,
                        text="How reproducible is it?", upvotes=7))
        db.commit()
        data = client.get(f"/api/companion/prep/{item.id}",
                          cookies=auth_cookie(speaker)).json()
        assert data["audience"]["bookmarked"] == 1
        assert data["top_questions"][0]["upvotes"] == 7
        assert data["checklist"]

    def test_chair_can_open_prep_attendee_cannot(self, client, db):
        speaker = make_user(db, email="cb6@t.com", role=UserRole.speaker)
        chair = make_user(db, email="cb7@t.com", role=UserRole.session_chair)
        att = make_user(db, email="cb8@t.com", role=UserRole.attendee)
        item = _session(db, "Chaired talk", offset_hours=2,
                        speaker_id=speaker.id, chair_id=chair.id)
        assert client.get(f"/api/companion/prep/{item.id}",
                          cookies=auth_cookie(chair)).status_code == 200
        assert client.get(f"/api/companion/prep/{item.id}",
                          cookies=auth_cookie(att)).status_code == 403

    def test_prep_unknown_session(self, client, db):
        admin = make_user(db, email="cb9@t.com", role=UserRole.admin)
        assert client.get("/api/companion/prep/9999",
                          cookies=auth_cookie(admin)).status_code == 404

    def test_session_summary(self, client, db):
        att = make_user(db, email="cs1@t.com", role=UserRole.attendee)
        item = _session(db, "Organoid keynote",
                        "Liver organoids from iPSC lines.", 2)
        db.add(Question(schedule_item_id=item.id, user_id=att.id,
                        text="What about scale-up?", upvotes=3))
        db.add(SessionRating(schedule_item_id=item.id, user_id=att.id, stars=4))
        db.commit()
        data = client.get(f"/api/companion/summary/{item.id}",
                          cookies=auth_cookie(att)).json()
        assert "Organoid keynote" in data["summary"]
        assert data["rating"] == 4.0 and data["top_questions"]

    def test_summary_unknown_session(self, client, db):
        att = make_user(db, email="cs2@t.com", role=UserRole.attendee)
        assert client.get("/api/companion/summary/9999",
                          cookies=auth_cookie(att)).status_code == 404

    def test_guide_hides_wifi_password(self, client, db):
        att = make_user(db, email="cg1@t.com", role=UserRole.attendee)
        data = client.get("/api/companion/guide", cookies=auth_cookie(att)).json()
        assert "password" not in data["wifi"]
        assert data["places"] and data["emergency"]


# ─── LLM path (optional: Claude / Gemini / OpenAI) ───────────────
class TestLLMPath:
    def test_llm_off_without_any_key(self):
        from app.core.config import get_settings
        # No provider is configured by default, so the companion runs offline.
        settings = get_settings()
        assert not (settings.ANTHROPIC_API_KEY or settings.GEMINI_API_KEY
                    or settings.OPENAI_API_KEY)
        assert companion.llm_available() is False
        assert companion.llm_provider() is None

    @pytest.mark.parametrize("provider,model", [
        ("anthropic", "claude-opus-5"),
        ("gemini", "gemini-2.5-pro"),
        ("openai", "gpt-4o"),
    ])
    def test_unmatched_question_uses_the_active_provider(self, client, db,
                                                         provider, model):
        att = make_user(db, email=f"cl-{provider}@t.com", role=UserRole.attendee)
        _session(db, "A talk", offset_hours=2)
        result = LLMResult(text="Here is a grounded answer.", provider=provider,
                           model=model)
        with patch.object(companion, "llm_available", return_value=True), \
             patch.object(companion, "ask_llm", return_value=result):
            data = _ask(client, att, "qwerty zxcvb", allow_llm=True)
        assert data["via"] == provider and data["model"] == model
        assert data["answer"] == "Here is a grounded answer."

    def test_llm_failure_degrades_to_rules(self, client, db):
        att = make_user(db, email="cl2@t.com", role=UserRole.attendee)
        with patch.object(companion, "llm_available", return_value=True), \
             patch.object(companion, "ask_llm", return_value=None):
            data = _ask(client, att, "qwerty zxcvb", allow_llm=True)
        assert data["via"] == "rules" and data["intent"] == "fallback"

    def test_rule_intents_never_call_the_llm(self, client, db):
        att = make_user(db, email="cl3@t.com", role=UserRole.attendee)
        _live_session(db, "Now talk")
        with patch.object(companion, "ask_llm") as spy:
            data = _ask(client, att, "What's on right now?", allow_llm=True)
        spy.assert_not_called()
        assert data["via"] == "rules"

    def test_allow_llm_false_skips_the_llm(self, client, db):
        att = make_user(db, email="cl4@t.com", role=UserRole.attendee)
        with patch.object(companion, "llm_available", return_value=True), \
             patch.object(companion, "ask_llm") as spy:
            _ask(client, att, "qwerty zxcvb", allow_llm=False)
        spy.assert_not_called()

    def test_facts_brief_contains_program_and_venue(self, client, db):
        att = make_user(db, email="cl5@t.com", role=UserRole.attendee)
        _live_session(db, "Grounding keynote", "Hall 7")
        facts = companion._llm_facts(db, att, companion.build_context(db, att))
        assert "Grounding keynote" in facts and "Hall 7" in facts
        assert "WiFi SSID" in facts and "/schedule" in facts

    def test_ask_llm_without_a_provider_returns_none(self, client, db):
        """No provider → no call attempted, and the caller still answers."""
        att = make_user(db, email="cl6@t.com", role=UserRole.attendee)
        ctx = companion.build_context(db, att)
        assert companion.ask_llm(db, att, ctx, "anything") is None

    def test_briefing_reports_provider_state(self, client, db):
        att = make_user(db, email="cl7@t.com", role=UserRole.attendee)
        data = client.get("/api/companion/briefing",
                          cookies=auth_cookie(att)).json()
        assert data["llm_enabled"] is False and data["llm_provider"] is None


# ─── Page + feature flag ─────────────────────────────────────────
class TestGating:
    def test_page_renders(self, client, db):
        att = make_user(db, email="cp1@t.com", role=UserRole.attendee)
        r = client.get("/companion", cookies=auth_cookie(att))
        assert r.status_code == 200 and "Companion" in r.text

    def test_anonymous_redirected(self, client, db):
        r = client.get("/companion", follow_redirects=False)
        assert r.status_code == 302 and "/login" in r.headers["location"]

    def test_flag_off_blocks_attendee(self, client, db):
        from app.core import feature_flags
        admin = make_user(db, email="cp2@t.com", role=UserRole.admin)
        att = make_user(db, email="cp3@t.com", role=UserRole.attendee)
        feature_flags.set_enabled(db, "companion", False, admin.id)
        try:
            assert client.get("/companion", cookies=auth_cookie(att),
                              follow_redirects=False).status_code == 302
            assert client.get("/api/companion/briefing",
                              cookies=auth_cookie(att)).status_code == 403
            assert client.get("/api/companion/briefing",
                              cookies=auth_cookie(admin)).status_code == 200
        finally:
            feature_flags.set_enabled(db, "companion", True, admin.id)
