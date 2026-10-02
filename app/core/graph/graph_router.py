"""
graph_router.py  (FastAPI router — mount this in your main app)
---------------------------------------------------------------
Exposes two endpoints for the iGOT Karmayogi Resolution Graph.

Incoming payload from user / any interface:
    { "email": "...", "message": "..." }

  POST /api/v1/resolution/ingest
    → Fire-and-forget. Processes in the background. Returns immediately.

  POST /api/v1/resolution/process
    → Synchronous. Waits for the graph to finish. Returns final_response.

## ES Ticket Lifecycle

  1. Incoming request arrives.
  2. Check ES for an open+awaiting ticket for this email.
  3. If found → LLM disambiguates: continuation reply OR new request?
       - Continuation: restore state from ES, set is_continuation=True,
         skip intake_node, route directly to saved subgraph.
       - New: normal intake → router flow, new ticket_id.
  4. Run graph (arun_ticket).
  5. Persist result to ES:
       - New ticket  → create_ticket
       - Continuation → update_ticket
  6. Return API response.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

# ── Date parsing helper ────────────────────────────────────────────────────────

_DATE_FMT = "%d-%m-%Y"   # expected user-facing format: DD-MM-YYYY


def _parse_date(value: str | None, *, end_of_day: bool = False) -> str | None:
    """
    Convert a user-supplied DD-MM-YYYY string to an ISO 8601 UTC datetime string
    that Elasticsearch range queries accept.

    - date_from  → start of day (00:00:00 UTC)
    - date_to    → end   of day (23:59:59 UTC)  when end_of_day=True

    Raises HTTPException 400 when the format is wrong.
    Returns None when value is None or empty.
    """
    if not value:
        return None
    try:
        dt = datetime.strptime(value.strip(), _DATE_FMT)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format '{value}'. Expected DD-MM-YYYY (e.g. 09-07-2026).",
        )
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=timezone.utc).isoformat()

from app.core.graph.main_graph import arun_ticket
from app.core.graph.ticket_store import ticket_store
from app.core.utils.constants import RESTRICT_TO_EMAIL_CHANNEL
from app.core.utils.kafka_queue import produce_ticket
from app.core.utils.ticket_tracker import STAGE_QUEUED, ticket_tracker
from app.core.utils.token_tracker import token_tracker
from app.services.zoho_service import get_cleaned_ticket_details, get_ticket_conversations

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/resolution",
    tags=["iGOT Resolution Graph"],
)

ES_TICKET_FIELDS = [
    "ticket_id", "email", "status", "awaiting_clarification",
    "category", "main_category", "route_to", "messages",
    "clarification_question", "partial_match", "graph_plan",
    "retry_count", "final_response", "created_at", "updated_at",
]


# ── Request schema ─────────────────────────────────────────────────────────────

# Removed ResolutionRequest as we are now accepting a dynamic dictionary payload.


# ── Pre-graph: build ticket_dict ───────────────────────────────────────────────

def _new_ticket_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _build_ticket_dict(
    email: str,
    message: Any,
    passed_ticket_id: str | None = None,
    conversations: list[dict] | None = None,
) -> tuple[dict, bool, str | None]:
    """
    Check ES for an open clarification ticket or inspect Zoho conversation history.
    Returns:
      (ticket_dict, is_continuation, existing_ticket_id)
    """
    if isinstance(message, dict):
        message = json.dumps(message)
    elif not isinstance(message, str):
        message = str(message)
        
    open_ticket = None
    target_ticket_id = passed_ticket_id if passed_ticket_id else _new_ticket_id()

    try:
        if passed_ticket_id:
            # specifically requested a ticket
            t = ticket_store.get_ticket(passed_ticket_id)
            if t and t.get("email") == email and t.get("status") == "open":
                open_ticket = t
            else:
                logger.warning(f"[graph_router] Passed ticket_id={passed_ticket_id} not open or mismatch.")
        else:
            open_ticket = ticket_store.get_open_clarification_ticket(email)
    except Exception as e:
        logger.warning(f"[graph_router] ES check failed (proceeding as new): {e}")

    # Prioritize Zoho Desk conversation history, falling back to ES stored messages
    stored_messages = list(conversations) if conversations else list((open_ticket or {}).get("messages") or [])

    is_reply = False
    if open_ticket:
        if passed_ticket_id and open_ticket.get("ticket_id") == passed_ticket_id:
            # Explicit ticket_id match means it's a continuation
            is_reply = True
        else:
            try:
                is_reply = ticket_store.is_continuation_reply(
                    open_ticket,
                    message,
                    ticket_id=open_ticket.get("ticket_id", ""),
                    email=open_ticket.get("email", ""),
                )
            except Exception as e:
                logger.warning(f"[graph_router] Continuation disambiguation failed: {e}")

    # Check if Zoho conversation history indicates prior agent/user exchange
    if not is_reply and conversations:
        has_prior_agent_interaction = any(
            c.get("role") in ("agent", "internal_note") for c in conversations
        )
        if has_prior_agent_interaction:
            is_reply = True
            logger.info(f"[graph_router] Continuation detected via Zoho Desk conversations history for {target_ticket_id}")

    if is_reply:
        # ── Build continuation state ──────────────────────────────────────
        existing_ticket_id = open_ticket.get("ticket_id", target_ticket_id) if open_ticket else target_ticket_id

        # Append the new user message if not already present in stored_messages
        if not stored_messages or stored_messages[-1].get("content") != message:
            stored_messages.append({
                "type":      "thread",
                "role":      "user",
                "author":    f"{email} (User)",
                "content":   message,
                "timestamp": _now_iso(),
                "is_public": True,
            })

        ticket_dict = {
            "ticket_id":            existing_ticket_id,
            "interaction_id":       existing_ticket_id,
            "email":                email,
            "message":              message,
            "is_continuation":      True,
            "conversation_messages": stored_messages,
            # Restore routing info from stored ticket if available
            "category":             (open_ticket or {}).get("category", "general"),
            "main_category":        (open_ticket or {}).get("main_category", "general"),
            "sub_category":         (open_ticket or {}).get("sub_category", ""),
            "sub_category_label":   (open_ticket or {}).get("sub_category_label", ""),
            "route_to":             (open_ticket or {}).get("route_to", "general_query_subgraph"),
            "sop_categories":       [],
            "confidence":           1.0 if open_ticket else 0.8,  # Trust stored routing for continuations
            "retry_count":          0,
            "max_retries":          3,
            "quality_reroute_count": 0,
            "graph_plan": [{
                "node":      "graph_router",
                "detail":    f"Continuation detected. Resuming ticket '{existing_ticket_id}'. "
                             f"Routing to '{(open_ticket or {}).get('route_to', 'general_query_subgraph')}'.",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }],
        }
        logger.info(f"[graph_router] Continuation for ticket={existing_ticket_id}")
        return ticket_dict, True, existing_ticket_id

    # ── New ticket ────────────────────────────────────────────────────────────
    new_id = target_ticket_id
    if not stored_messages or stored_messages[-1].get("content") != message:
        stored_messages.append({
            "type":      "thread",
            "role":      "user",
            "author":    f"{email} (User)",
            "content":   message,
            "timestamp": _now_iso(),
            "is_public": True,
        })

    ticket_dict = {
        "ticket_id":    new_id,
        "interaction_id": new_id,
        "email":        email,
        "message":      message,
        "is_continuation": False,
        "conversation_messages": stored_messages,
        "retry_count":           0,
        "max_retries":           3,
        "quality_reroute_count": 0,
        "graph_plan":            [],
    }
    logger.info(f"[graph_router] New ticket={new_id}")
    return ticket_dict, False, None


# ── Post-graph: persist to ES ──────────────────────────────────────────────────

def _persist(result: dict, is_continuation: bool, existing_ticket_id: str | None):
    """Save or update ticket in ES after the graph completes."""
    try:
        if is_continuation and existing_ticket_id:
            ticket_store.update_ticket(existing_ticket_id, result)
        else:
            ticket_store.create_ticket(result)
    except Exception as e:
        logger.error(f"[graph_router] ES persist failed: {e}")


# ── Response builder ───────────────────────────────────────────────────────────

def _build_response(result: dict) -> dict:
    return {
        "status":               "completed",
        "ticket_id":            result.get("ticket_id"),
        "interaction_id":       result.get("interaction_id") or result.get("ticket_id"),
        "email":                result.get("email"),
        "is_continuation":      result.get("is_continuation", False),
        "is_junk":              result.get("is_junk", False),
        "category":             result.get("category"),
        "main_category":        result.get("main_category"),
        "confidence":           result.get("confidence"),
        "route_to":             result.get("route_to"),
        "needs_clarification":  result.get("needs_clarification", False),
        "partial_match":        result.get("partial_match", False),
        "escalated_to_human":   result.get("escalated_to_human", False),
        "escalation_reason":    result.get("escalation_reason", ""),
        "final_response":       result.get("final_response", ""),
        "retry_count":          result.get("retry_count", 0),
        "quality_passed":       result.get("quality_passed"),
        "graph_plan":           result.get("graph_plan", []),
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/process")
async def process_ticket_sync(req: dict[str, Any]):
    """
    Synchronous endpoint — runs the full resolution graph and returns the answer.
    Ideal for direct chat integrations that need an immediate response.
    """
    email = req.get("email")
    ticket_id = req.get("id")
    channel = req.get("channel")
    
    if RESTRICT_TO_EMAIL_CHANNEL and str(channel or "").lower() != "email":
        raise HTTPException(status_code=400, detail="Only 'Email' channel is allowed")
        
    if not email:
        raise HTTPException(status_code=400, detail="email is required in the payload")
    if not ticket_id:
        raise HTTPException(status_code=400, detail="id is required in the payload")
        
    message = req.get("message")

    # Fetch conversations if ticket_id is provided
    conversations = []
    if ticket_id:
        try:
            conversations = await get_ticket_conversations(ticket_id)
        except Exception as e:
            logger.warning(f"[process] Failed to fetch conversations for {ticket_id}: {e}")

    ticket_dict, is_continuation, existing_id = await _build_ticket_dict(
        email, message, ticket_id, conversations=conversations
    )
    tid = ticket_dict["ticket_id"]
    logger.info(f"[process] ticket_id={tid} continuation={is_continuation}")

    try:
        result = await arun_ticket(ticket_dict)
        _persist(result, is_continuation, existing_id)
        return _build_response(result)
    except Exception as e:
        logger.error(f"[process] ticket={tid} error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tickets")
async def get_tickets_endpoint(
    ticket_id: str | None = None,
    limit: int = 10,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    Get tickets from the ticket store.

    Query params:
      - ticket_id : return a specific ticket
      - limit     : max results (default 10)
      - date_from : DD-MM-YYYY e.g. 01-07-2026
      - date_to   : DD-MM-YYYY e.g. 09-07-2026
    """
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    tickets = ticket_store.get_tickets(
        ticket_id=ticket_id,
        limit=limit,
        date_from=df,
        date_to=dt,
    )
    return {
        "status": "success",
        "count": len(tickets),
        "date_from": date_from,
        "date_to": date_to,
        "tickets": tickets,
    }


@router.get("/tickets/stats")
async def get_tickets_stats(
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    Returns statistics on tickets including category/sub-category breakdown
    and count of early exits.

    Query params:
      - date_from : DD-MM-YYYY e.g. 01-07-2026
      - date_to   : DD-MM-YYYY e.g. 09-07-2026
    """
    from app.core.utils.ticket_tracker import ticket_tracker
    if not ticket_tracker.client:
        raise HTTPException(status_code=503, detail="Elasticsearch not connected")

    # Parse DD-MM-YYYY → ISO and build optional date filter
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    date_filter: dict | None = None
    if df or dt:
        dr: dict = {}
        if df:
            dr["gte"] = df
        if dt:
            dr["lte"] = dt
        date_filter = {"range": {"created_at": dr}}

    query: dict = {
        "size": 0,
        "aggs": {
            "categories": {
                "terms": {"field": "category", "size": 100},
                "aggs": {
                    "main_categories": {
                        "terms": {"field": "main_category", "size": 100},
                        "aggs": {
                            "sub_categories": {
                                "terms": {"field": "sub_category", "size": 100}
                            }
                        }
                    }
                }
            },
            "early_exits": {
                "filter": {
                    "bool": {
                        "must": [{"term": {"status": "resolved"}}],
                        "must_not": [{"term": {"graph_plan.node.keyword": "router_node"}}]
                    }
                }
            }
        }
    }

    if date_filter:
        query["query"] = date_filter

    try:
        res = ticket_tracker.client.search(index="aurora_igot_user_tickets", body=query)
        aggs = res.get("aggregations", {})

        categories_data = []
        for cat_bucket in aggs.get("categories", {}).get("buckets", []):
            main_cats = []
            for main_bucket in cat_bucket.get("main_categories", {}).get("buckets", []):
                sub_cats = [
                    {"name": sub["key"], "count": sub["doc_count"]}
                    for sub in main_bucket.get("sub_categories", {}).get("buckets", [])
                ]
                main_cats.append({
                    "name": main_bucket["key"],
                    "count": main_bucket["doc_count"],
                    "sub_categories": sub_cats,
                })
            categories_data.append({
                "category": cat_bucket["key"],
                "total_count": cat_bucket["doc_count"],
                "main_categories": main_cats,
            })

        return {
            "status": "success",
            "date_from": date_from,
            "date_to": date_to,
            "early_exits_count": aggs.get("early_exits", {}).get("doc_count", 0),
            "categories": categories_data,
        }
    except Exception as e:
        logger.error(f"Failed to fetch ticket stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve ticket statistics")


@router.delete("/tickets/cleanup")
async def cleanup_old_tickets(days_old: int = 10):
    """
    Deletes resolved tickets that meet ALL criteria:
    1. status = "resolved"
    2. awaiting_clarification = False
    3. updated_at is older than `days_old` days (default: 10)

    Query params:
      - days_old: Number of days (default: 10)

    Returns:
      - deleted_count: Number of tickets deleted
      - errors: List of any errors encountered
    """
    if days_old < 1:
        raise HTTPException(status_code=400, detail="days_old must be at least 1")

    logger.info(f"[cleanup] Starting cleanup of resolved tickets older than {days_old} days")

    try:
        result = ticket_store.delete_old_resolved_tickets(days_old=days_old)
        return {
            "status": "completed",
            "deleted_count": result["deleted_count"],
            "criteria": {
                "status": "resolved",
                "awaiting_clarification": False,
                "days_old": days_old,
            },
            "errors": result["errors"],
        }
    except Exception as e:
        logger.error(f"[cleanup] Cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest")
async def ingest_ticket(request: Request):
    """
    Fire-and-forget. Accepts Zoho Desk webhook events, enqueues ticket to
    Redis queue for processing. Returns immediately with ticket_id.

    Supports both flat dict payloads and list-wrapped Zoho webhook events:
      - [{"payload": {...}, "eventType": "Ticket_Add", ...}]
      - {"payload": {...}}
    """
    # --------------------------------------------------
    # Parse Body
    # --------------------------------------------------
    try:
        body = await request.json()
        logger.debug(f"[ingest] Payload received: {body}")
    except Exception:
        raw = await request.body()
        logger.warning(f"[ingest] Received non-JSON body: {raw[:200]}")
        return {"status": "ignored", "message": "Non-JSON payload received"}

    # --------------------------------------------------
    # Zoho Webhook Payload Handling
    # --------------------------------------------------
    if isinstance(body, list):
        if not body:
            raise HTTPException(status_code=400, detail="Empty payload list")
        event = body[0]
    elif isinstance(body, dict):
        event = body
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unexpected payload type: {type(body).__name__}"
        )

    payload = event.get("payload") or event.get("ticket") or event  # support wrapped (payload/ticket) and flat
    if isinstance(payload, list):
        payload = payload[0] if payload else {}

    email = payload.get("email")
    # Fallback: some Zoho events nest email under contact
    if not email:
        email = (payload.get("contact") or {}).get("email")

    ticket_id = payload.get("id")
    channel = payload.get("channel")

    # --------------------------------------------------
    # Channel Restriction — return 200 so Zoho doesn't retry
    # --------------------------------------------------
    if RESTRICT_TO_EMAIL_CHANNEL and str(channel or "").strip().lower() != "email":
        logger.info(f"[ingest] Skipping non-email channel: {channel}")
        return {
            "status": "ignored",
            "message": f"Only Email channel is processed. Received: {channel}",
        }

    if not email:
        raise HTTPException(status_code=400, detail="email is required in the payload")
    if not ticket_id:
        raise HTTPException(status_code=400, detail="id is required in the payload")

    # --------------------------------------------------
    # Fetch & Clean Zoho Ticket Details and Conversations in Parallel
    # --------------------------------------------------
    try:
        cleaned_ticket_data, conversations = await asyncio.gather(
            get_cleaned_ticket_details(ticket_id),
            get_ticket_conversations(ticket_id),
        )
    except Exception as e:
        logger.error(f"[ingest] Failed to fetch ticket details for {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch ticket details: {e}")

    # --------------------------------------------------
    # Build Ticket Dict & Enqueue
    # --------------------------------------------------
    ticket_dict, is_continuation, existing_id = await _build_ticket_dict(
        email, cleaned_ticket_data, ticket_id, conversations=conversations
    )
    tid = ticket_dict["ticket_id"]

    ticket_data = {
        "ticket_dict": ticket_dict,
        "is_continuation": is_continuation,
        "existing_id": existing_id,
    }

    # ── Ticket lifecycle tracking ────────────────────────────────────────────
    # MUST be created BEFORE producing to Kafka to prevent race condition
    # where worker picks it up and updates 'in-progress' before the doc exists!
    ticket_tracker.create(
        ticket_id=tid,
        email=email,
        zoho_ticket_id=ticket_id,
        is_continuation=is_continuation,
        subject=ticket_dict.get("subject", ""),
        body=ticket_dict.get("message", ""),
    )

    success = await produce_ticket(ticket_data)
    logger.info(f"[ingest] produce result={success} ticket_id={tid} continuation={is_continuation}")

    if not success:
        # If Kafka fails, we should ideally rollback the ES doc, but for now we raise 500
        raise HTTPException(status_code=500, detail="Failed to enqueue ticket")

    logger.info(f"[ingest] ticket_id={tid} continuation={is_continuation} queued")
    return {
        "status":          "queued",
        "ticket_id":       tid,
        "interaction_id":  tid,
        "is_continuation": is_continuation,
        "message":         "Your request has been queued and will be processed shortly.",
    }


# ── Ticket Tracking Endpoints ──────────────────────────────────────────────────

@router.get("/tracking")
async def list_tracked_tickets(
    size: int = 500,
    from_: int = 0,
    email: str | None = None,
    stage: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    List tickets from the tracking index, newest first.

    Query params:
      - stage     : filter by current stage — queued | in-progress | resolved
      - email     : filter by sender email
      - date_from : DD-MM-YYYY e.g. 01-07-2026
      - date_to   : DD-MM-YYYY e.g. 09-07-2026
      - size, from_: pagination
    """
    from app.core.utils.ticket_tracker import STAGE_IN_PROGRESS, STAGE_RESOLVED
    allowed_stages = {STAGE_QUEUED, STAGE_IN_PROGRESS, STAGE_RESOLVED}
    if stage and stage not in allowed_stages:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage '{stage}'. Allowed values: {sorted(allowed_stages)}"
        )
    result = ticket_tracker.list_all(
        size=size,
        from_=from_,
        email=email,
        stage=stage,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to, end_of_day=True),
    )
    return {
        "status":   "success",
        "stage":    stage or "all",
        "date_from": date_from,
        "date_to":   date_to,
        "total":    result["total"],
        "count":    len(result["tickets"]),
        "tickets":  result["tickets"],
    }


@router.get("/tracking/{ticket_id}")
async def get_tracked_ticket(ticket_id: str):
    """
    Fetch full lifecycle details for a specific ticket by ticket_id.
    Returns all stages and final result.
    """
    doc = ticket_tracker.get(ticket_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Tracking record not found for ticket_id={ticket_id}")
    return {"status": "success", "ticket": doc}


# ── Token usage endpoints ─────────────────────────────────────────────────────

@router.get("/token-usage")
async def list_token_usages(
    size: int = 500,
    from_: int = 0,
    email: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    List individual ticket token usage from the token_usage_index, newest first.

    Query params:
      - email     : filter by sender email
      - date_from : DD-MM-YYYY e.g. 01-07-2026
      - date_to   : DD-MM-YYYY e.g. 09-07-2026
      - size, from_: pagination
    """
    result = token_tracker.list_all(
        size=size,
        from_=from_,
        email=email,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to, end_of_day=True),
    )
    return {
        "status": "success",
        "date_from": date_from,
        "date_to": date_to,
        "total": result["total"],
        "count": len(result["token_usages"]),
        "token_usages": result["token_usages"],
    }


@router.get("/token-usage/stats")
async def get_token_usage_stats(
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    Returns aggregated LLM token consumption across processed tickets.

    Query params:
      - date_from : DD-MM-YYYY e.g. 01-07-2026
      - date_to   : DD-MM-YYYY e.g. 09-07-2026

    Response fields:
      - total_tickets, total_prompt_tokens, total_completion_tokens,
        total_tokens, total_llm_calls, avg_tokens_per_ticket,
        avg_processing_time_s, by_model
    """
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    stats = token_tracker.aggregate_stats(date_from=df, date_to=dt)
    if stats is None:
        raise HTTPException(
            status_code=503,
            detail="Token tracker unavailable — Elasticsearch may be unreachable.",
        )
    model_stats = token_tracker.get_model_stats(date_from=df, date_to=dt) or {}
    return {"status": "success", "date_from": date_from, "date_to": date_to, **stats, **model_stats}


@router.get("/token-usage/model-stats")
async def get_token_usage_by_model(
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    Returns per-model LLM token usage aggregated across tickets.

    Query params:
      - date_from : DD-MM-YYYY e.g. 01-07-2026
      - date_to   : DD-MM-YYYY e.g. 09-07-2026

    Response: by_model: { "<model-name>": { prompt, completion, total, calls } }
    """
    model_stats = token_tracker.get_model_stats(
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to, end_of_day=True),
    )
    if model_stats is None:
        raise HTTPException(
            status_code=503,
            detail="Token tracker unavailable — Elasticsearch may be unreachable.",
        )
    return {"status": "success", "date_from": date_from, "date_to": date_to, **model_stats}


@router.get("/token-usage/{ticket_id}")
async def get_token_usage_for_ticket(ticket_id: str):
    """
    Returns detailed LLM token consumption breakdown for a single ticket,
    including per-model and per-node breakdowns.
    """
    doc = token_tracker.get(ticket_id)
    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"No token usage record found for ticket_id={ticket_id}",
        )
    return {"status": "success", "token_usage": doc}


@router.get("/tickets/resolution-time-stats")
async def get_tickets_resolution_time_stats(
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    Returns resolution time statistics (fastest and slowest 5 tickets).

    Query params:
      - date_from : DD-MM-YYYY e.g. 01-07-2026
      - date_to   : DD-MM-YYYY e.g. 09-07-2026
    """
    stats = token_tracker.get_resolution_time_stats(
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to, end_of_day=True),
    )
    if stats is None:
        raise HTTPException(
            status_code=503,
            detail="Token tracker stats unavailable — Elasticsearch may be unreachable.",
        )
    return {"status": "success", "date_from": date_from, "date_to": date_to, **stats}


@router.get("/tickets/agent-stats")
async def get_tickets_agent_stats(
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    Returns agent performance statistics.

    Query params:
      - date_from : DD-MM-YYYY e.g. 01-07-2026
      - date_to   : DD-MM-YYYY e.g. 09-07-2026
    """
    from app.core.graph.ticket_store import ES_INDEX
    if not ticket_tracker.client:
        raise HTTPException(status_code=503, detail="Elasticsearch not connected")

    # Parse DD-MM-YYYY → ISO and build optional date filter
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    must_clauses: list = []
    if df or dt:
        dr: dict = {}
        if df:
            dr["gte"] = df
        if dt:
            dr["lte"] = dt
        must_clauses.append({"range": {"created_at": dr}})

    top_level_query = ({"bool": {"must": must_clauses}} if must_clauses else {"match_all": {}})

    query = {
        "size": 0,
        "query": top_level_query,
        "aggs": {
            "categories": {
                "terms": {"field": "category", "size": 100}
            },
            "addressed_by_agent_status": {
                "filter": {
                    "bool": {
                        "must": must_clauses,
                        "must_not": [{"term": {"category": "general"}}],
                    }
                },
                "aggs": {
                    "statuses": {
                        "terms": {"field": "status", "size": 10}
                    }
                }
            }
        }
    }

    try:
        res = ticket_tracker.client.search(index=ES_INDEX, body=query)
        total_tickets = res.get("hits", {}).get("total", {}).get("value", 0)
        
        # Get count of general tickets
        general_tickets = 0
        categories_buckets = res.get("aggregations", {}).get("categories", {}).get("buckets", [])
        for bucket in categories_buckets:
            if bucket.get("key") == "general":
                general_tickets = bucket.get("doc_count", 0)
                break
        
        addressed_by_agent = total_tickets - general_tickets
        
        # Get counts for addressed tickets by status
        resolved_by_agent = 0
        escalated_to_human = 0
        awaiting_clarification = 0
        
        addressed_buckets = res.get("aggregations", {}).get("addressed_by_agent_status", {}).get("statuses", {}).get("buckets", [])
        for bucket in addressed_buckets:
            status_key = bucket.get("key")
            count = bucket.get("doc_count", 0)
            if status_key == "resolved":
                resolved_by_agent = count
            elif status_key == "escalated":
                escalated_to_human = count
            elif status_key == "open":
                awaiting_clarification = count

        # Percentages
        pct_general = round((general_tickets / total_tickets) * 100, 2) if total_tickets > 0 else 0.0
        pct_addressed = round((addressed_by_agent / total_tickets) * 100, 2) if total_tickets > 0 else 0.0
        
        pct_resolved = round((resolved_by_agent / addressed_by_agent) * 100, 2) if addressed_by_agent > 0 else 0.0
        pct_escalated = round((escalated_to_human / addressed_by_agent) * 100, 2) if addressed_by_agent > 0 else 0.0
        pct_clarification = round((awaiting_clarification / addressed_by_agent) * 100, 2) if addressed_by_agent > 0 else 0.0

        return {
            "status": "success",
            "date_from": date_from,
            "date_to": date_to,
            "metrics": {
                "total_tickets": total_tickets,
                "general_category_tickets": {
                    "count": general_tickets,
                    "percentage": pct_general
                },
                "tickets_addressed_by_agent": {
                    "count": addressed_by_agent,
                    "percentage": pct_addressed,
                    "breakdown": {
                        "resolved_by_agent": {
                            "count": resolved_by_agent,
                            "percentage": pct_resolved
                        },
                        "escalated_to_human": {
                            "count": escalated_to_human,
                            "percentage": pct_escalated
                        },
                        "awaiting_clarification": {
                            "count": awaiting_clarification,
                            "percentage": pct_clarification
                        }
                    }
                }
            }
        }
    except Exception as e:
        logger.error(f"Failed to fetch agent stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve agent performance statistics")

