"""
services/zoho_service.py
-------------------------
Zoho Desk OAuth2 token manager and HTTP client for the iGOT Aurora agent.

Handles:
  - OAuth2 refresh-token flow to obtain short-lived access tokens
  - In-memory token caching with expiry check
  - Fetching full Zoho Desk ticket details (HTML body stripped to plain text)
  - Creating draft replies (HIL workflow — nothing is sent to the customer)

Custom exceptions:
  - ZohoAPIError   : base exception for all Zoho API failures
  - ZohoAuthError  : raised when token retrieval fails

Environment variables required (optional — Zoho integration is optional):
  ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN, ZOHO_ORG_ID,
  ZOHO_FROM_ADDRESS (defaults to mission.karmayogi@gov.in)
"""
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from app.core.utils.config import (
    ZOHO_ACCOUNTS_URL,
    ZOHO_CLIENT_ID,
    ZOHO_CLIENT_SECRET,
    ZOHO_DESK_URL,
    ZOHO_FROM_ADDRESS,
    ZOHO_ORG_ID,
    ZOHO_REFRESH_TOKEN,
)

logger = logging.getLogger(__name__)

# In-memory token cache
_cached_token = None
_token_expires_at = 0.0

class ZohoAPIError(Exception):
    """Base exception for Zoho API failures."""


class ZohoAuthError(ZohoAPIError):
    """Raised when Zoho OAuth token retrieval fails."""


async def get_valid_access_token() -> str:
    """
    Checks the in-memory cache for a valid access token. If not found or expired,
    makes a refresh API call, caches the new access token, and returns it.
    """
    global _cached_token, _token_expires_at
    
    if _cached_token and time.monotonic() < _token_expires_at:
        return _cached_token

    logger.info("Access token not found or expired in memory. Requesting a new one.")

    url = f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token"
    params = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, params=params)
        response.raise_for_status()
        data = response.json()

        access_token = data.get("access_token")
        # Default to 3600 seconds (1 hour) if expires_in is not provided
        expires_in = int(data.get("expires_in", 3600))

        if access_token:
            # Cache the token with slightly less expiry time to avoid edge cases (e.g., 5 mins less)
            cache_time = max(0, expires_in - 300)
            _cached_token = access_token
            _token_expires_at = time.monotonic() + cache_time
            return access_token
        else:
            logger.error(f"Failed to get access token from response: {data}")
            raise ZohoAuthError("Could not fetch Zoho access token")


async def get_tickets_list(limit: int = 99, from_idx: int = 0, status: str = "Open") -> dict:
    """
    Fetches a list of tickets from Zoho Desk API.
    """
    access_token = await get_valid_access_token()

    url = f"{ZOHO_DESK_URL}/api/v1/tickets"
    params = {
        "limit": limit,
        "from": from_idx,
        "sortBy": "-modifiedTime",
        "fields": "id,ticketNumber,modifiedTime",
        "include": "assignee",
        "status": status
    }

    headers = {
        "orgId": ZOHO_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


async def get_ticket_details(ticket_id: str) -> dict:
    """
    Fetches the details of a specific ticket from Zoho Desk API.
    """
    access_token = await get_valid_access_token()

    url = f"{ZOHO_DESK_URL}/api/v1/tickets/{ticket_id}"

    headers = {
        "orgId": ZOHO_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def create_draft_reply(ticket_id: str, content: str, to: str) -> dict:
    """
    Creates a DRAFT reply on a Zoho Desk ticket.

    The draft sits in the ticket's Reply box inside Zoho Desk and is NOT
    sent to the customer. The L1 support agent reviews the AI-generated
    resolution and manually clicks Send — this is the HIL checkpoint.

    Nothing changes the ticket status. No notification is sent.

    Args:
        ticket_id : Zoho internal ticket ID (long numeric string)
        content   : HTML body of the AI-generated resolution
        to        : customer email address (reply-to)

    Returns:
        Zoho API response dict with draft id and status='DRAFT'
    """
    access_token = await get_valid_access_token()

    headers = {
        "orgId":         ZOHO_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type":  "application/json",
    }

    body = {
        "channel":          "EMAIL",
        "contentType":      "html",
        "content":          content,
        "to":               to,
        "fromEmailAddress": ZOHO_FROM_ADDRESS,
        "isForward":        False,
    }

    url = f"{ZOHO_DESK_URL}/api/v1/tickets/{ticket_id}/draftReply"
    logger.info(f"[zoho] Creating draft reply for ticket {ticket_id} -> to={to}")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=headers, json=body)

    if response.status_code >= 400:
        logger.error(
            f"[zoho] draftReply failed for ticket {ticket_id}: "
            f"{response.status_code} {response.text}"
        )
        raise ZohoAPIError(
            f"draftReply {response.status_code}: {response.text}"
        )

    result = response.json()
    logger.info(
        f"[zoho] Draft created for ticket {ticket_id}: "
        f"draft_id={result.get('id')} status={result.get('status')}"
    )
    return result


def extract_email_body(html_content: str, strip_signature: bool = True, strip_disclaimer: bool = True) -> str:
    if not html_content:
        return ""
    
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove signature blocks (Zoho/Outlook: id or class contains "Signature")
    if strip_signature:
        for el in soup.find_all(attrs={"id": re.compile(r"signature", re.IGNORECASE)}):
            el.decompose()
        for el in soup.find_all(attrs={"class": re.compile(r"signature", re.IGNORECASE)}):
            el.decompose()

    # Remove noise tags
    for tag in soup.find_all(["img", "style", "script", "head"]):
        tag.decompose()

    text = soup.get_text(separator="\n")

    # Strip disclaimer and everything after it
    if strip_disclaimer:
        match = re.search(r"(DISCLAIMER:|This email is confidential|strictly forbidden)", text, re.IGNORECASE)
        if match:
            text = text[:match.start()]

    # Normalize whitespace
    lines = [line.strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)

    return cleaned.strip()


async def get_cleaned_ticket_details(ticket_id: str) -> dict:
    """
    Fetches the details of a specific ticket and returns only the desired fields,
    cleaning HTML out of the description.
    """
    data = await get_ticket_details(ticket_id)
    
    desired_keys = [
        "modifiedTime", "subject", "departmentId", "channel", 
        "source", "createdTime", "id", "phone", "status", 
        "ticketNumber", "description", "email"
    ]
    
    filtered_data = {k: data.get(k) for k in desired_keys if k in data}
    
    if filtered_data.get("description"):
        filtered_data["description"] = extract_email_body(filtered_data["description"])
        
    return filtered_data


async def ensure_aurora_tag(ticket_id: str) -> dict:
    """
    Ensures the 'aurora' tag is associated with a Zoho Desk ticket.

    The function first retrieves the ticket's existing tags. If the
    'aurora' tag is already present, no update is performed. Otherwise,
    the 'aurora' tag is associated with the ticket.

    Args:
        ticket_id : Zoho internal ticket ID (long numeric string)

    Returns:
        Zoho API response dict from the associate-tag API when the tag
        is added, or a dict indicating that no update was required.
    """
    access_token = await get_valid_access_token()

    headers = {
        "orgId":         ZOHO_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type":  "application/json",
    }

    # Get existing tags
    get_tags_url = f"{ZOHO_DESK_URL}/api/v1/tickets/{ticket_id}/tags"

    logger.info(
        f"[zoho] Checking tags for ticket {ticket_id}"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            get_tags_url,
            headers=headers,
        )

    if response.status_code >= 400:
        logger.error(
            f"[zoho] Failed to get tags for ticket {ticket_id}: "
            f"{response.status_code} {response.text}"
        )
        raise ZohoAPIError(
            f"get tags {response.status_code}: {response.text}"
        )

    result = response.json()

    tags = result.get("tags", [])

    # Check whether 'aurora' already exists
    aurora_present = any(
        tag.get("name") == "aurora"
        for tag in tags
    )

    if aurora_present:
        logger.info(
            f"[zoho] Tag 'aurora' already exists on ticket {ticket_id}. "
            "Skipping tag update."
        )
        return {
            "ticket_id": ticket_id,
            "tag": "aurora",
            "updated": False,
            "reason": "tag_already_present",
        }

    # 'aurora' is not present, so associate it
    associate_tag_url = (
        f"{ZOHO_DESK_URL}/api/v1/tickets/{ticket_id}/associateTag"
    )

    body = {
        "tags": [
            "aurora"
        ]
    }

    logger.info(
        f"[zoho] Tag 'aurora' not found on ticket {ticket_id}. "
        "Associating tag."
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            associate_tag_url,
            headers=headers,
            json=body,
        )

    if response.status_code >= 400:
        logger.error(
            f"[zoho] Failed to associate tag 'aurora' "
            f"with ticket {ticket_id}: "
            f"{response.status_code} {response.text}"
        )
        raise ZohoAPIError(
            f"associate tag {response.status_code}: {response.text}"
        )

    result = response.json()

    logger.info(
        f"[zoho] Tag 'aurora' associated with ticket {ticket_id}"
    )

    return result


def is_system_or_bounce(item: dict) -> bool:
    """Detect automated delivery failure / system mailer daemon messages."""
    author = item.get("author") or {}
    author_name = str(author.get("name") or "").lower()
    from_addr = str(item.get("fromEmailAddress") or "").lower()
    summary = str(item.get("summary") or "").lower()

    if "mailer-daemon" in author_name or "mailer-daemon" in from_addr:
        return True
    if "mail delivery software" in summary or "could not be delivered" in summary:
        return True
    return False


async def get_ticket_conversations_raw(ticket_id: str) -> dict:
    """Fetches raw conversations JSON from Zoho Desk API."""
    access_token = await get_valid_access_token()
    url = f"{ZOHO_DESK_URL}/api/v1/tickets/{ticket_id}/conversations"
    headers = {
        "orgId": ZOHO_ORG_ID,
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def get_ticket_conversations(ticket_id: str, max_turns: int = 7) -> list[dict]:
    """
    Fetches, cleans, filters, and sorts conversations (threads and comments)
    for a specific ticket.
    Returns the last `max_turns` messages in chronological order (oldest -> newest).
    """
    try:
        raw_data = await get_ticket_conversations_raw(ticket_id)
    except Exception as e:
        logger.warning(f"[zoho] Failed to fetch conversations for ticket {ticket_id}: {e}")
        return []

    data = raw_data.get("data", [])
    if not isinstance(data, list):
        return []

    parsed_items = []
    for item in data:
        if not isinstance(item, dict):
            continue

        # 1. Filter out bounce and system emails
        if is_system_or_bounce(item):
            continue

        # 2. Skip initial ticket description thread (already in ticket details)
        if item.get("isDescriptionThread") is True:
            continue

        item_type = item.get("type", "thread")
        timestamp = (
            item.get("commentedTime")
            or item.get("createdTime")
            or item.get("modifiedTime")
            or ""
        )

        if item_type == "comment":
            raw_content = item.get("content") or item.get("encodedContent") or ""
            clean_content = extract_email_body(raw_content) if raw_content else ""
            if not clean_content:
                continue

            commenter = item.get("commenter") or {}
            author_name = commenter.get("name") or "L1 Support Agent"
            is_public = item.get("isPublic", False)
            role = "agent" if is_public else "internal_note"

            parsed_items.append({
                "type": "comment",
                "role": role,
                "author": f"{author_name} (L1 Agent)",
                "content": clean_content,
                "timestamp": timestamp,
                "is_public": is_public,
            })

        elif item_type == "thread":
            direction = str(item.get("direction") or "").lower()
            author = item.get("author") or {}
            author_name = author.get("name") or ("User" if direction == "in" else "Agent")
            role = "user" if direction == "in" else "agent"

            raw_content = item.get("content") or item.get("summary") or ""
            if ("<" in raw_content and ">" in raw_content):
                clean_content = extract_email_body(raw_content)
            else:
                clean_content = raw_content.strip()

            if not clean_content:
                continue

            parsed_items.append({
                "type": "thread",
                "role": role,
                "author": f"{author_name} ({'User' if role == 'user' else 'Agent'})",
                "content": clean_content,
                "timestamp": timestamp,
                "is_public": True,
            })

    # Sort chronologically (oldest first -> newest last)
    parsed_items.sort(key=lambda x: x.get("timestamp") or "")

    # Apply sliding window of max_turns
    return parsed_items[-max_turns:]