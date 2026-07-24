"""
Knowledge Graph API.

Everything here is computed live from the program (see
``app.core.knowledge``) — there is no separate graph to maintain:

- ``/graph``            visual topic map (nodes + edges), optionally focused
- ``/topics``           trending topics, ranked by Q&A and discussion volume
- ``/related/{kind}/{id}``  "if you liked A, B is relevant because…"
- ``/thread/{topic}``   a research thread following one topic across the days
- ``/cross-pollination`` adjacent topics you haven't engaged with yet
- ``/my-map`` + ``/my-map.pdf``  the attendee's personal map, exportable
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core import knowledge as kg
from app.core.audit_service import log_action
from app.core.config import get_settings
from app.core.database import get_db
from app.core.knowledge_pdf import knowledge_map_pdf
from app.core.security import get_current_user
from app.models.audit_log import AuditAction
from app.models.user import User

router = APIRouter()

ENTITY_KINDS = ("session", "paper", "poster", "person")


@router.get("/graph")
def graph(topic: Optional[str] = Query(None, description="Focus on one topic"),
          limit: int = Query(40, ge=5, le=200),
          db: Session = Depends(get_db),
          user: User = Depends(get_current_user)):
    index = kg.build_index(db)
    focus = kg.normalize_topic(topic) if topic else None
    payload = kg.graph_payload(index, focus=focus, limit=limit)
    payload["topic_count"] = len(index.by_topic)
    payload["entity_count"] = len(index.entities)
    return payload


@router.get("/topics")
def topics(limit: int = Query(30, ge=1, le=200),
           db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """Trending topics — ordered by discussion volume, then breadth."""
    index = kg.build_index(db)
    rows = kg.topic_summary(index)
    return {"total": len(rows), "topics": rows[:limit]}


@router.get("/related/{kind}/{entity_id}")
def related(kind: str, entity_id: int, limit: int = Query(8, ge=1, le=40),
            db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    if kind not in ENTITY_KINDS:
        raise HTTPException(status_code=400,
                            detail=f"Kind must be one of: {', '.join(ENTITY_KINDS)}")
    index = kg.build_index(db)
    entity = kg.find_entity(index, kind, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Not in the knowledge graph")
    return {
        "source": entity.brief(),
        "related": kg.related_to(index, entity, limit=limit),
    }


@router.get("/thread/{topic}")
def thread(topic: str, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """Follow one topic across the congress days."""
    index = kg.build_index(db)
    key = kg.normalize_topic(topic)
    if key not in index.by_topic:
        raise HTTPException(status_code=404, detail="Unknown topic")
    return kg.thread_for(index, key)


@router.get("/cross-pollination")
def cross_pollination(limit: int = Query(6, ge=1, le=20),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    index = kg.build_index(db)
    mine = kg.user_topics(db, index, user)
    return {
        "my_topics": [{"topic": t, "label": kg.label_for(t)} for t in sorted(mine)],
        "suggestions": kg.cross_pollination(index, mine, limit=limit),
    }


@router.get("/my-map")
def my_map(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    index = kg.build_index(db)
    return kg.personal_map(db, index, user)


@router.get("/my-map.pdf")
def my_map_pdf(request: Request, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """Export the personal knowledge map as a PDF."""
    index = kg.build_index(db)
    data = kg.personal_map(db, index, user)
    settings = get_settings()
    pdf = knowledge_map_pdf(
        holder=user.full_name, data=data,
        congress_full=f"{settings.CONGRESS_NAME} {settings.CONGRESS_YEAR}",
        congress_dates=settings.CONGRESS_DATES)
    log_action(db, user, AuditAction.export_data,
               "Exported personal knowledge map (PDF)", request=request,
               target_type="knowledge", target_id=user.id)
    filename = f"knowledge-map-{user.id}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'attachment; filename="{filename}"'})
