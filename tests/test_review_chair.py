"""
Tests for the review-chair dashboard, reviewer workload, unassign, and
review deadlines.
"""
from datetime import date, timedelta

from app.models.schedule import ScheduleItem  # noqa: F401 - FK resolution
from app.models.user import UserRole
from tests.conftest import auth_cookie, make_user

SUB = {"title": "T", "authors": "A", "abstract": "ab"}


def _setup(client, db, n=0):
    chair = make_user(db, email=f"ch{n}@t.com", role=UserRole.review_chair)
    author = make_user(db, email=f"au{n}@t.com", role=UserRole.attendee)
    r1 = make_user(db, email=f"r1{n}@t.com", role=UserRole.reviewer, institution="Yale")
    r2 = make_user(db, email=f"r2{n}@t.com", role=UserRole.reviewer, institution="MIT")
    pid = client.post("/api/papers", json=SUB, cookies=auth_cookie(author)).json()["id"]
    return chair, author, r1, r2, pid


class TestOverview:
    def test_requires_chair(self, client, attendee):
        assert client.get("/api/papers/overview",
                          cookies=auth_cookie(attendee)).status_code == 403

    def test_counts_and_lists(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        ov = client.get("/api/papers/overview", cookies=auth_cookie(chair)).json()
        assert ov["total"] == 1 and ov["status_counts"]["submitted"] == 1
        # A submitted paper with 0 reviewers needs assignment.
        assert pid in [p["id"] for p in ov["needs_assignment"]]

    def test_reviews_aggregate_and_roster(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [r1.id, r2.id]},
                    cookies=auth_cookie(chair))
        client.put(f"/api/papers/{pid}/review", json={"score": 4, "submitted": True},
                   cookies=auth_cookie(r1))
        ov = client.get("/api/papers/overview", cookies=auth_cookie(chair)).json()
        assert ov["reviews_assigned"] == 2 and ov["reviews_submitted"] == 1
        assert ov["reviews_pending"] == 1
        roster = {r["email"]: r for r in ov["reviewers"]}
        assert roster[r1.email]["completed"] == 1
        assert roster[r2.email]["active"] == 1
        # r1 submitted; r2 still pending → ready-to-decide requires ALL submitted, so not yet.
        assert pid not in [p["id"] for p in ov["awaiting_decision"]]

    def test_awaiting_decision_when_all_in(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [r1.id]},
                    cookies=auth_cookie(chair))
        client.put(f"/api/papers/{pid}/review", json={"score": 5, "submitted": True},
                   cookies=auth_cookie(r1))
        ov = client.get("/api/papers/overview", cookies=auth_cookie(chair)).json()
        assert pid in [p["id"] for p in ov["awaiting_decision"]]


class TestReviewerWorkload:
    def test_picker_shows_load(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        # Assign r1 to a second paper to build load.
        other = client.post("/api/papers", json=SUB, cookies=auth_cookie(author)).json()["id"]
        client.post(f"/api/papers/{other}/assign", json={"reviewer_ids": [r1.id]},
                    cookies=auth_cookie(chair))
        revs = client.get(f"/api/papers/reviewers?paper_id={pid}",
                          cookies=auth_cookie(chair)).json()
        by = {r["email"]: r for r in revs}
        assert by[r1.email]["active_load"] == 1 and by[r2.email]["active_load"] == 0


class TestUnassign:
    def test_remove_unsubmitted_reviewer(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [r1.id, r2.id]},
                    cookies=auth_cookie(chair))
        r = client.request("DELETE", f"/api/papers/{pid}/assign/{r2.id}",
                           cookies=auth_cookie(chair))
        assert r.status_code == 200
        revs = client.get(f"/api/papers/reviewers?paper_id={pid}",
                          cookies=auth_cookie(chair)).json()
        assert not next(x for x in revs if x["id"] == r2.id)["assigned"]

    def test_cannot_remove_submitted(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [r1.id]},
                    cookies=auth_cookie(chair))
        client.put(f"/api/papers/{pid}/review", json={"score": 3, "submitted": True},
                   cookies=auth_cookie(r1))
        assert client.request("DELETE", f"/api/papers/{pid}/assign/{r1.id}",
                              cookies=auth_cookie(chair)).status_code == 400

    def test_removing_last_reverts_to_submitted(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [r1.id]},
                    cookies=auth_cookie(chair))
        r = client.request("DELETE", f"/api/papers/{pid}/assign/{r1.id}",
                           cookies=auth_cookie(chair)).json()
        assert r["status"] == "submitted"

    def test_non_chair_cannot_unassign(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        client.post(f"/api/papers/{pid}/assign", json={"reviewer_ids": [r1.id]},
                    cookies=auth_cookie(chair))
        assert client.request("DELETE", f"/api/papers/{pid}/assign/{r1.id}",
                              cookies=auth_cookie(author)).status_code == 403


class TestDeadline:
    def test_set_and_clear(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        due = (date.today() + timedelta(days=7)).isoformat()
        r = client.put(f"/api/papers/{pid}/deadline", json={"deadline": due},
                       cookies=auth_cookie(chair)).json()
        assert r["review_deadline"] == due and r["is_overdue"] is False
        r = client.put(f"/api/papers/{pid}/deadline", json={"deadline": None},
                       cookies=auth_cookie(chair)).json()
        assert r["review_deadline"] is None

    def test_overdue_flag_and_overview(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        past = (date.today() - timedelta(days=1)).isoformat()
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [r1.id], "deadline": past},
                    cookies=auth_cookie(chair))
        got = client.get(f"/api/papers/{pid}", cookies=auth_cookie(chair)).json()
        assert got["is_overdue"] is True
        ov = client.get("/api/papers/overview", cookies=auth_cookie(chair)).json()
        assert pid in [p["id"] for p in ov["overdue"]]

    def test_bad_date_rejected(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        assert client.put(f"/api/papers/{pid}/deadline", json={"deadline": "not-a-date"},
                          cookies=auth_cookie(chair)).status_code == 422

    def test_reviewer_sees_deadline(self, client, db):
        chair, author, r1, r2, pid = _setup(client, db)
        due = (date.today() + timedelta(days=3)).isoformat()
        client.post(f"/api/papers/{pid}/assign",
                    json={"reviewer_ids": [r1.id], "deadline": due},
                    cookies=auth_cookie(chair))
        seen = client.get(f"/api/papers/{pid}", cookies=auth_cookie(r1)).json()
        assert seen["review_deadline"] == due
