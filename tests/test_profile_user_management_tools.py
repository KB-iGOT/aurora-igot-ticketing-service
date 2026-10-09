"""
Unit tests for the tools in app/core/tools/profile_user_management_tools.py
that are NOT already covered by test_profile_verification_tools.py (SOP-A3):

  SOP-A1  get_user_transfer_request_details, get_mdo_details_by_org_id,
          search_organization, search_organization_under_ministry_or_state
  SOP-P3  search_designation, get_user_root_org_id,
          get_org_imported_designations
  SOP-A2  validate_new_contact_domain, check_contact_registered,
          get_enrollment_summary

Plus the get_profile_user_management_tools() convenience list.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core.tools import profile_user_management_tools as put
from app.core.tools.profile_user_management_tools import (
    check_contact_registered,
    check_mother_tongue_available,
    get_enrollment_summary,
    get_mdo_details_by_org_id,
    get_org_imported_designations,
    get_profile_completion_details,
    get_profile_user_management_tools,
    get_user_ehrms_details,
    get_user_root_org_id,
    get_user_transfer_request_details,
    search_designation,
    search_organization,
    search_organization_under_ministry_or_state,
    validate_new_contact_domain,
)
from app.core.utils.prompt_templates import PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT


def _mock_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def _search_response(content):
    return _mock_response({"result": {"response": {"content": content}}})


# ── SOP-A1 STEP 1 — get_user_transfer_request_details ──────────────────────

class TestGetUserTransferRequestDetails:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_transfer_request_details.func("nobody@x.com"))

        assert result["found"] is False
        assert result["message"] == put.USER_PROFILE_NOT_FOUND_MESSAGE

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_has_transfer_request(self, mock_post):
        user = {
            "firstName": "Asha",
            "wfTransferRequest": {
                "wfId": "wf-1",
                "departmentName": "Target Dept",
                "rootOrgId": "root-1",
                "orgId": "org-1",
                "organisationId": "orgn-1",
                "orgName": "Target Org",
            },
        }
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_transfer_request_details.func("asha@x.com"))

        assert result["found"] is True
        assert result["has_transfer_request"] is True
        assert result["wf_transfer_id"] == "wf-1"
        assert result["transfer_root_org_id"] == "root-1"
        assert result["transfer_org_name"] == "Target Org"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_no_transfer_request(self, mock_post):
        user = {"firstName": "Ravi", "wfTransferRequest": {}}
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_transfer_request_details.func("ravi@x.com"))

        assert result["has_transfer_request"] is False
        assert result["wf_transfer_id"] is None

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_request_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_transfer_request_details.func("err@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── SOP-A1 STEP 2 — get_mdo_details_by_org_id ───────────────────────────────

class TestGetMdoDetailsByOrgId:
    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_found(self, mock_post):
        admin = {
            "organisations": [{"roles": ["MDO_LEADER"]}],
            "rootOrgName": "Target Org",
            "profileDetails": {
                "personalDetails": {
                    "firstname": "Lead",
                    "primaryEmail": "lead@x.com",
                    "mobile": "9876543210",
                }
            },
        }
        mock_post.return_value = _search_response([admin])

        result = json.loads(get_mdo_details_by_org_id.func("org-1"))

        assert result["found"] is True
        assert result["rootOrgName"] == "Target Org"
        assert result["_spoc_replacements"]["{{MDO_ADMIN_NAME}}"] == "Lead"
        assert result["_spoc_replacements"]["{{MDO_ADMIN_EMAIL}}"] == "lead@x.com"
        assert result["_spoc_replacements"]["{{MDO_ADMIN_MOBILE}}"] == "9876543210"

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_not_found(self, mock_post):
        mock_post.side_effect = [_search_response([]), _search_response([])]

        result = json.loads(get_mdo_details_by_org_id.func("org-empty"))

        assert result["found"] is False
        assert "org-empty" in result["message"]

    @patch("app.core.utils.mdo_lookup.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("boom")

        result = json.loads(get_mdo_details_by_org_id.func("org-1"))

        assert result["found"] is False
        assert "error" in result


# ── SOP-A1 Edge Case 2 — search_organization ────────────────────────────────

class TestSearchOrganization:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_found(self, mock_post):
        org = {"id": "org-1", "orgName": "Ministry of Health", "ministryOrStateType": "ministry"}
        mock_post.return_value = _search_response([org])

        result = json.loads(search_organization.func("Ministry of Health"))

        assert result["found"] is True
        assert result["org_id"] == "org-1"
        assert result["org_name"] == "Ministry of Health"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(search_organization.func("Unknown Org"))

        assert result["found"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("network error")

        result = json.loads(search_organization.func("Any Org"))

        assert result["found"] is False
        assert "error" in result


# ── SOP-A1 Edge Case 2 — search_organization_under_ministry_or_state ───────

class TestSearchOrganizationUnderMinistryOrState:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_ministry_and_org_found(self, mock_post):
        ministry_resp = _search_response([{"id": "min-1", "channel": "Ministry of Health"}])
        state_resp = _search_response([])
        hierarchy_resp = _search_response([{"identifier": "org-1", "orgName": "Sub Department"}])
        mock_post.side_effect = [ministry_resp, hierarchy_resp]

        result = json.loads(
            search_organization_under_ministry_or_state.func("Health", "Department")
        )

        assert result["parent_found"] is True
        assert result["parent_name"] == "Ministry of Health"
        assert result["found"] is True
        assert result["org_id"] == "org-1"
        assert result["org_name"] == "Sub Department"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_parent_not_found(self, mock_post):
        mock_post.side_effect = [_search_response([]), _search_response([])]

        result = json.loads(
            search_organization_under_ministry_or_state.func("Nonexistent", "Org")
        )

        assert result["parent_found"] is False
        assert result["found"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_parent_found_org_not_found(self, mock_post):
        ministry_resp = _search_response([{"id": "min-1", "channel": "Ministry of Health"}])
        hierarchy_resp = _search_response([{"identifier": "org-1", "orgName": "Other Dept"}])
        mock_post.side_effect = [ministry_resp, hierarchy_resp]

        result = json.loads(
            search_organization_under_ministry_or_state.func("Health", "Missing Dept")
        )

        assert result["parent_found"] is True
        assert result["found"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("boom")

        result = json.loads(
            search_organization_under_ministry_or_state.func("Health", "Dept")
        )

        assert result["found"] is False
        assert "error" in result


# ── SOP-P3 STEP 1 — search_designation ──────────────────────────────────────

def _designation_page_response(data, total_count):
    return _mock_response({"result": {"result": {"data": data, "totalCount": total_count}}})


@pytest.fixture(autouse=True)
def _reset_designation_cache():
    """search_designation caches results in a module-level dict; reset it
    around every test in this module so tests don't leak state into each
    other (or into other test files importing the same module)."""
    put._DESIGNATION_CACHE["data"] = None
    put._DESIGNATION_CACHE["fetched_at"] = None
    yield
    put._DESIGNATION_CACHE["data"] = None
    put._DESIGNATION_CACHE["fetched_at"] = None


class TestSearchDesignation:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_exact_match(self, mock_post):
        data = [{"id": "d1", "designation": "Section Officer"}, {"id": "d2", "designation": "Under Secretary"}]
        mock_post.return_value = _designation_page_response(data, len(data))

        result = json.loads(search_designation.func("section officer"))

        assert result["match_type"] == "exact"
        assert result["designation"]["id"] == "d1"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_ambiguous_prefix_match(self, mock_post):
        data = [
            {"id": "d1", "designation": "Section Officer"},
            {"id": "d2", "designation": "Secretary Officer"},
        ]
        mock_post.return_value = _designation_page_response(data, len(data))

        result = json.loads(search_designation.func("sec officer"))

        assert result["match_type"] == "ambiguous"
        assert result["candidate_count"] == 2
        assert "designation" not in result

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_not_found(self, mock_post):
        data = [{"id": "d1", "designation": "Section Officer"}]
        mock_post.return_value = _designation_page_response(data, len(data))

        result = json.loads(search_designation.func("Totally Different Title"))

        assert result["match_type"] == "not_found"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_single_prefix_candidate_treated_as_exact(self, mock_post):
        data = [{"id": "d1", "designation": "Section Officer"}]
        mock_post.return_value = _designation_page_response(data, len(data))

        result = json.loads(search_designation.func("sec"))

        assert result["match_type"] == "exact"
        assert result["designation"]["id"] == "d1"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_pagination_is_followed_until_total_count(self, mock_post):
        page1 = [{"id": f"d{i}", "designation": f"Designation {i}"} for i in range(100)]
        page2 = [{"id": "d100", "designation": "Special Officer"}]
        mock_post.side_effect = [
            _designation_page_response(page1, 101),
            _designation_page_response(page2, 101),
        ]

        result = json.loads(search_designation.func("special officer"))

        assert mock_post.call_count == 2
        assert result["match_type"] == "exact"
        assert result["designation"]["id"] == "d100"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_results_are_cached_across_calls(self, mock_post):
        data = [{"id": "d1", "designation": "Section Officer"}]
        mock_post.return_value = _designation_page_response(data, len(data))

        search_designation.func("Section Officer")
        search_designation.func("Section Officer")

        assert mock_post.call_count == 1

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_fetch_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("designation api down")

        result = json.loads(search_designation.func("Section Officer"))

        assert result["match_type"] == "error"
        assert "error" in result


# ── SOP-P3 STEP 1 — _fetch_all_designations retry/TTL hardening ────────────
# Added for the production ~20,743-designation scale, where a single page can
# intermittently time out or come back with a null body (the classic
# max_result_window signature past record #10,000).

class TestFetchAllDesignationsRetryAndCaching:
    def test_ttl_is_one_day(self):
        assert put._DESIGNATION_CACHE_TTL_SECONDS == 3600 * 24

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_page_retries_after_timeout_then_succeeds(self, mock_post):
        data = [{"id": "d1", "designation": "Section Officer"}]
        mock_post.side_effect = [
            requests.exceptions.Timeout("read timed out"),
            _designation_page_response(data, len(data)),
        ]

        result = json.loads(search_designation.func("Section Officer"))

        assert result["match_type"] == "exact"
        assert result["designation"]["id"] == "d1"
        assert mock_post.call_count == 2

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_null_body_page_is_retried_then_succeeds(self, mock_post):
        data = [{"id": "d1", "designation": "Section Officer"}]
        mock_post.side_effect = [
            _mock_response({"result": {"result": None}}),  # 200 OK, empty body — the
                                                             # production 10k-wall signature
            _designation_page_response(data, len(data)),
        ]

        result = json.loads(search_designation.func("Section Officer"))

        assert result["match_type"] == "exact"
        assert mock_post.call_count == 2

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_page_stops_gracefully_after_exhausting_retries(self, mock_post):
        # Page 1 succeeds with 100 of 150 total; page 2 fails all 3 attempts —
        # the fetch must stop cleanly with page 1's data rather than crash the
        # whole tool call, since production has ~200+ pages and one bad page
        # shouldn't take down the entire designation check.
        page1 = [{"id": f"d{i}", "designation": f"Designation {i}"} for i in range(100)]
        mock_post.side_effect = [
            _designation_page_response(page1, 150),
            requests.exceptions.Timeout("read timed out"),
            requests.exceptions.Timeout("read timed out"),
            requests.exceptions.Timeout("read timed out"),
        ]

        result = json.loads(search_designation.func("Designation 5"))

        assert result["match_type"] == "exact"
        assert result["designation"]["id"] == "d5"
        assert mock_post.call_count == 4  # 1 (page 1) + 3 (page 2 retries)

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_first_page_exhausting_retries_yields_not_found_not_error(self, mock_post):
        # A total fetch failure on the very first page currently surfaces as
        # an empty designation list — search_designation still resolves,
        # match_type="not_found" (which SOP-P3 safely escalates to a human),
        # not a raised exception.
        mock_post.side_effect = [requests.exceptions.Timeout("read timed out")] * 3

        result = json.loads(search_designation.func("Section Officer"))

        assert result["match_type"] == "not_found"
        assert mock_post.call_count == 3


# ── SOP-P3 STEP 2 — get_user_root_org_id ────────────────────────────────────

class TestGetUserRootOrgId:
    @patch("app.core.tools.profile_user_management_tools.requests.get")
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_success(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({"result": {"response": {"rootOrgId": "root-1"}}})

        result = json.loads(get_user_root_org_id.func("user@x.com"))

        assert result["found"] is True
        assert result["user_id"] == "user-1"
        assert result["root_org_id"] == "root-1"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_root_org_id.func("nobody@x.com"))

        assert result["found"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_user_id_missing(self, mock_post):
        mock_post.return_value = _search_response([{}])

        result = json.loads(get_user_root_org_id.func("noid@x.com"))

        assert result["found"] is False
        assert "User id" in result["message"]

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_root_org_id_missing(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.return_value = _mock_response({"result": {"response": {}}})

        result = json.loads(get_user_root_org_id.func("user@x.com"))

        assert result["found"] is False
        assert "rootOrgId" in result["message"]

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_root_org_id.func("err@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── SOP-P3 STEP 2 — get_org_imported_designations ───────────────────────────

class TestGetOrgImportedDesignations:
    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_success(self, mock_get):
        framework = {
            "categories": [
                {
                    "code": "org",
                    "terms": [
                        {
                            "associations": [
                                {"refType": "designation", "name": "Section Officer"},
                                {"refType": "other", "name": "Irrelevant"},
                            ]
                        }
                    ],
                }
            ]
        }
        mock_get.return_value = _mock_response({"result": {"framework": framework}})

        result = json.loads(get_org_imported_designations.func("root-1"))

        assert result["found"] is True
        assert result["imported_designations"] == ["Section Officer"]

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_org_category_not_found(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"framework": {"categories": []}}})

        result = json.loads(get_org_imported_designations.func("root-1"))

        assert result["found"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_exception_is_handled(self, mock_get):
        mock_get.side_effect = Exception("boom")

        result = json.loads(get_org_imported_designations.func("root-1"))

        assert result["found"] is False
        assert "error" in result


# ── SOP-A2 STEP 2 — validate_new_contact_domain ─────────────────────────────

class TestValidateNewContactDomain:
    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_whitelisted_domain(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"domains": ["gov.in", "nic.in"]}})

        result = json.loads(validate_new_contact_domain.func("user@GOV.IN"))

        assert result["is_whitelisted"] is True

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_non_whitelisted_domain(self, mock_get):
        mock_get.return_value = _mock_response({"result": {"domains": ["gov.in"]}})

        result = json.loads(validate_new_contact_domain.func("user@gmail.com"))

        assert result["is_whitelisted"] is False
        assert "lookup_failed" not in result

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_exception_is_handled_without_false_positive(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        result = json.loads(validate_new_contact_domain.func("user@gov.in"))

        assert result["is_whitelisted"] is False
        assert result["lookup_failed"] is True
        assert "error" in result


# ── SOP-A2 STEP 3 — check_contact_registered ────────────────────────────────

class TestCheckContactRegistered:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_email_registered(self, mock_post):
        mock_post.return_value = _search_response(
            [{"id": "user-2", "rootOrgId": "root-2", "rootOrgName": "Other Org", "status": 1}]
        )

        result = json.loads(check_contact_registered.func("taken@x.com"))

        assert result["contact_type"] == "email"
        assert result["is_registered"] is True
        assert result["matched_user_id"] == "user-2"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_mobile_registered_strips_to_last_ten_digits(self, mock_post):
        mock_post.return_value = _search_response([{"id": "user-3"}])

        result = json.loads(check_contact_registered.func("+91-9876543210"))

        assert result["contact_type"] == "mobile"
        assert result["is_registered"] is True
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["json"]["request"]["filters"]["phone"] == "9876543210"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_registered_email_exposes_new_contact_token_for_unmasking(self, mock_post):
        mock_post.return_value = _search_response([{"id": "user-2"}])

        result = json.loads(check_contact_registered.func("  taken@x.com "))

        assert result["_spoc_replacements"] == {"{{NEW_CONTACT}}": "taken@x.com"}

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_registered_mobile_exposes_new_contact_token_for_unmasking(self, mock_post):
        mock_post.return_value = _search_response([{"id": "user-3"}])

        result = json.loads(check_contact_registered.func("9876543210"))

        assert result["_spoc_replacements"] == {"{{NEW_CONTACT}}": "9876543210"}

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_not_registered_has_no_token(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(check_contact_registered.func("free@x.com"))

        assert "_spoc_replacements" not in result

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_not_registered(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(check_contact_registered.func("free@x.com"))

        assert result["is_registered"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_exception_is_handled_without_false_negative(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(check_contact_registered.func("err@x.com"))

        assert result["is_registered"] is False
        assert result["lookup_failed"] is True
        assert "error" in result


# ── SOP-A2 STEP 4.1 — get_enrollment_summary ────────────────────────────────

class TestGetEnrollmentSummary:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_success(self, mock_post):
        in_progress_resp = _mock_response({"result": {"courses": [{"id": "c1"}, {"id": "c2"}]}})
        completed_resp = _mock_response({"result": {"courses": [{"id": "c3"}]}})
        mock_post.side_effect = [in_progress_resp, completed_resp]

        result = json.loads(get_enrollment_summary.func("user-1"))

        assert result["in_progress_count"] == 2
        assert result["completed_count"] == 1
        assert result["enrolled_count"] == 3

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_enrollment_summary.func("user-1"))

        assert "error" in result
        assert "in_progress_count" not in result


# ── SOP-P5 STEP 1 — check_mother_tongue_available ───────────────────────────

class TestCheckMotherTongueAvailable:
    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_found_case_insensitive(self, mock_get):
        mock_get.return_value = _mock_response({"languages": [{"name": "Hindi"}, {"name": "Tamil"}]})

        result = json.loads(check_mother_tongue_available.func("hindi"))

        assert result["found"] is True
        assert result["matched_name"] == "Hindi"

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_not_found(self, mock_get):
        mock_get.return_value = _mock_response({"languages": [{"name": "Hindi"}]})

        result = json.loads(check_mother_tongue_available.func("Klingon"))

        assert result["found"] is False
        assert result["matched_name"] is None

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    def test_exception_is_handled(self, mock_get):
        mock_get.side_effect = Exception("timeout")

        result = json.loads(check_mother_tongue_available.func("Hindi"))

        assert result["found"] is False
        assert "error" in result


# ── SOP-P7 STEP 1 — get_user_ehrms_details ──────────────────────────────────

class TestGetUserEhrmsDetails:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_user_ehrms_details.func("nobody@x.com"))

        assert result["found"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_ehrms_id_set(self, mock_post):
        user = {
            "profileDetails": {
                "additionalProperties": {
                    "externalSystemId": "EHRMS-123",
                    "externalSystem": "EHRMS",
                }
            }
        }
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_ehrms_details.func("asha@x.com"))

        assert result["found"] is True
        assert result["ehrms_id_set"] is True
        assert result["external_system_id"] == "EHRMS-123"
        assert result["external_system_name"] == "EHRMS"

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_ehrms_id_not_set(self, mock_post):
        user = {"profileDetails": {"additionalProperties": {}}}
        mock_post.return_value = _search_response([user])

        result = json.loads(get_user_ehrms_details.func("ravi@x.com"))

        assert result["found"] is True
        assert result["ehrms_id_set"] is False
        assert result["external_system_id"] is None

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_user_ehrms_details.func("err@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── SOP-P12 STEP 1 — get_profile_completion_details ─────────────────────────
# Two-step lookup: POST search (email -> user_id) then GET read (user_id ->
# full profile). Search is mocked via requests.post, read via requests.get.

def _read_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"result": {"response": payload}}
    return resp


class TestGetProfileCompletionDetails:
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_user_not_found(self, mock_post):
        mock_post.return_value = _search_response([])

        result = json.loads(get_profile_completion_details.func("nobody@x.com"))

        assert result["found"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_user_id_missing(self, mock_post):
        mock_post.return_value = _search_response([{"firstName": "Asha"}])

        result = json.loads(get_profile_completion_details.func("asha@x.com"))

        assert result["found"] is False
        assert result["message"] == "User id not available in profile."

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_all_fields_complete(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-1"}])
        mock_get.return_value = _read_response({
            "firstName": "Asha",
            "profileCompletionPercentage": 100,
            "profileDetails": {
                "profileImageUrl": "https://example.com/photo.png",
                "profileBannerUrl": "https://example.com/banner.png",
                "verifiedKarmayogi": "true",
                "employmentDetails": {"aboutme": "I work in govt."},
                "profileDesignationStatus": "VERIFIED",
                "profileGroupStatus": "VERIFIED",
            },
        })

        result = json.loads(get_profile_completion_details.func("asha@x.com"))

        assert result["found"] is True
        assert result["profile_completion_percentage"] == 100
        assert result["profile_photo_set"] is True
        assert result["cover_photo_set"] is True
        assert result["profile_verification_verified"] is True
        assert result["about_me_set"] is True
        assert result["designation_verified"] is True
        assert result["group_verified"] is True

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_fields_incomplete_reports_which_are_missing(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-2"}])
        mock_get.return_value = _read_response({
            "firstName": "Ravi",
            "profileCompletionPercentage": 66.8,
            "profileDetails": {
                "profileImageUrl": None,
                "profileBannerUrl": None,
                "verifiedKarmayogi": None,
                "employmentDetails": {},
                "profileDesignationStatus": "NOT-VERIFIED",
                "profileGroupStatus": "NOT-VERIFIED",
            },
        })

        result = json.loads(get_profile_completion_details.func("ravi@x.com"))

        assert result["found"] is True
        assert result["profile_completion_percentage"] == 66.8
        assert result["profile_photo_set"] is False
        assert result["cover_photo_set"] is False
        assert result["profile_verification_verified"] is False
        assert result["about_me_set"] is False
        assert result["designation_verified"] is False
        assert result["group_verified"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.get")
    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_verified_karmayogi_string_false_treated_as_not_verified(self, mock_post, mock_get):
        mock_post.return_value = _search_response([{"id": "user-3"}])
        mock_get.return_value = _read_response({
            "firstName": "Meera",
            "profileCompletionPercentage": 90,
            "profileDetails": {"verifiedKarmayogi": "false"},
        })

        result = json.loads(get_profile_completion_details.func("meera@x.com"))

        assert result["profile_verification_verified"] is False

    @patch("app.core.tools.profile_user_management_tools.requests.post")
    def test_exception_is_handled(self, mock_post):
        mock_post.side_effect = Exception("timeout")

        result = json.loads(get_profile_completion_details.func("err@x.com"))

        assert result["found"] is False
        assert "error" in result


# ── Convenience list ─────────────────────────────────────────────────────────

class TestGetProfileUserManagementTools:
    def test_returns_expected_tools(self):
        tools = get_profile_user_management_tools()
        names = {t.name for t in tools}

        assert names == {
            "get_user_transfer_request_details",
            "get_mdo_details_by_org_id",
            "search_organization",
            "search_organization_under_ministry_or_state",
            "get_yp_am_details",
            "search_designation",
            "get_user_root_org_id",
            "get_org_imported_designations",
            "get_user_profile",
            "get_mdo_details",
            "validate_new_contact_domain",
            "check_contact_registered",
            "get_enrollment_summary",
            "get_profile_verification_request_details",
            "get_department_mdo_admin",
            "check_mother_tongue_available",
            "get_user_ehrms_details",
            "get_profile_completion_details",
        }


# ── PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT — SOP-P4/SOP-A2 routing guard ─────
# Pure prompt-text check: an explicit OTP-not-received complaint must route to
# SOP-P4, and every other email/mobile update request must route to SOP-A2 —
# guards against the two SOPs' scopes silently drifting back into overlap.

class TestEmailMobileUpdateSopRouting:
    def test_sop_p4_scoped_to_otp_not_received_only(self):
        assert "Covers ONLY users who explicitly report not receiving the OTP" in PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT

    def test_sop_p4_points_other_requests_to_sop_a2(self):
        assert "is SOP-A2 below,\nnot this SOP." in PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT

    def test_sop_a2_points_otp_not_received_to_sop_p4(self):
        assert "EXCEPTION: a user who explicitly\nreports not receiving the OTP is SOP-P4 above instead" in PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT


class TestSopA2NoContactPlaceholders:
    """The LLM only sees a PII-masked ticket message, so SOP-A2 must never tell it to
    echo the new contact value — doing so leaks `<EMAIL_ADDRESS>` into customer drafts."""

    def test_constraint_forbids_value_and_placeholder_tokens(self):
        assert "NEVER write the new\n  Email ID / Mobile Number value" in PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT
        assert "<EMAIL_ADDRESS> / <PHONE_NUMBER> placeholder" in PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT

    def test_escalation_reason_no_longer_asks_for_actual_value(self):
        assert "the actual value, exactly as the user gave" not in PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT


class TestSopA2AlreadyRegisteredConfirmation:
    def test_confirmation_uses_unmask_tokens_after_format(self):
        rendered = PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT.format(email="x", main_category="m")
        assert "Current Email ID: {{USER_EMAIL}}" in rendered
        assert "to be Updated: {{NEW_CONTACT}}" in rendered
        assert "Please confirm whether you would like us to raise a support" in rendered


class TestSpocReplacementsAccumulate:
    def test_tokens_survive_later_tool_calls_in_same_execute_pass(self):
        import json
        from unittest.mock import MagicMock, patch
        from langchain_core.messages import AIMessage
        from app.core.graph.subgraphs.base_subgraph import BaseSubgraph

        class _Sub(BaseSubgraph):
            CATEGORY = "t"
            def system_prompt(self, state): return "sys"
            def get_tools(self, state):
                from langchain_core.tools import tool
                @tool
                def first() -> str:
                    """first"""
                    return json.dumps({"ok": 1, "_spoc_replacements": {"{{NEW_CONTACT}}": "9999999999"}})
                @tool
                def second() -> str:
                    """second"""
                    return json.dumps({"ok": 2})
                return [first, second]

        llm = MagicMock()
        llm.bind_tools.return_value.invoke.side_effect = [
            AIMessage(content="", tool_calls=[
                {"name": "first", "args": {}, "id": "1"},
                {"name": "second", "args": {}, "id": "2"},
            ]),
            AIMessage(content="done"),
        ]
        with patch("app.core.graph.subgraphs.base_subgraph._llm_execute", llm):
            out = _Sub().execute_node({"ticket_id": "t", "email": "a@b.c", "message": "m"})
        assert out["spoc_replacements"]["{{NEW_CONTACT}}"] == "9999999999"
