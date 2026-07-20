"""
Abstracts & Paper submission + peer review.

Flow: author submits → review chair assigns reviewers → reviewers score/comment
→ chair decides (accept / reject / request revision) → author may resubmit
(bounded rounds). Reviews are hidden from the author until a decision is made,
and the submitter's identity is hidden from reviewers (light double-blind).
"""
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Enum as SAEnum,
    ForeignKey, UniqueConstraint, Index,
)
from app.core.database import Base

MAX_REVISION_ROUNDS = 2  # REV-05


class PaperStatus(str, enum.Enum):
    submitted = "submitted"                    # awaiting reviewer assignment
    under_review = "under_review"              # reviewers assigned
    revision_requested = "revision_requested"  # author must revise & resubmit
    accepted = "accepted"
    rejected = "rejected"
    withdrawn = "withdrawn"


class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)
    author_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    title = Column(String(300), nullable=False)
    authors = Column(Text, nullable=False)        # free-text author list
    category = Column(String(120), nullable=True)
    abstract = Column(Text, nullable=False)
    file_url = Column(String(500), nullable=True)  # link to the full paper/PDF
    status = Column(SAEnum(PaperStatus), default=PaperStatus.submitted,
                    nullable=False, index=True)
    round = Column(Integer, default=1, nullable=False)          # current revision round
    author_response = Column(Text, nullable=True)               # rebuttal on resubmit
    decision_comment = Column(Text, nullable=True)              # chair's note to author
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(Integer, ForeignKey("papers.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    score = Column(Integer, nullable=True)          # 1–5
    comments = Column(Text, nullable=True)          # shared with author on decision
    submitted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("paper_id", "reviewer_id", name="uq_review_paper_reviewer"),
        Index("idx_review_paper", "paper_id"),
    )
