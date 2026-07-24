"""
Tests for Review Management additions: scoring rubric, auto-assign with COI
check, and chair score override with written justification.
"""
from app.models.paper import Paper, Review, PaperStatus, RUBRIC_KEYS
from app.models.audit_log import AuditLog, AuditAction
from app.models.schedule import ScheduleItem  # noqa: F401 - FK resolution
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user

SUB = {"title": "On iPSCs", "authors": "Jane Doe", "abstract": "We show…",
       "category": "iPSC"}


def _paper(client, author, **over):
    body = dict(SUB); body.update(over)
    return client.post("/api/papers", json=body, cookies=auth_cookie(author)).json()["id"]


# ─── Scoring rubric ──────────────────────────────────────────────
class TestRubric:
    def test_rubric_endpoint(self, client, db):
        u = make_user(db, email="ru@t.com", role=UserRole.reviewer)
        d = client.get("/api/papers/rubric", cookies=auth_cookie(u)).json()
        keys = {c["key"] for c in d["criteria"]}
        assert keys == RUBRIC_KEYS and d["scale"] == {"min": 1, "max": 5}

    def test_rubric_page_injects_criteria(self, client, db):
        chair = make_user(db, email="rp@t.com", role=UserRole.review_chair)
        html = client.get("/papers", cookies=auth_cookie(chair)).text
        assert "const RUBRIC" in html and "Methodology" in html

    def test_review_with_rubric_derives_score(self, client, db):
        chair = make_user(db, email="rc@t.com", role=UserRole.review_chair)
        author = make_user(db, email="ra@t.com", role=UserRole.attendee)
        rev = make_user(db, email="rr@t.com", role=UserRole.reviewer)
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [rev.id]}, cookies=auth_cookie(chair))
        # 5,4,4,3 → mean 4.0 → 4
        rubric = {"originality": 5, "significance": 4, "methodology": 4, "clarity": 3}
        r = client.put(f"/api/papers/{pid}/review",
                       json={"rubric": rubric, "comments": "Good", "submitted": True},
                       cookies=auth_cookie(rev))
        assert r.status_code == 200
        row = db.query(Review).filter(Review.paper_id == pid).one()
        assert row.rubric == rubric and row.score == 4 and row.submitted is True

    def test_explicit_score_overrides_rubric_mean(self, client, db):
        chair = make_user(db, email="re@t.com", role=UserRole.review_chair)
        author = make_user(db, email="re2@t.com", role=UserRole.attendee)
        rev = make_user(db, email="re3@t.com", role=UserRole.reviewer)
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [rev.id]}, cookies=auth_cookie(chair))
        client.put(f"/api/papers/{pid}/review",
                   json={"rubric": {"clarity": 1}, "score": 5, "submitted": True},
                   cookies=auth_cookie(rev))
        assert db.query(Review).filter(Review.paper_id == pid).one().score == 5

    def test_bad_rubric_key_rejected(self, client, db):
        chair = make_user(db, email="rb@t.com", role=UserRole.review_chair)
        author = make_user(db, email="rb2@t.com", role=UserRole.attendee)
        rev = make_user(db, email="rb3@t.com", role=UserRole.reviewer)
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [rev.id]}, cookies=auth_cookie(chair))
        assert client.put(f"/api/papers/{pid}/review",
                          json={"rubric": {"bogus": 3}},
                          cookies=auth_cookie(rev)).status_code == 422

    def test_cannot_submit_without_score_or_rubric(self, client, db):
        chair = make_user(db, email="rn@t.com", role=UserRole.review_chair)
        author = make_user(db, email="rn2@t.com", role=UserRole.attendee)
        rev = make_user(db, email="rn3@t.com", role=UserRole.reviewer)
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [rev.id]}, cookies=auth_cookie(chair))
        assert client.put(f"/api/papers/{pid}/review",
                          json={"comments": "no score", "submitted": True},
                          cookies=auth_cookie(rev)).status_code == 400

    def test_chair_and_author_see_rubric(self, client, db):
        chair = make_user(db, email="rs@t.com", role=UserRole.review_chair)
        author = make_user(db, email="rs2@t.com", role=UserRole.attendee)
        rev = make_user(db, email="rs3@t.com", role=UserRole.reviewer)
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [rev.id]}, cookies=auth_cookie(chair))
        client.put(f"/api/papers/{pid}/review",
                   json={"rubric": {"clarity": 4, "originality": 2}, "submitted": True},
                   cookies=auth_cookie(rev))
        # Chair sees the rubric immediately.
        chair_view = client.get(f"/api/papers/{pid}", cookies=auth_cookie(chair)).json()
        assert chair_view["reviews"][0]["rubric"] == {"clarity": 4, "originality": 2}
        # Author sees it only after a decision.
        client.post(f"/api/papers/{pid}/decision",
                    json={"decision": "accept"}, cookies=auth_cookie(chair))
        author_view = client.get(f"/api/papers/{pid}", cookies=auth_cookie(author)).json()
        assert author_view["reviews"][0]["rubric"] == {"clarity": 4, "originality": 2}


# ─── Auto-assign with COI check ──────────────────────────────────
class TestAutoAssign:
    def test_auto_assigns_min_reviewers(self, client, db):
        chair = make_user(db, email="aa@t.com", role=UserRole.review_chair)
        author = make_user(db, email="aa2@t.com", role=UserRole.attendee, institution="Yale")
        make_user(db, email="aa3@t.com", role=UserRole.reviewer, institution="MIT")
        make_user(db, email="aa4@t.com", role=UserRole.reviewer, institution="Oxford")
        make_user(db, email="aa5@t.com", role=UserRole.reviewer, institution="ETH")
        pid = _paper(client, author)
        r = client.post(f"/api/papers/{pid}/assign/auto", json={},
                        cookies=auth_cookie(chair))
        assert r.status_code == 200
        assert r.json()["status"] == "under_review"
        assert db.query(Review).filter(Review.paper_id == pid).count() == 2

    def test_auto_respects_count(self, client, db):
        chair = make_user(db, email="ac@t.com", role=UserRole.review_chair)
        author = make_user(db, email="ac2@t.com", role=UserRole.attendee, institution="Yale")
        for i in range(4):
            make_user(db, email=f"acr{i}@t.com", role=UserRole.reviewer, institution=f"U{i}")
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign/auto", json={"count": 3},
                    cookies=auth_cookie(chair))
        assert db.query(Review).filter(Review.paper_id == pid).count() == 3

    def test_auto_excludes_coi_and_author(self, client, db):
        chair = make_user(db, email="ax@t.com", role=UserRole.review_chair)
        author = make_user(db, email="ax2@t.com", role=UserRole.reviewer, institution="Yale")
        same = make_user(db, email="ax3@t.com", role=UserRole.reviewer, institution="Yale")
        other = make_user(db, email="ax4@t.com", role=UserRole.reviewer, institution="MIT")
        pid = _paper(client, author)  # author is also a reviewer role
        client.post(f"/api/papers/{pid}/assign/auto", json={"count": 5},
                    cookies=auth_cookie(chair))
        assigned = {r.reviewer_id for r in db.query(Review).filter(Review.paper_id == pid).all()}
        assert other.id in assigned
        assert author.id not in assigned and same.id not in assigned

    def test_auto_override_coi_includes_same_institution(self, client, db):
        chair = make_user(db, email="ao@t.com", role=UserRole.review_chair)
        author = make_user(db, email="ao2@t.com", role=UserRole.attendee, institution="Yale")
        same = make_user(db, email="ao3@t.com", role=UserRole.reviewer, institution="Yale")
        pid = _paper(client, author)
        # Without override, no eligible reviewers → 400.
        assert client.post(f"/api/papers/{pid}/assign/auto", json={},
                           cookies=auth_cookie(chair)).status_code == 400
        # With override, the same-institution reviewer is used.
        client.post(f"/api/papers/{pid}/assign/auto", json={"override_coi": True},
                    cookies=auth_cookie(chair))
        assert {r.reviewer_id for r in db.query(Review).filter(
            Review.paper_id == pid).all()} == {same.id}

    def test_auto_prefers_lightest_load(self, client, db):
        chair = make_user(db, email="al@t.com", role=UserRole.review_chair)
        author = make_user(db, email="al2@t.com", role=UserRole.attendee, institution="Yale")
        busy = make_user(db, email="alb@t.com", role=UserRole.reviewer, institution="MIT")
        idle = make_user(db, email="ali@t.com", role=UserRole.reviewer, institution="Oxford")
        # Give `busy` an active assignment on another paper.
        other_pid = _paper(client, author, title="Other")
        db.add(Review(paper_id=other_pid, reviewer_id=busy.id, state="accepted"))
        db.commit()
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign/auto", json={"count": 1},
                    cookies=auth_cookie(chair))
        assigned = {r.reviewer_id for r in db.query(Review).filter(Review.paper_id == pid).all()}
        assert assigned == {idle.id}

    def test_auto_skips_already_assigned(self, client, db):
        chair = make_user(db, email="as@t.com", role=UserRole.review_chair)
        author = make_user(db, email="as2@t.com", role=UserRole.attendee, institution="Yale")
        r1 = make_user(db, email="as3@t.com", role=UserRole.reviewer, institution="MIT")
        make_user(db, email="as4@t.com", role=UserRole.reviewer, institution="Oxford")
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [r1.id]}, cookies=auth_cookie(chair))
        client.post(f"/api/papers/{pid}/assign/auto", json={"count": 2},
                    cookies=auth_cookie(chair))
        # 1 manual + 1 auto to reach the target of 2 (no duplicate of r1).
        rows = db.query(Review).filter(Review.paper_id == pid).all()
        assert len(rows) == 2 and len({r.reviewer_id for r in rows}) == 2

    def test_auto_target_already_met(self, client, db):
        chair = make_user(db, email="at@t.com", role=UserRole.review_chair)
        author = make_user(db, email="at2@t.com", role=UserRole.attendee, institution="Yale")
        r1 = make_user(db, email="at3@t.com", role=UserRole.reviewer, institution="MIT")
        r2 = make_user(db, email="at4@t.com", role=UserRole.reviewer, institution="Oxford")
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [r1.id, r2.id]}, cookies=auth_cookie(chair))
        assert client.post(f"/api/papers/{pid}/assign/auto", json={"count": 2},
                           cookies=auth_cookie(chair)).status_code == 400

    def test_auto_requires_chair(self, client, db):
        author = make_user(db, email="ap@t.com", role=UserRole.attendee)
        pid = _paper(client, author)
        assert client.post(f"/api/papers/{pid}/assign/auto", json={},
                           cookies=auth_cookie(author)).status_code == 403


# ─── Score override with written justification ───────────────────
class TestScoreOverride:
    def _decided(self, client, db, n, decision="accept", **extra):
        chair = make_user(db, email=f"so{n}@t.com", role=UserRole.review_chair)
        author = make_user(db, email=f"so{n}a@t.com", role=UserRole.attendee)
        rev = make_user(db, email=f"so{n}r@t.com", role=UserRole.reviewer)
        pid = _paper(client, author)
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [rev.id]}, cookies=auth_cookie(chair))
        client.put(f"/api/papers/{pid}/review",
                   json={"score": 2, "submitted": True}, cookies=auth_cookie(rev))
        body = {"decision": decision}; body.update(extra)
        r = client.post(f"/api/papers/{pid}/decision", json=body, cookies=auth_cookie(chair))
        return chair, author, pid, r

    def test_override_requires_justification(self, client, db):
        _, _, pid, r = self._decided(client, db, 1, override_score=4)
        assert r.status_code == 400
        # The decision was not applied.
        assert db.query(Paper).get(pid).status == PaperStatus.under_review

    def test_override_stored_and_audited(self, client, db):
        chair, _, pid, r = self._decided(
            client, db, 2, decision="accept",
            override_score=4, override_reason="Reviewer under-scored; methods are sound")
        assert r.status_code == 200
        p = db.query(Paper).get(pid)
        assert p.decision_score == 4
        assert "methods are sound" in p.decision_score_reason
        assert p.status == PaperStatus.accepted
        log = db.query(AuditLog).filter(
            AuditLog.action == AuditAction.paper_decision,
            AuditLog.description.like("%Score override%")).first()
        assert log is not None and log.new_value == "4"

    def test_override_visible_to_chair_and_author(self, client, db):
        chair, author, pid, _ = self._decided(
            client, db, 3, override_score=5, override_reason="Exceptional contribution")
        cv = client.get(f"/api/papers/{pid}", cookies=auth_cookie(chair)).json()
        assert cv["decision_score"] == 5 and "Exceptional" in cv["decision_score_reason"]
        av = client.get(f"/api/papers/{pid}", cookies=auth_cookie(author)).json()
        assert av["decision_score"] == 5

    def test_decision_without_override_leaves_score_null(self, client, db):
        _, _, pid, r = self._decided(client, db, 4, decision="reject")
        assert r.status_code == 200
        assert db.query(Paper).get(pid).decision_score is None
