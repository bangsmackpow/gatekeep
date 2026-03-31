import uuid
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from elasticsearch import AsyncElasticsearch
from sqlalchemy import text
from src.models.database import async_session_factory
from src.middleware.matter_scope import require_matter, MatterContext

logger = logging.getLogger(__name__)

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    file_types: Optional[list[str]] = None
    sender_email: Optional[str] = None
    author: Optional[str] = None
    page: int = 1
    page_size: int = 20
    fuzzy_distance: int = 1


class SearchResultHit(BaseModel):
    document_id: str
    original_filename: str
    author: Optional[str]
    email_subject: Optional[str]
    sender_email: Optional[str]
    file_extension: str
    sent_date: Optional[datetime]
    score: float
    highlights: dict[str, list[str]]


class SearchResponse(BaseModel):
    query: str
    matter_id: str
    matter_number: str
    total: int
    page: int
    page_size: int
    results: list[SearchResultHit]
    took_ms: int


@router.post("/search", response_model=SearchResponse)
async def search_documents(
    request: Request,
    search: SearchRequest,
    matter: MatterContext = Depends(require_matter),
):
    es: AsyncElasticsearch = request.app.state.es

    bool_query = {
        "must": [],
        "filter": [
            {"term": {"matter_id": str(matter.matter_id)}},
        ],
    }

    if search.query.strip():
        parsed_query = _parse_boolean_query(search.query, search.fuzzy_distance)
        bool_query["must"].append({
            "query_string": {
                "query": parsed_query,
                "default_operator": "AND",
                "fields": [
                    "original_filename^3",
                    "email_subject^2",
                    "author^2",
                    "sender_name^2",
                    "subject",
                    "extracted_text",
                    "ocr_text",
                ],
                "analyze_wildcard": True,
                "allow_leading_wildcard": False,
            }
        })

    if search.date_from or search.date_to:
        date_range = {}
        if search.date_from:
            date_range["gte"] = search.date_from.isoformat()
        if search.date_to:
            date_range["lte"] = search.date_to.isoformat()
        bool_query["filter"].append({"range": {"sent_date": date_range}})

    if search.file_types:
        bool_query["filter"].append({"terms": {"file_extension": [ft.lower() for ft in search.file_types]}})

    if search.sender_email:
        bool_query["filter"].append({"term": {"sender_email": search.sender_email.lower()}})

    if search.author:
        bool_query["filter"].append({"match": {"author": search.author}})

    es_query = {
        "query": {"bool": bool_query},
        "highlight": {
            "fields": {
                "extracted_text": {"fragment_size": 150, "number_of_fragments": 3},
                "ocr_text": {"fragment_size": 150, "number_of_fragments": 3},
                "email_subject": {"fragment_size": 150, "number_of_fragments": 1},
                "original_filename": {"fragment_size": 150, "number_of_fragments": 1},
            },
            "pre_tags": ["<mark>"],
            "post_tags": ["</mark>"],
        },
        "from": (search.page - 1) * search.page_size,
        "size": search.page_size,
        "_source": [
            "document_id", "original_filename", "author", "email_subject",
            "sender_email", "file_extension", "sent_date",
        ],
        "sort": [
            {"_score": "desc"},
            {"sent_date": {"order": "desc", "missing": "_last"}},
        ],
    }

    response = await es.search(index="documents", body=es_query)

    hits = []
    for hit in response["hits"]["hits"]:
        source = hit["_source"]
        highlights = hit.get("highlight", {})
        hits.append(SearchResultHit(
            document_id=source.get("document_id", ""),
            original_filename=source.get("original_filename", ""),
            author=source.get("author"),
            email_subject=source.get("email_subject"),
            sender_email=source.get("sender_email"),
            file_extension=source.get("file_extension", ""),
            sent_date=datetime.fromisoformat(source["sent_date"]) if source.get("sent_date") else None,
            score=hit["_score"],
            highlights={k: v for k, v in highlights.items()},
        ))

    total = response["hits"]["total"]["value"]
    took_ms = response["took"]

    try:
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            async with async_session_factory() as session:
                await session.execute(
                    text("""
                        INSERT INTO search_history (user_id, query_text, filters, result_count)
                        VALUES (:user_id, :query, :filters, :count)
                    """),
                    {
                        "user_id": user_id,
                        "query": search.query,
                        "filters": {
                            "matter_id": str(matter.matter_id),
                            "date_from": search.date_from.isoformat() if search.date_from else None,
                            "date_to": search.date_to.isoformat() if search.date_to else None,
                            "file_types": search.file_types,
                        },
                        "count": total,
                    }
                )
                await session.commit()
    except Exception as e:
        logger.error(f"Failed to log search history: {e}")

    return SearchResponse(
        query=search.query,
        matter_id=str(matter.matter_id),
        matter_number=request.state.matter_number,
        total=total,
        page=search.page,
        page_size=search.page_size,
        results=hits,
        took_ms=took_ms,
    )


def _parse_boolean_query(query: str, fuzzy_distance: int = 1) -> str:
    parsed = query.strip()
    for op in [" AND ", " OR ", " NOT ", " and ", " or ", " not "]:
        if op in parsed:
            parsed = parsed.replace(" and ", " AND ")
            parsed = parsed.replace(" or ", " OR ")
            parsed = parsed.replace(" not ", " NOT ")
            break
    if fuzzy_distance > 0:
        terms = parsed.split()
        fuzzy_terms = []
        for term in terms:
            clean = term.strip('\"()')
            if clean.upper() not in ("AND", "OR", "NOT") and len(clean) > 3:
                fuzzy_terms.append(f"{clean}~{fuzzy_distance}")
            else:
                fuzzy_terms.append(term)
        parsed = " ".join(fuzzy_terms)
    return parsed


@router.get("/search/suggestions")
async def search_suggestions(
    request: Request,
    q: str = Query(..., min_length=2),
    field: Optional[str] = Query(None),
    matter: MatterContext = Depends(require_matter),
):
    es: AsyncElasticsearch = request.app.state.es

    search_fields = [field] if field else [
        "original_filename", "author", "sender_email", "email_subject"
    ]

    suggest_query = {
        "query": {"term": {"matter_id": str(matter.matter_id)}},
        "size": 0,
        "aggs": {
            "suggestions": {
                "filter": {"prefix": {search_fields[0]: q.lower()}},
                "aggs": {
                    "terms": {
                        "terms": {
                            "field": f"{search_fields[0]}.keyword" if len(search_fields) == 1 else "original_filename.keyword",
                            "size": 10,
                            "order": {"_count": "desc"},
                        }
                    }
                }
            }
        }
    }

    response = await es.search(index="documents", body=suggest_query)
    buckets = response["aggregations"]["suggestions"]["terms"]["buckets"]
    return {"suggestions": [b["key"] for b in buckets]}
