"""
tools/profile_user_management_tools.py
-----------------------------------------
Tools used exclusively by the ProfileUserManagementSubgraph.

Covers SOP workflows from Agent_SOP_Profile_User_Management.md:

  SOP-A1  Access Revoked (transfer request already raised)
          get_user_transfer_request_details -> STEP 1 wfTransferRequest check
                                                 + greeting name
          get_mdo_details_by_org_id          -> STEP 2 MDO lookup for the
                                                 target transfer organisation
                                                 (not the user's own org)

  SOP-A3  Profile Verification / Verified Badge — Designation or Group Not
          Verified (see the UC-05 API Integration Guide)
          get_profile_verification_request_details -> STEP 1
                                                 wfProfileDesignationRequest /
                                                 wfProfileGroupRequest check
          get_department_mdo_admin           -> STEP 2 MDO point-of-contact
                                                 lookup for the pending
                                                 request's target department
                                                 (channel filter, MDO_LEADER
                                                 preferred, MDO_ADMIN fallback)

  SOP-P3  Profile Update - Designation Not Found
          search_designation                 -> STEP 1 identify the exact
                                                 designation against master data
          get_user_root_org_id                -> STEP 2 the user's own rootOrgId
          get_org_imported_designations        -> STEP 2 has this org's MDO
                                                 imported that designation yet

  SOP-P5  Profile Update - Mother Tongue Update
          check_mother_tongue_available       -> STEP 1 verify the reported
                                                 mother tongue against master
                                                 data. Not found -> silent
                                                 human_queue hand-off (nothing
                                                 actionable to tell the user;
                                                 only a human can decide
                                                 whether to add it)
  SOP-P7  Profile Update - Date of Retirement Update
          get_user_ehrms_details -> EHRMS ID set or not; if not, MDO then
                                     YP/SPOC fallback

  SOP-P12 Profile Update - Profile Completion Not Showing 100%
          get_profile_completion_details -> STEP 1 profile_completion_percentage
                                     + Profile Photo / Cover Photo / Profile
                                     Verification / About Me / Designation /
                                     Group (two-step: search then read)

  SOP-P8  Profile Update - Service History Update
          get_own_profile_details (alias get_user_profile) -> STEP 1 current
                                     rootOrgName/designation vs. what the user
                                     says it should be
          search_organization      -> STEP 2 resolve the org NAMED BY THE
                                     USER to an org_id (not the user's own,
                                     currently-mapped org)
          get_mdo_details_by_org_id -> STEP 2 MDO Admin for that org, to
                                     approve the resulting Transfer Request

  SOP-A2  Email / Mobile Already Registered
          validate_new_contact_domain -> STEP 2 domain check on the NEW contact
          check_contact_registered    -> STEP 3 duplicate-registration check
          get_enrollment_summary      -> STEP 4.1 enrollment counts for the
                                          already-registered (other) account

          NOTE on parameter naming: execute_node's secure tool-email-injection
          (base_subgraph.py) force-overwrites any tool argument literally named
          `email` with the ticket owner's own address, so the LLM can never
          control which account a tool inspects. SOP-A2 genuinely needs to look
          up a DIFFERENT contact (the new email/mobile, which may belong to
          another person entirely) — so its tools use `new_email`/`new_contact`
          param names to stay outside that guard. To keep this from becoming an
          unbounded PII-lookup surface, only generic fields (is_registered,
          rootOrgName, enrollment counts) are ever returned, and the SOP script
          restricts what reaches the end user: the matched account's identity
          and course-level details are for the internal escalation note only,
          never quoted back to the customer.
"""

import json
import logging

import requests
from langchain.tools import tool

from app.core.utils.config import IGOT_API_HOST_URL, IGOT_KEY
from app.core.utils.mdo_lookup import find_mdo_contact, find_mdo_contact_by_channel

logger = logging.getLogger(__name__)

CONTENT_TYPE_JSON = "application/json"
USER_PROFILE_NOT_FOUND_MESSAGE = "User profile not found."
USER_EMAIL_PLACEHOLDER = "{{USER_EMAIL}}"
MDO_ADMIN_NAME_PLACEHOLDER = "{{MDO_ADMIN_NAME}}"
MDO_ADMIN_EMAIL_PLACEHOLDER = "{{MDO_ADMIN_EMAIL}}"


# ── SOP-A1 STEP 1 — wfTransferRequest check ─────────────────────────────────

@tool
def get_user_transfer_request_details(email: str) -> str:
    """Check whether the user has an existing pending organisation transfer
    request, via the User Search API's wfTransferRequest field.

    Used in SOP-A1 STEP 1 to determine whether a transfer request has already
    been raised, and if so, which organisation it targets.

    Field mapping (confirmed via live UAT inspection):
      wfTransferRequest.wfId            -> wf_transfer_id
      wfTransferRequest.departmentName  -> transfer_dept_name
      wfTransferRequest.rootOrgId       -> transfer_root_org_id
      wfTransferRequest.orgId           -> transfer_org_id
      wfTransferRequest.organisationId  -> transfer_organisation_id
      wfTransferRequest.orgName         -> transfer_org_name

    An empty {} wfTransferRequest means no transfer request has been raised.
    """
    try:
        url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
        headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
        payload = {"request": {"filters": {"email": email}}}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("response", {}).get("content", [])

        if not content:
            return json.dumps({"found": False, "message": USER_PROFILE_NOT_FOUND_MESSAGE,
                                "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})

        user = content[0]
        wf_transfer = user.get("wfTransferRequest") or {}
        has_transfer_request = bool(wf_transfer)

        return json.dumps({
            "email": USER_EMAIL_PLACEHOLDER,
            "found": True,
            "firstName": user.get("firstName"),
            "has_transfer_request": has_transfer_request,
            "wf_transfer_id": wf_transfer.get("wfId"),
            "transfer_dept_name": wf_transfer.get("departmentName"),
            "transfer_root_org_id": wf_transfer.get("rootOrgId"),
            "transfer_org_id": wf_transfer.get("orgId"),
            "transfer_organisation_id": wf_transfer.get("organisationId"),
            "transfer_org_name": wf_transfer.get("orgName"),
            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
        }, indent=2)
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_user_transfer_request_details error: {e}")
        return json.dumps({"found": False, "error": str(e),
                            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})


# ── SOP-A1 STEP 2 — MDO lookup for a SPECIFIC (target) organisation ────────
# get_mdo_details (login_issue_tool.py) always derives the org from the
# user's OWN profile — unusable here, since the whole point of this flow is
# that the user's own org is the "iGOT" placeholder, not the org their
# transfer request actually targets. This reuses the same MDO_ADMIN search
# logic, parameterized by org_id directly instead of deriving it from email.

@tool
def get_mdo_details_by_org_id(org_id: str) -> str:
    """Fetch MDO Admin contact details for a SPECIFIC organisation id.

    Used in SOP-A1 STEP 2 to find the MDO Admin for the organisation a
    transfer request actually targets (transfer_root_org_id from
    get_user_transfer_request_details) — not the user's own current
    organisation.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {
        "Authorization": f"Bearer {IGOT_KEY}",
        "Content-Type": CONTENT_TYPE_JSON,
    }
    try:
        admin, _matched_role, _ = find_mdo_contact(url, headers, org_id, timeout=10)
        if admin is None:
            return json.dumps({
                "org_id": org_id,
                "found": False,
                "message": f"No active MDO Admin found for organisation '{org_id}'.",
            })

        pd = admin.get("profileDetails", {})
        personal = pd.get("personalDetails", {})

        real_name = personal.get("firstname", "MDO Admin")
        real_email = personal.get("primaryEmail", "")
        real_mobile = str(personal.get("mobile", ""))

        spoc_replacements = {}
        if real_name:
            spoc_replacements[MDO_ADMIN_NAME_PLACEHOLDER] = real_name
        if real_email:
            spoc_replacements[MDO_ADMIN_EMAIL_PLACEHOLDER] = real_email
        if real_mobile:
            spoc_replacements["{{MDO_ADMIN_MOBILE}}"] = real_mobile

        return json.dumps({
            "org_id": org_id,
            "found": True,
            "rootOrgName": admin.get("rootOrgName", ""),
            "mdo_admin_name": MDO_ADMIN_NAME_PLACEHOLDER,
            "mdo_admin_email": MDO_ADMIN_EMAIL_PLACEHOLDER,
            "mdo_admin_mobile": "{{MDO_ADMIN_MOBILE}}",
            "_spoc_replacements": spoc_replacements,
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_mdo_details_by_org_id error: {e}")
        return json.dumps({"org_id": org_id, "found": False, "error": str(e)})


# ── SOP-A1 Edge Case 2 — organization search ─────────────────────────────────

@tool
def search_organization(org_name: str) -> str:
    """Search for an organization by its exact name via the Org Search API.

    Used in SOP-A1 Edge Case 2 to verify whether an organization the user
    named (but couldn't find in the Transfer Request dropdown) actually
    exists on the platform.

    Matching is EXACT (case-insensitive) — this API does not support partial/
    substring matching, so the org_name passed in must be the exact name as
    given by the user, not a fragment of it.
    """
    url = f"{IGOT_API_HOST_URL}/api/org/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    try:
        payload = {"request": {"filters": {"orgName": [org_name]}, "limit": 5}}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("response", {}).get("content", [])

        if not content:
            return json.dumps({"query": org_name, "found": False})

        org = content[0]
        return json.dumps({
            "query": org_name,
            "found": True,
            "org_id": org.get("id"),
            "org_name": org.get("orgName") or org.get("channel"),
            "ministry_or_state_type": org.get("ministryOrStateType"),
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] search_organization error: {e}")
        return json.dumps({"query": org_name, "found": False, "error": str(e)})


@tool
def search_organization_under_ministry_or_state(ministry_or_state_name: str, org_name: str) -> str:
    """Search for an organization under a named Ministry or State, when an exact
    org-name search (search_organization) already failed.

    Used in SOP-A1 Edge Case 2 as a second attempt, only when the user's ticket
    ALSO names a Ministry or State (not just the organization). Two-step lookup:
      1. Find the Ministry or State by name (partial/case-insensitive match)
         via the Ministry/State hierarchy list APIs.
      2. Search organizations under that Ministry/State (also partial/
         case-insensitive match on org_name) via the Org Hierarchy Search API.
    """
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    base = IGOT_API_HOST_URL
    try:
        parent_id = None
        parent_label = None
        for path in ("/api/org/hierarchy/ministry/search", "/api/org/hierarchy/state/search"):
            resp = requests.post(f"{base}{path}", json={"request": {}}, headers=headers, timeout=10)
            resp.raise_for_status()
            content = resp.json().get("result", {}).get("response", {}).get("content", [])
            match = next(
                (c for c in content if ministry_or_state_name.lower() in (c.get("channel") or "").lower()),
                None,
            )
            if match:
                parent_id = match.get("id")
                parent_label = match.get("channel")
                break

        if not parent_id:
            return json.dumps({
                "ministry_or_state_query": ministry_or_state_name,
                "parent_found": False,
                "found": False,
            })

        hierarchy_payload = {
            "request": {
                "filters": {"status": 1, "levelZeroOrgId": parent_id},
                "query": "",
                "limit": 50,
                "offset": 0,
                "fields": ["identifier", "orgName", "description", "channel"],
            }
        }
        h_resp = requests.post(f"{base}/api/org/hierarchy/search", json=hierarchy_payload,
                                headers=headers, timeout=10)
        h_resp.raise_for_status()
        orgs = h_resp.json().get("result", {}).get("response", {}).get("content", [])

        org_match = next(
            (o for o in orgs if org_name.lower() in (o.get("orgName") or "").lower()),
            None,
        )

        if not org_match:
            return json.dumps({
                "ministry_or_state_query": ministry_or_state_name,
                "parent_found": True,
                "parent_name": parent_label,
                "found": False,
            })

        return json.dumps({
            "ministry_or_state_query": ministry_or_state_name,
            "parent_found": True,
            "parent_name": parent_label,
            "found": True,
            "org_id": org_match.get("identifier"),
            "org_name": org_match.get("orgName"),
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] search_organization_under_ministry_or_state error: {e}")
        return json.dumps({
            "ministry_or_state_query": ministry_or_state_name,
            "found": False,
            "error": str(e),
        })


# ── SOP-A3 STEP 1 — wfProfileDesignationRequest / wfProfileGroupRequest check ──
# Per the UC-05 API Integration Guide (flows/mode_b_designation_not_verified.yaml
# and flows/mode_b_karmayogi_badge_check.yaml — identical API sequence for both):
# the private profile carries its own pending-request fields separate from
# wfTransferRequest, and a distinct profileGroupStatus alongside
# profileDesignationStatus.

@tool
def get_profile_verification_request_details(email: str) -> str:
    """Fetch the user's private profile plus any pending designation/group
    verification request, via the User Search API's wfProfileDesignationRequest
    and wfProfileGroupRequest fields.

    Used in SOP-A3 STEP 1 to determine whether the profile is already verified,
    and if not, whether a designation and/or group verification request is
    already pending (and which department it targets for approval).

    Field mapping (per UC-05 API Integration Guide):
      profileDetails.profileStatus                      -> profile_status
      profileDetails.profileDesignationStatus           -> designation_status
      profileDetails.profileGroupStatus                 -> group_status
      profileDetails.professionalDetails[0].designation -> designation
      profileDetails.professionalDetails[0].group       -> group
      profileDetails.professionalDetails[0].name        -> department_name
      channel                                           -> department_name fallback
      wfProfileDesignationRequest.wfId / .departmentName -> pending designation request
      wfProfileGroupRequest.wfId / .departmentName       -> pending group request

    An empty {} for either wf*Request field means no request of that type has
    been raised. When both are present, the designation request's department
    takes priority over the group request's for pending_department_name.
    """
    try:
        url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
        headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
        payload = {"request": {"filters": {"email": email}}}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("response", {}).get("content", [])

        if not content:
            return json.dumps({"found": False, "message": USER_PROFILE_NOT_FOUND_MESSAGE,
                                "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})

        user = content[0]
        profile_details = user.get("profileDetails") or {}
        prof_list = profile_details.get("professionalDetails")
        prof = prof_list[0] if isinstance(prof_list, list) and prof_list else {}

        wf_designation = user.get("wfProfileDesignationRequest") or {}
        wf_group = user.get("wfProfileGroupRequest") or {}
        has_designation_request = bool(wf_designation)
        has_group_request = bool(wf_group)

        pending_department_name = (
            wf_designation.get("departmentName") or wf_group.get("departmentName")
        )

        return json.dumps({
            "email": USER_EMAIL_PLACEHOLDER,
            "found": True,
            "firstName": user.get("firstName"),
            "profile_status": profile_details.get("profileStatus"),
            "designation_status": profile_details.get("profileDesignationStatus"),
            "group_status": profile_details.get("profileGroupStatus"),
            "designation": prof.get("designation"),
            "group": prof.get("group"),
            "department_name": prof.get("name") or user.get("channel"),
            "has_designation_request": has_designation_request,
            "has_group_request": has_group_request,
            "has_pending_request": has_designation_request or has_group_request,
            "pending_department_name": pending_department_name,
            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
        }, indent=2)
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_profile_verification_request_details error: {e}")
        return json.dumps({"found": False, "error": str(e),
                            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})


# ── SOP-A3 STEP 2 — MDO_ADMIN lookup for the pending request's TARGET department ──

@tool
def get_department_mdo_admin(department_name: str) -> str:
    """Fetch the MDO point of contact for a specific department/organisation
    channel name — used once a designation/group verification request is
    confirmed pending, to find who can approve it.

    Filters by channel=<department_name>, preferring an active MDO_LEADER and
    falling back to MDO_ADMIN if none is found — same two-call, single-role
    precedence as get_mdo_details/get_mdo_details_by_org_id, just keyed by
    channel instead of org_id.

    Used in SOP-A3 STEP 2.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    try:
        admin, _matched_role, _ = find_mdo_contact_by_channel(
            url, headers, department_name, timeout=10
        )

        if admin is None:
            return json.dumps({
                "department_name": department_name,
                "found": False,
                "message": f"No active MDO_LEADER or MDO_ADMIN found for department '{department_name}'.",
            })

        pd = admin.get("profileDetails", {})
        personal = pd.get("personalDetails", {})

        first_name = personal.get("firstname") or admin.get("firstName") or "MDO Admin"
        surname = personal.get("surname") or ""
        real_email = personal.get("primaryEmail") or admin.get("email") or ""
        real_name = f"{first_name} {surname}".strip()

        spoc_replacements = {}
        if real_name:
            spoc_replacements[MDO_ADMIN_NAME_PLACEHOLDER] = real_name
        if real_email:
            spoc_replacements[MDO_ADMIN_EMAIL_PLACEHOLDER] = real_email

        return json.dumps({
            "department_name": department_name,
            "found": True,
            "admin_name": MDO_ADMIN_NAME_PLACEHOLDER,
            "admin_email": MDO_ADMIN_EMAIL_PLACEHOLDER,
            "_spoc_replacements": spoc_replacements,
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_department_mdo_admin error: {e}")
        return json.dumps({"department_name": department_name, "found": False, "error": str(e)})


# ── SOP-P3 STEP 1 — verify the designation against master data ─────────────

_DESIGNATION_CACHE: dict = {"data": None, "fetched_at": None}
_DESIGNATION_CACHE_TTL_SECONDS = 3600 * 24  # 1 day — designation master data changes
                                             # rarely (weeks/months), so the
                                             # slow full paginated fetch only
                                             # needs to happen once a day.


def _fetch_all_designations() -> list[dict]:
    """Fetch (or return cached) full active-designation master list.

    Response shape (confirmed via live curl against the real endpoint):
      {"result": {"result": {"data": [...], "totalCount": N}}} — note the
      DOUBLE-nested "result" key, easy to miss.
    """
    import time

    now = time.time()
    if (
        _DESIGNATION_CACHE["data"] is not None
        and (now - _DESIGNATION_CACHE["fetched_at"]) < _DESIGNATION_CACHE_TTL_SECONDS
    ):
        return _DESIGNATION_CACHE["data"]

    url = f"{IGOT_API_HOST_URL}/apis/public/v8/designation/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    all_designations: list[dict] = []
    page_size = 100  # confirmed API max — pageSize > 100 returns HTTP 400
    page_number = 1
    total_count = None
    max_retries = 3  # production has ~200+ pages to fetch; one slow/timed-out
                      # or malformed page shouldn't fail the entire list —
                      # retry that single page a few times first.

    while total_count is None or len(all_designations) < total_count:
        payload = {
            "pageNumber": page_number,
            "pageSize": page_size,
            "filterCriteriaMap": {"status": "Active"},
            "requestedFields": ["id", "designation"],
        }
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=15)
                resp.raise_for_status()
                inner = resp.json().get("result", {}).get("result")
                if inner is None:
                    # HTTP 200 but a null body — this is the production 10k-wall
                    # signature (or a transient hiccup below that point). Retrying
                    # won't get past the wall, but it's cheap insurance against a
                    # genuine transient blip, and either way we stop cleanly once
                    # retries are exhausted rather than crashing the whole fetch.
                    raise ValueError("designation search returned an empty/null result body")
                break
            except (requests.exceptions.RequestException, ValueError) as e:
                if attempt == max_retries:
                    logger.warning(
                        f"[profile_user_management_tools] designation page {page_number} "
                        f"failed after {max_retries} attempts ({e}) — stopping with "
                        f"{len(all_designations)} designations fetched so far."
                    )
                    total_count = len(all_designations)
                    inner = None
                    break
                logger.warning(
                    f"[profile_user_management_tools] designation page {page_number} "
                    f"attempt {attempt} failed ({e}), retrying..."
                )
        if inner is None:
            break
        page_data = inner.get("data", [])
        total_count = inner.get("totalCount", len(page_data))
        if not page_data:
            break
        all_designations.extend(page_data)
        page_number += 1

    _DESIGNATION_CACHE["data"] = all_designations
    _DESIGNATION_CACHE["fetched_at"] = now
    logger.info(f"[profile_user_management_tools] Cached {len(all_designations)} active designations.")
    return all_designations


@tool
def search_designation(designation_name: str) -> str:
    """Verify a user-reported designation against the platform's full active
    designation master data (cached in-memory for 1 day — this API has no
    server-side name search, so it fetches everything, paginated, once per
    cache window, and matches locally).

    Used in SOP-P3 STEP 1. Matching is done here, not left to the caller: a
    literal (case-insensitive, whitespace-normalized) match is exact; failing
    that, word-PREFIX matching (e.g. "sec" matches "Section" but never
    matches merely appearing inside "Secretary") finds plausible candidates —
    a single candidate is treated as the confident match too, and 2+ is
    ambiguous, never a loose substring guess.

    Returns match_type: "exact" (a literal match, or exactly one plausible
    word-prefix candidate), "ambiguous" (2+ plausible candidates, the user
    must confirm which), or "not_found" (also returned, safely, if the
    designation falls past this endpoint's ~10,000 record production limit —
    SOP-P3 escalates "not_found" to a human rather than ever telling the user
    their designation doesn't exist).
    """
    try:
        all_designations = _fetch_all_designations()
    except Exception as e:
        logger.error(f"[profile_user_management_tools] search_designation error: {e}")
        return json.dumps({"query": designation_name, "match_type": "error", "error": str(e)})

    query_norm = " ".join(designation_name.strip().lower().split())
    query_words = set(query_norm.split())

    exact = [
        d for d in all_designations
        if " ".join((d.get("designation") or "").strip().lower().split()) == query_norm
    ]
    if exact:
        d = exact[0]
        return json.dumps({
            "query": designation_name,
            "match_type": "exact",
            "designation": {"id": d.get("id"), "designation": d.get("designation")},
        })

    # Word-PREFIX matching, never mid-word substring: every query word must be
    # a prefix of some whole word in the candidate name (e.g. "sec" is a
    # prefix of "section", catching that as a plausible candidate) — but "sec"
    # must NOT match because it merely appears somewhere inside "Secretary"
    # if it isn't at the start of a word. This is what actually catches
    # abbreviation-style ambiguity (the "sec officer" case) instead of
    # missing it as not_found the way pure whole-word equality would.
    candidates = []
    for d in all_designations:
        name = d.get("designation") or ""
        name_words = list(name.strip().lower().split())
        if query_words and all(
            any(w.startswith(qw) for w in name_words) for qw in query_words
        ):
            candidates.append({"id": d.get("id"), "designation": name})

    if len(candidates) == 1:
        return json.dumps({"query": designation_name, "match_type": "exact", "designation": candidates[0]})
    if candidates:
        # Deliberately NOT including the candidate names here — the caller
        # must never act on or surface any one of them (STEP 1 forbids
        # guessing), so there's nothing for it to do with the actual names.
        return json.dumps({"query": designation_name, "match_type": "ambiguous", "candidate_count": len(candidates)})
    return json.dumps({"query": designation_name, "match_type": "not_found"})


# ── SOP-P3 STEP 2 — the user's own org, and whether it has imported the ────
# confirmed designation (two separate APIs, matching the given SOP exactly)

@tool
def get_user_root_org_id(email: str) -> str:
    """Fetch the user's own rootOrgId via the User Read API.

    Used in SOP-P3 STEP 2, ONLY after a specific designation has already been
    confirmed in STEP 1 — resolves email -> user_id via the standard search,
    then reads the full user profile for rootOrgId.
    """
    user_id, user_data, error = _fetch_user_id_and_profile_or_error(email)
    if error:
        return error

    root_org_id = user_data.get("rootOrgId")
    if not root_org_id:
        return json.dumps({"found": False, "message": "rootOrgId not available for this user."})

    return json.dumps({"found": True, "user_id": user_id, "root_org_id": root_org_id})


@tool
def get_org_imported_designations(root_org_id: str) -> str:
    """Fetch the list of designations a specific organisation's MDO has
    imported, via the Org Framework Read API.

    Used in SOP-P3 STEP 2 to check whether the CONFIRMED designation from
    STEP 1 is actually usable by this user's own org yet — existing in the
    platform's master data is not enough, the org must separately import it.

    Response shape (confirmed via live curl against the real endpoint): the
    imported-designations list is NOT a top-level "designation" category (one
    literally coded "designation" exists but is unrelated — its terms have
    zero designation-type associations). It lives inside the "org" category's
    single term (the org itself), under that term's `associations` array,
    filtered to entries with refType == "designation".
    """
    url = f"{IGOT_API_HOST_URL}/api/framework/v1/read/{root_org_id}_odcs"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        framework = resp.json().get("result", {}).get("framework", {})
        categories = framework.get("categories", [])

        org_category = next((c for c in categories if c.get("code") == "org"), None)
        if not org_category or not org_category.get("terms"):
            return json.dumps({"root_org_id": root_org_id, "found": False,
                                "message": "Org framework/category not found."})

        imported = []
        for org_term in org_category["terms"]:
            for assoc in (org_term.get("associations") or []):
                if assoc.get("refType") == "designation" and assoc.get("name"):
                    imported.append(assoc["name"])

        return json.dumps({
            "root_org_id": root_org_id,
            "found": True,
            "imported_designations": imported,
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_org_imported_designations error: {e}")
        return json.dumps({"root_org_id": root_org_id, "found": False, "error": str(e)})
# ── SOP-A2 STEP 2 — domain check for the NEW contact (not the ticket owner) ──
# Deliberately NOT named `validate_email_domain(email=...)` / reused from
# login_issue_tool — that param name would be hijacked by execute_node's
# secure email-injection (see module docstring). This checks the domain of
# the NEW email the user wants to update to, which is frequently NOT the
# ticket owner's own (already-whitelisted) address.

@tool
def validate_new_contact_domain(new_email: str) -> str:
    """Check if the domain of a NEW email address is whitelisted on the iGOT
    platform.

    Used in SOP-A2 STEP 2 — only for email updates. Mobile number updates have
    no domain to validate; skip this tool for those.
    """
    domain = new_email.split("@")[-1].strip().lower()
    url = f"{IGOT_API_HOST_URL}/api/user/v1/email/approvedDomains"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        domains = resp.json().get("result", {}).get("domains", [])
        whitelisted = [d.strip().lower() for d in domains if isinstance(d, str)]
        return json.dumps({
            "is_whitelisted": domain in whitelisted,
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] validate_new_contact_domain error: {e}")
        # Do NOT default is_whitelisted to False here — that would be indistinguishable
        # from a genuine "domain not approved" result and cause SOP-A2 STEP 2 to tell the
        # user, confidently and incorrectly, that a real domain isn't registered.
        return json.dumps({"is_whitelisted": False, "lookup_failed": True, "error": str(e)})


# ── SOP-A2 STEP 3 — duplicate-registration check for the NEW contact ────────

@tool
def check_contact_registered(new_contact: str) -> str:
    """Check whether the NEW Email ID or Mobile Number the user wants to update
    to is already registered to another account on the iGOT platform.

    Used in SOP-A2 STEP 3. Auto-detects whether `new_contact` is an email
    (contains '@') or a mobile number (digits) and filters the User Search API
    accordingly:
      - email  -> filters: {"email": new_contact}
      - mobile -> filters: {"phone": <last 10 digits of new_contact>}
        (confirmed field name: private User Search API filters on plain
        10-digit "phone", no country code)

    Returns is_registered, plus (if found) the matched account's user_id and
    organisation — needed for get_enrollment_summary. These matched-account
    details are for the internal escalation note only; never surface the
    other account's identity to the end user directly.
    """
    contact = new_contact.strip()
    is_email = "@" in contact
    if is_email:
        filter_key, filter_value = "email", contact
    else:
        filter_key, filter_value = "phone", "".join(ch for ch in contact if ch.isdigit())[-10:]

    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    try:
        payload = {"request": {"filters": {filter_key: filter_value}}}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("response", {}).get("content", [])

        if not content:
            return json.dumps({
                "contact_type": "email" if is_email else "mobile",
                "is_registered": False,
            })

        user = content[0]
        return json.dumps({
            "contact_type": "email" if is_email else "mobile",
            "is_registered": True,
            "matched_user_id": user.get("id"),
            "matched_rootOrgId": user.get("rootOrgId"),
            "matched_rootOrgName": user.get("rootOrgName"),
            "matched_status": user.get("status"),
            # Stripped by the subgraph; notify_user swaps the token for the real value
            # so the LLM never has to see or echo the PII-masked contact.
            "_spoc_replacements": {"{{NEW_CONTACT}}": contact},
        }, indent=2)
    except Exception as e:
        logger.error(f"[profile_user_management_tools] check_contact_registered error: {e}")
        # Do NOT default is_registered to False here — that would be indistinguishable
        # from a genuine "not registered" result and cause SOP-A2 STEP 3 to tell the user,
        # confidently and incorrectly, that an already-taken contact is available.
        return json.dumps({
            "contact_type": "email" if is_email else "mobile",
            "is_registered": False,
            "lookup_failed": True,
            "error": str(e),
        })


# ── SOP-A2 STEP 4.1 — enrollment summary for the ALREADY-REGISTERED account ──

def _fetch_enrollment_count(user_id: str, status: list) -> int:
    """Internal helper: count enrollments for user_id matching the given status list."""
    url = f"{IGOT_API_HOST_URL}/api/course/private/v4/user/enrollment/list/{user_id}"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    resp = requests.post(url, headers=headers, json={"request": {"status": status}}, timeout=10)
    resp.raise_for_status()
    return len(resp.json().get("result", {}).get("courses", []))


@tool
def get_enrollment_summary(user_id: str) -> str:
    """Fetch enrollment counts for the account already associated with the new
    contact, identified by matched_user_id from check_contact_registered.

    Used in SOP-A2 STEP 4.1, strictly for the internal escalation note handed
    to the human agent — never quote these counts back to the end user.

    enrolled_count is the sum of in_progress_count + completed_count (courses
    in other states, if any exist on the platform, are not included).
    """
    try:
        in_progress = _fetch_enrollment_count(user_id, ["In-Progress"])
        completed = _fetch_enrollment_count(user_id, ["Completed"])
        return json.dumps({
            "user_id": user_id,
            "in_progress_count": in_progress,
            "completed_count": completed,
            "enrolled_count": in_progress + completed,
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_enrollment_summary error: {e}")
        return json.dumps({"user_id": user_id, "error": str(e)})


# ── SOP-P5 — Mother Tongue Update ────────────────────────────────────────────

@tool
def check_mother_tongue_available(mother_tongue_name: str) -> str:
    """Check whether a user-reported mother tongue exists in the platform's
    master language list.

    Used in SOP-P5 STEP 1. Matching is case-insensitive exact match — language
    names are unambiguous (unlike designations), so no word-boundary/candidate
    handling is needed here.
    """
    url = f"{IGOT_API_HOST_URL}/api/masterData/v1/languages"
    headers = {"Authorization": f"Bearer {IGOT_KEY}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        languages = resp.json().get("languages", []) or []
        query_norm = mother_tongue_name.strip().lower()
        match = next(
            (lang.get("name") for lang in languages if (lang.get("name") or "").strip().lower() == query_norm),
            None,
        )
        return json.dumps({
            "query": mother_tongue_name,
            "found": match is not None,
            "matched_name": match,
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] check_mother_tongue_available error: {e}")
        return json.dumps({"query": mother_tongue_name, "found": False, "error": str(e)})


# ── Shared helper — SOP-P7 / SOP-P12 own-profile lookup by email ────────────
# Both SOPs below need nothing but "look this user up by email, and if
# anything goes wrong return a ready-to-use error response" before doing
# their own, unrelated field extraction — factored out to avoid duplicating
# that fetch/error-handling shape between them.

def _fetch_own_profile_or_error(email: str) -> tuple[dict | None, str | None]:
    """Look up a user by email via the User Search API.

    Returns (user_dict, None) on success, or (None, json_error_string) if the
    user wasn't found or the API call failed — callers return that error
    string directly.
    """
    url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    try:
        payload = {"request": {"filters": {"email": email}}}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("response", {}).get("content", [])
        if not content:
            return None, json.dumps({"found": False, "message": USER_PROFILE_NOT_FOUND_MESSAGE,
                                      "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})
        return content[0], None
    except Exception as e:
        logger.error(f"[profile_user_management_tools] _fetch_own_profile_or_error error: {e}")
        return None, json.dumps({"found": False, "error": str(e),
                                  "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email}})


# ── Shared helper — SOP-P3 / SOP-P12 own-profile lookup via Read API ───────
# Both SOPs need fields the Search API doesn't expose (rootOrgId reliably;
# profileCompletionPercentage / verifiedKarmayogi not at all) — so both
# resolve email -> user_id via Search, then fetch the full profile via Read.
# Factored out to avoid duplicating that two-call shape between them.

def _fetch_user_id_and_profile_or_error(email: str) -> tuple[str | None, dict | None, str | None]:
    """Resolve email -> user_id via the User Search API, then fetch the full
    profile via the User Read API.

    Returns (user_id, profile_dict, None) on success, or (None, None,
    json_error_string) if the user/id wasn't found or an API call failed —
    callers return that error string directly.
    """
    search_url = f"{IGOT_API_HOST_URL}/api/private/user/v1/search"
    headers = {"Authorization": f"Bearer {IGOT_KEY}", "Content-Type": CONTENT_TYPE_JSON}
    try:
        search_payload = {"request": {"filters": {"email": email}}}
        search_resp = requests.post(search_url, json=search_payload, headers=headers, timeout=10)
        search_resp.raise_for_status()
        content = search_resp.json().get("result", {}).get("response", {}).get("content", [])
        if not content:
            return None, None, json.dumps({"found": False, "message": USER_PROFILE_NOT_FOUND_MESSAGE})

        user_id = content[0].get("id")
        if not user_id:
            return None, None, json.dumps({"found": False, "message": "User id not available in profile."})

        read_url = f"{IGOT_API_HOST_URL}/api/user/private/v1/read/{user_id}"
        read_resp = requests.get(read_url, headers=headers, timeout=10)
        read_resp.raise_for_status()
        user_data = read_resp.json().get("result", {}).get("response", {})
        return user_id, user_data, None
    except Exception as e:
        logger.error(f"[profile_user_management_tools] _fetch_user_id_and_profile_or_error error: {e}")
        return None, None, json.dumps({"found": False, "error": str(e)})


# ── SOP-P7 — Date of Retirement Update ───────────────────────────────────────

@tool
def get_user_ehrms_details(email: str) -> str:
    """Check whether the user's EHRMS ID / External System ID is set on their
    profile.

    Used in SOP-P7 STEP 1 — Date of Retirement is auto-fetched from the EHRMS
    portal, but only once the EHRMS ID sync is set up; if it isn't, the user
    needs their MDO to set it first.

    Field mapping (confirmed via live UAT inspection):
      EHRMS ID             -> profileDetails.additionalProperties.externalSystemId
      External System Name -> profileDetails.additionalProperties.externalSystem
    """
    user, error = _fetch_own_profile_or_error(email)
    if error:
        return error

    additional_properties = (user.get("profileDetails") or {}).get("additionalProperties") or {}
    external_system_id = additional_properties.get("externalSystemId")
    external_system_name = additional_properties.get("externalSystem")

    return json.dumps({
        "email": USER_EMAIL_PLACEHOLDER,
        "found": True,
        "ehrms_id_set": bool(external_system_id),
        "external_system_id": external_system_id,
        "external_system_name": external_system_name,
        "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
    })


# ── SOP-P12 — Profile Completion Not Showing 100% ───────────────────────────

@tool
def get_profile_completion_details(email: str) -> str:
    """Check which mandatory profile fields are set, for a user reporting their
    profile completion isn't showing 100%.

    Used in SOP-P12 STEP 1. Two-step lookup: search by email to get user_id,
    then read the full profile via the User Read API.

    Field mapping (from GET /api/user/private/v1/read/{user_id}):
      profile_completion_percentage -> profileCompletionPercentage
      Profile Photo                 -> profileDetails.profileImageUrl (present = set)
      Cover Photo                   -> profileDetails.profileBannerUrl (present = set)
      Profile Verification          -> profileDetails.verifiedKarmayogi ("False" or falsy = not verified)
      About Me                      -> profileDetails.employmentDetails.aboutme (present = set)
      Designation                   -> profileDetails.profileDesignationStatus ("VERIFIED" = done)
      Group                         -> profileDetails.profileGroupStatus ("VERIFIED" = done)
    """
    try:
        _user_id, user_data, error = _fetch_user_id_and_profile_or_error(email)
        if error:
            return error

        profile_details = user_data.get("profileDetails") or {}
        employment = profile_details.get("employmentDetails") or {}
        verified_karmayogi = profile_details.get("verifiedKarmayogi")

        return json.dumps({
            "email": USER_EMAIL_PLACEHOLDER,
            "found": True,
            "firstName": user_data.get("firstName"),
            "profile_completion_percentage": user_data.get("profileCompletionPercentage"),
            "profile_photo_set": bool(profile_details.get("profileImageUrl")),
            "cover_photo_set": bool(profile_details.get("profileBannerUrl")),
            "profile_verification_verified": bool(
                verified_karmayogi and str(verified_karmayogi).lower() != "false"
            ),
            "about_me_set": bool(employment.get("aboutme")),
            "designation_verified": profile_details.get("profileDesignationStatus") == "VERIFIED",
            "group_verified": profile_details.get("profileGroupStatus") == "VERIFIED",
            "_spoc_replacements": {USER_EMAIL_PLACEHOLDER: email},
        })
    except Exception as e:
        logger.error(f"[profile_user_management_tools] get_profile_completion_details error: {e}")
        return json.dumps({"found": False, "error": str(e)})


# ── Convenience list for the subgraph ─────────────────────────────────────────

def get_profile_user_management_tools() -> list:
    """Return all tools for the ProfileUserManagementSubgraph."""
    from app.core.tools.login_issue_tool import get_mdo_details, get_yp_am_details
    from app.core.tools.profile_update_tool import get_user_profile as get_own_profile_details

    return [
        get_user_transfer_request_details,  # SOP-A1 STEP 1
        get_mdo_details_by_org_id,          # SOP-A1 STEP 2, SOP-P6/P7/P8
        search_organization,                # SOP-A1 Edge Case 2 (exact name), SOP-P8 STEP 2
        search_organization_under_ministry_or_state,  # SOP-A1 Edge Case 2 (Ministry/State + org)
        get_yp_am_details,                  # SOP-A1 STEP 3 / Edge Case 2 YP/SPOC fallback, SOP-P3 Case 2, SOP-A2 fallback, SOP-P7 fallback, SOP-A3 YP Fallback
        search_designation,                 # SOP-P3 STEP 1
        get_user_root_org_id,               # SOP-P3 STEP 2, SOP-P6/P7
        get_org_imported_designations,      # SOP-P3 STEP 2
        get_own_profile_details,            # SOP-A2 STEP 2A/3.1.1/3.2 — ticket owner's own org name / user id; SOP-P8 STEP 1
        get_mdo_details,                    # SOP-A2 STEP 2A/3.1.1 — MDO for the ticket owner's own org
        validate_new_contact_domain,        # SOP-A2 STEP 2
        check_contact_registered,           # SOP-A2 STEP 3
        get_enrollment_summary,             # SOP-A2 STEP 4.1
        check_mother_tongue_available,      # SOP-P5 STEP 1
        get_user_ehrms_details,             # SOP-P7 STEP 1
        get_profile_completion_details,     # SOP-P12 STEP 1
        get_profile_verification_request_details,  # SOP-A3 STEP 1
        get_department_mdo_admin,           # SOP-A3 STEP 2
    ]