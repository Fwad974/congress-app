"""
Issued certificates — one row per (user, kind), created the first time an
unlocked certificate is viewed or downloaded.

The row gives every certificate a stable identity: a unique ``serial`` that
appears on the printed/PDF certificate (and inside its QR code) and can be
checked by anyone at ``/verify/{serial}`` — so institutions can confirm a
certificate is genuine without an account. ``sessions_count`` / ``credits``
snapshot what the certificate attests to (refreshed if the holder attends
more sessions and re-downloads).
"""
from datetime import datetime, timezone

from sqlalchemy import (Column, Integer, String, DateTime, Boolean,
                        ForeignKey, UniqueConstraint)

from app.core.database import Base


class IssuedCertificate(Base):
    __tablename__ = "issued_certificates"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    kind = Column(String(20), nullable=False)          # attendance | cme | speaker
    serial = Column(String(40), nullable=False, unique=True, index=True)
    issued_at = Column(DateTime(timezone=True),
                       default=lambda: datetime.now(timezone.utc), nullable=False)
    sessions_count = Column(Integer, nullable=False, default=0)
    credits = Column(Integer, nullable=False, default=0)
    # Kill-switch for a certificate issued in error; /verify reports it invalid.
    revoked = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("user_id", "kind", name="uq_issued_cert_user_kind"),
    )
