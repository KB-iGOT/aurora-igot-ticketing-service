# Agent SOP — Profile & User Management Issues Resolution
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Access Revoked, Email/Mobile Registration, Profile
Verification, Designation/Group, and Profile Update Use Cases

---

## Global Agent Principles
- Tool-first, ask-last. Fetch relevant status via API immediately before asking the user
  anything.
- Single-pass diagnosis. Fetch all relevant data upfront and deliver one complete,
  informed response.
- Be empathetic, concise, and professional in every response.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_user_transfer_request_details` | `(email)` | Check whether a Transfer Request has already been raised, via `wfTransferRequest` — also the greeting-name source (SOP-A1 STEP 1) |
| `get_mdo_details_by_org_id` | `(org_id)` | Fetch MDO Admin contact for a SPECIFIC organisation id — unlike `get_mdo_details`, which always derives the org from the user's own profile (SOP-A1 STEP 2) |
| `get_yp_am_details` | `(ministry_or_state)` | YP/SPOC fallback when no MDO Admin exists — reused from login_issue_tool.py (SOP-A1 STEP 3, Edge Case 2, SOP-A2 fallback) |
| `search_organization` | `(org_name)` | Search for an organization by EXACT name (case-insensitive, no partial matching) via the Org Search API (SOP-A1 Edge Case 2) |
| `search_organization_under_ministry_or_state` | `(ministry_or_state_name, org_name)` | Second-attempt search when the exact name fails: matches the Ministry/State by name, then searches (partial match) for the org under it via the Org Hierarchy Search API (SOP-A1 Edge Case 2) |
| `search_designation` | `(designation_name)` | Fetch the full active-designation master list — matching against the user's wording is done by the LLM, conservatively (word-boundary, never loose substring) (SOP-P3 STEP 1) |
| `get_user_root_org_id` | `(email)` | Resolve the user's own `rootOrgId` via email → user_id → User Read API (SOP-P3 STEP 2) |
| `get_org_imported_designations` | `(root_org_id)` | Fetch which designations a specific org's MDO has actually imported, via the Org Framework Read API — response shape unverified against a live call, parsing may need adjustment (SOP-P3 STEP 2) |
| `get_user_profile` | `(email)` | Ticket owner's OWN organization/ministry details — reused from profile_update_tool.py (SOP-A2 STEP 2A/3.1.1, YP fallback input) |
| `get_mdo_details` | `(email)` | MDO Admin for the ticket owner's OWN organization — reused from login_issue_tool.py (SOP-A2 STEP 2A/3.1.1) |
| `validate_new_contact_domain` | `(new_email)` | Domain-whitelist check for the NEW email the user wants to update to — NOT the ticket owner's own email (SOP-A2 STEP 2) |
| `check_contact_registered` | `(new_contact)` | Checks whether the new Email ID / Mobile Number is already registered to another account; auto-detects email vs. mobile (SOP-A2 STEP 3) |
| `get_enrollment_summary` | `(user_id)` | Enrollment counts (In-Progress/Completed) for the OTHER account already linked to the new contact — internal escalation-note use only (SOP-A2 STEP 4.1) |
| `check_mother_tongue_available` | `(mother_tongue_name)` | Checks a user-reported mother tongue against the platform's master language list, case-insensitive exact match (SOP-P5 STEP 1) |
| `get_user_ehrms_details` | `(email)` | Checks whether the user's EHRMS ID / External System ID is set on their profile (SOP-P7 STEP 1) |
| `get_profile_completion_details` | `(email)` | Checks `mandatoryFieldsExists` (overall bool) plus Profile Photo/Group/Designation individually — Cover Photo, About Me, and Username Verification are not exposed by this API at all (SOP-P12 STEP 1) |

---
---

# SOP-A1: Access Revoked

Both Access Revoked scenarios are implemented — Transfer Request already raised, and no
Transfer Request raised yet. SOP-A2 (Email/Mobile already registered, below) and Profile
Update (SOP-P1–P9, below) are also implemented. Profile Verification/Verified Badge and
Designation/Group Not verified still escalate immediately as out of scope.

Covers users who see: "Your access has been revoked because your organization no longer
identifies you as a user..." — typically because their organization is mapped as the
"iGOT" placeholder and their profile status is "Not My User".

**STEP 1.** `get_user_transfer_request_details(email)` — checks `wfTransferRequest`; also
the greeting-name source (its `firstName` field, same call).

| has_transfer_request | Action |
|---|---|
| false | → STEP 1A. Resolved. Close — formally tell the user their previous department's MDO marked their profile "Not My User", resulting in revocation of access, so they must raise a Transfer Request. As its own separate paragraph, guide them through it (Profile Icon → View Profile → Make Transfer Request → select Organization/Group/Designation → Submit → await MDO Admin approval). |
| true | → STEP 2. Tell the user a Transfer Request has already been raised to `transfer_org_name`. |

**STEP 2.** `get_mdo_details_by_org_id(org_id=transfer_root_org_id)` — MDO Admin for the
TARGET transfer organization, never the user's own (revoked) organization.

- MDO found → Resolved. Close — share MDO Admin contact (Name, Email, Mobile — masked
  placeholder tokens copied exactly); ask the user to connect with them and request
  approval.
- MDO not found → STEP 3.

**STEP 3.** `get_yp_am_details(ministry_or_state=transfer_org_name)` — YP/SPOC fallback.

- YP/SPOC found → Resolved. Close — share contact; ask the user to coordinate for
  creation of an MDO Leader/Admin and approval-related support.
- YP/SPOC not found → **escalate=true** — keep the STEP 1 context sentence (Transfer Request already raised to the target org), then briefly note the MDO/YP contact couldn't be found and that support will reach out soon. Brief, but not stripped down to one bare sentence — no "a human agent will review your case" padding.

**Before STEP 1:** if the ticket message describes the "Make Transfer Request" button/option
itself as disabled, greyed out, or not clickable (rather than a generic "access has been
revoked" report), skip STEP 1 and go directly to Edge Case 1.

## Edge Case 1 — Transfer Request Button Disabled / Not Clickable

`get_user_transfer_request_details(email)` called only for its `firstName` field (greeting
name) — `wfTransferRequest` is not relevant here. Likely caused by an existing pending
request under Primary Details blocking a fresh transfer request. Resolved. Close —
formally tell the user they currently have a pending request under Primary Details
preventing a new Transfer Request; request that they withdraw it first (once withdrawn,
"Make Transfer Request" becomes enabled). As its own separate paragraph, guide them:
Profile section → Primary Details section → Withdraw Request → submit a fresh Transfer
Request.

## Edge Case 2 — User Unable to Find Organization in Dropdown

Triggered ONLY when the user names a specific organization they cannot find — the org name
must be present in the message itself for this edge case to apply; if not, fall through to
STEP 1. Extract it directly, do not ask the user to restate it.

`get_user_transfer_request_details(email)` — greeting name only. `search_organization(org_name)`
— EXACT name match only (case-insensitive, no partial matching), pass the name exactly as
the user wrote it.

- found=true → Resolved. Close — full, formal, official-email response (not a single terse
  line): acknowledge the backend verification carried out, confirm the exact organization
  name was located, request the user select it from the dropdown, close courteously.
- found=false, AND the message also names a Ministry/State → `search_organization_under_ministry_or_state(ministry_or_state_name, org_name)`
  — matches the Ministry/State by name against the hierarchy list APIs, then searches
  (partial match) for the org under it via the Org Hierarchy Search API. found=true →
  Resolved. Close — same formal, full official-email style as above.
- found=false (either search, or no Ministry/State was named at all) → `get_yp_am_details(ministry_or_state=<Ministry/State/Department from the
  message, else the org name>)`. YP/SPOC found → Resolved. Close — tell the user we could
  not find that organization; share the YP/SPOC contact; ask them to coordinate for
  organization creation/onboarding. YP/SPOC not found → **escalate=true** — tell the user
  we could not find the organization, and their issue has been logged and escalated to the
  support team (same standard phrasing used elsewhere, not invented wording).

## SOP-A1 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| No Transfer Request raised yet | ❌ |
| Edge Case 1 — Transfer Request button disabled | ❌ |
| Edge Case 2 — org named, found | ❌ |
| Edge Case 2 — org named, not found, YP/SPOC found | ❌ |
| Edge Case 2 — org named, not found, neither found | ✅ |
| Transfer Request raised, MDO Admin found | ❌ |
| Transfer Request raised, no MDO, YP/SPOC found | ❌ |
| Transfer Request raised, neither MDO nor YP/SPOC | ✅ |

---
---

# SOP-P1: Profile Update — Name Update

Use Case: user requests to update their NAME on their profile.

No tool call needed — pure self-service guidance. Resolved. Close. No ticket. Guide the
user through the steps, as an HTML ordered list, followed by a closing sentence:
1. Click on View Profile.
2. Navigate to the Name section.
3. Click on Edit Profile.
4. Update the Name as required.
5. Click on Save / Submit to update the details.

Closing: "Please feel free to reach out if you face any difficulty with the above
steps."

# SOP-P2: Profile Update — Display Name Update

Use Case: user requests to update their Display Name / Profile Name / User Name —
distinct from SOP-P1's NAME field (this is the system-generated name shown next to the
profile, not the editable profile name).

No tool call needed. Resolved. Close. No ticket. Inform the user politely: the display
name (profile name / user name) is generated automatically by the platform and cannot be
manually modified by users at this time; close with an appreciative, courteous note
inviting further questions.

# SOP-P3: Profile Update — Designation Not Found

Use Case: user reports being unable to find their designation while updating their
profile.

**STEP 1.** `search_designation(designation_name)` — returns the full active-designation
master list (id + name), not a pre-filtered match. The LLM matches the user's wording
against it conservatively:
- Exact match (case-insensitive, trivial spelling/spacing variant) → single confident
  match, proceed to STEP 2.
- No plausible match at all → **escalate=true** (not found in master data at all).
- Multiple plausible matches, not clearly obvious which one (e.g. an abbreviation that
  could expand to more than one real designation — "sec officer" must never be silently
  treated as matching "Secretary Officer" via substring overlap) → **needs_clarification=true**,
  ask the user to confirm the exact/full designation name. Never guess — acting on the
  wrong designation is a real mistake, not a minor inconvenience.

**STEP 2.** Only reached once exactly one designation is confirmed.
`get_user_root_org_id(email)` → the user's own `rootOrgId`.
`get_org_imported_designations(root_org_id)` → designations this org's MDO has actually
imported (existing in master data is not enough — each org must separately import it).

| Outcome | Action |
|---|---|
| Confirmed designation IS imported by the user's own org | Resolved. Close — give the 4-step self-service guide (View Profile → Primary Details → Edit/Pen icon → update Designation). |
| Confirmed designation exists in master data but NOT imported by the user's own org | Resolved. Close — `get_mdo_details_by_org_id(org_id=<user's own root_org_id>)`, tell the user it hasn't been imported yet, share MDO Name/Email, ask them to request the import. If no MDO found for their own org → **escalate=true**. |

# SOP-P4: Email ID / Mobile Number Updation — OTP Not Received

Use Case: user reports not receiving the OTP while trying to update their Email ID or
Mobile Number on their profile.

`get_user_root_org_id(email)` → the user's own `rootOrgId`.
`get_mdo_details_by_org_id(root_org_id)` → the user's own MDO Admin.

- MDO found → Resolved. Close — one single, formal response covering both: (1) OTP
  verification is mandatory for this update and cannot be bypassed, and (2) since OTP
  isn't being received, connect directly with the MDO Admin (share Name/Email only, no
  Mobile), providing both the existing and the new Email ID/Mobile Number so the MDO can
  make the change on their behalf.
- MDO not found → **escalate=true**, standard phrasing.

# SOP-P5: Profile Update — Mother Tongue Update

Use Case: user reports their mother tongue is not available while updating their profile.

**STEP 1.** `check_mother_tongue_available(mother_tongue_name)` — case-insensitive exact
match against the platform's master language list.

- Found → Resolved. Close — give the self-service guide (View Profile → Other Details →
  Edit icon → select the Mother Tongue → Save).
- Not found → There is nothing actionable to tell the user — only a human can decide
  whether to add it to master data. **escalate=true**, routed to a silent `human_queue`
  hand-off (no automated email — the subgraph structurally detects this dead end).

# SOP-P6: Profile Update — EHRMS ID / External System ID Update

Use Case: user wants to update their EHRMS ID / External System ID, or reports the one
shown on their profile is incorrect.

`get_user_root_org_id(email)` → the user's own `rootOrgId`.
`get_mdo_details_by_org_id(root_org_id)` → the user's own MDO Admin.

- MDO found → Resolved. Close — one single, formal response: (1) individual users cannot
  update the EHRMS ID / External System ID themselves, only their department's MDO can;
  (2) share MDO Name/Email only (no Mobile); (3) ask the user to share their Registered
  Email ID, Registered Mobile Number, and the correct EHRMS ID with the MDO.
- MDO not found → **escalate=true**, silent `human_queue` hand-off (no automated email).

# SOP-P7: Profile Update — Date of Retirement Update

Use Case: user reports Date of Retirement is blank on their profile, or unable to edit it.

**STEP 1.** `get_user_ehrms_details(email)` → `ehrms_id_set`, `external_system_id`,
`external_system_name`.

- EHRMS ID IS present → Resolved. Close — Date of Retirement is auto-fetched from the
  EHRMS portal and cannot be edited on iGOT directly; ask the user to update it on the
  EHRMS portal, where it will automatically reflect back on iGOT.
- EHRMS ID is NOT present → `get_user_root_org_id(email)` → `get_mdo_details_by_org_id(root_org_id)`.
  - MDO found → Resolved. Close — single response covering: EHRMS ID isn't set; Date of
    Retirement is auto-fetched from EHRMS and can't be edited on iGOT directly; the
    MDO/Nodal Officer needs to (a) set the EHRMS ID and (b) ensure Date of Retirement is
    correct in EHRMS; once both are done it reflects automatically on iGOT; share MDO
    Name/Email only.
  - MDO not found → `get_yp_am_details(ministry_or_state)` fallback. Found → Resolved.
    Close — same response shape, YP/SPOC contact instead. Not found → **escalate=true**,
    silent `human_queue` hand-off (no automated email).

# SOP-P8: Profile Update — Service History Update

Service History's current entry is never editable directly — it is always a default value
derived from the Organization and Designation currently mapped to the user's profile.

**Before STEP 1.** User wants to add a PREVIOUS/past employment entry (distinct from their
current one) → Case 2. Otherwise (reporting the current Service History entry is wrong,
blank, or not editable) → Case 1.

## Case 1: User Unable to Edit Service History

**STEP 1.** `get_user_profile(email)` → current `rootOrgName` / designation. Compare
against the Organization (and Designation, if given) the user believes should be reflected,
extracted from the ticket message.

- Matches → Resolved. Close — "your Service History entry reflects the current
  Organization and Designation mapped to your profile, and is accurate. Please feel free to
  reach out if you require any further assistance."
- Does not match → STEP 2.

**STEP 2.** Resolved. Close — single response covering: (1) Service History is a default
value derived from profile Organization/Designation and can't be edited directly; (2)
Transfer Request steps (View Profile → Transfer Request → update Organization/Designation
→ Submit for Approval); (3) the Department MDO approves it, and Service History
auto-updates once approved.

`search_organization(org_name=<org the user named>)` — EXACT match only.
- found=false → ask the user to confirm the exact Organization name. No ticket.
- found=true → `get_mdo_details_by_org_id(org_id)` for THAT organization (never the user's
  own, incorrect, current org).
  - MDO found → include in the same response: share MDO Name/Email only, ask the user to
    connect with them if required for approval.
  - MDO not found → **escalate=true**, silent `human_queue` hand-off (no automated email).

## Case 2: User Wants to Add Previous Employment History

No tool call needed — pure self-service navigation. Resolved. Close. Guide the user:
View Profile → Service History → Plus (+) icon → enter Organization/Employment Name, Start
Date, End Date, other required fields → Save.

# SOP-P9: Profile Update — Educational Qualification Update

Use Case: user unable to find their college/institute (or degree) name while adding an
Educational Qualification.

No tool call needed — pure self-service navigation. Resolved. Close. Guide the user:
1. Click on View Profile and navigate to the Educational Qualification section.
2. Click the Plus (+) icon to add a new entry.
3. Select the Degree Name. If not available in the list, select Other and enter it
   manually.
4. Enter the Field of Study.
5. Select the Institute Name. If not available in the list, select Other and enter the
   correct college or institute name manually.
6. Enter the Start Year and End Year, then click Add to save.

Closing: confirm that once these steps are completed, the Educational Qualification will
be successfully added to the user's profile.

# SOP-P10: Profile Update — Profile Photo Update

Use Case: user wants to update their profile photo, including replacing an existing one.

No tool call needed — pure self-service navigation. Resolved. Close. Guide the user:
1. Click on View Profile.
2. Click the three-dot (⋮) menu next to your profile name/username.
3. Click on Edit Profile.
4. Click on the Profile Photo section. If a photo is already present, delete it first.
5. Select and upload the new photo — file size ≤ 1 MB, resolution not exceeding 180×180
   pixels.
6. Adjust the photo as required, click Apply Changes, then Save Changes.

Closing: confirm that once these steps are completed, the profile photo will be
successfully updated.

# SOP-P11: Profile Update — Cover Photo Update

Use Case: user wants to update their cover photo.

No tool call needed — pure self-service navigation. Resolved. Close. Guide the user:
1. Click on View Profile.
2. Click the three-dot (⋮) menu at the top right corner of the profile section.
3. Click on Edit Cover Photo, then select Change Cover Photo.
4. Choose the desired cover photo from your device.
5. Click Apply Changes.

Closing: confirm that once applied, the cover photo will be successfully updated and saved.

# SOP-P12: Profile Update — Profile Completion Not Showing 100%

Use Case: user reports their profile completion percentage is not showing 100%.

**STEP 1.** `get_profile_completion_details(email)` -> `mandatory_fields_exists` (overall
bool), `profile_photo_set`, `group`, `designation`. Cover Photo, About Me, and Username
Verification status are NOT exposed by this API at all — there is no field for them.

- `mandatory_fields_exists = true` → Resolved. Close — tell the user all mandatory fields
  are already complete; if the percentage still isn't showing 100%, ask them to
  refresh/re-login, as it may just be a display delay.
- `mandatory_fields_exists = false` → STEP 2.

**STEP 2.** Resolved. Close — build ONE unified list, presented the same way regardless of
what we could actually verify (never tell the user which fields we could or couldn't
check): `profile_photo_set = false` → Profile Photo; empty `group` → Group; empty
`designation` → Designation; and ALWAYS include Cover Photo, About Me, and Username
Verification (never confirmed complete by this API, and `mandatory_fields_exists = false`
means something is still incomplete). Use the SOP's own example communication verbatim —
"We have checked your profile and found that certain details are missing. Please update the
following details in your profile to achieve 100% profile completion:" followed by the
list, then explain the percentage reaches 100% only once every mandatory field is updated.

---

Every other subcategory in this category (Profile Verification/Verified Badge,
Designation/Group Not verified) still escalates immediately as out of scope. Service
Details (fetching cadre/service master-config data) is on hold, blocked on a lookup that
requires a real user session token rather than the service-level API key every other tool
here uses.

# SOP-A2: Email / Mobile Already Registered

Covers users trying to update the Email ID or Mobile Number on their profile — asking how,
reporting an error during the update, or reporting the new contact is "already registered".

**Before STEP 1.** If the message reports not receiving an OTP during an update attempt
(and isn't a fresh "how do I update" question), skip directly to STEP 3.1.1.

**STEP 1.** Identify the new Email ID / Mobile Number from the message.
  Not present → ask the user to share it (no ticket, wait for reply).
  Present, is an email → STEP 2. Is a mobile number → STEP 3 (no domain to check).

**STEP 2.** `validate_new_contact_domain(new_email)` — domain whitelist check on the NEW
contact (never the ticket owner's own, already-whitelisted, email).
  Whitelisted → STEP 3. Not whitelisted → STEP 2A.

**STEP 2A — Domain Not Whitelisted.** `get_user_profile(email=<owner>)` for the owner's own
org/ministry, then `get_mdo_details(email=<owner>)` for their MDO Admin.
  MDO found → Resolved. Close — share MDO contact, explain the domain isn't whitelisted.
  MDO not found → `get_yp_am_details(ministry_or_state=<owner's org/ministry>)`.
    YP/SPOC found → Resolved. Close — share YP/SPOC contact.
    YP/SPOC not found → **escalate=true**.

**STEP 3.** `check_contact_registered(new_contact)` — auto-detects email vs. mobile
(mobile is matched via the private User Search API's `phone` filter, plain 10-digit number,
no country code).
  Not registered → STEP 3.1. Already registered → STEP 3.2 (confirm it's genuinely a
  different account, not the ticket owner's own).

**STEP 3.1 — Not Registered.** Resolved. Close — confirm the contact is available; guide the
user through the profile update (View Profile → Other Details → Edit icon → enter new
contact → Request OTP → verify OTP → Save Changes).

**STEP 3.1.1 — OTP Not Received.** Never generate/verify an OTP directly. Same MDO → YP/SPOC
lookup pattern as STEP 2A, for the ticket owner's own organization.
  MDO or YP/SPOC found → Resolved. Close — share contact.
  Neither found → **escalate=true**.

**STEP 3.2 — Confirm the Match Isn't the Ticket Owner's Own Account.**
`check_contact_registered` matches ANY account already using that contact — including the
ticket owner's own, if they simply re-sent their current Email ID / Mobile Number unchanged.
`get_user_profile(email=<owner>)` (reuse if already called this turn) for the owner's own
user id.
  matched_user_id == owner's own id → STEP 3.3 (their own account — not a duplicate).
  matched_user_id != owner's own id → STEP 4 (genuinely a different account).

**STEP 3.3 — Contact Is Already the Owner's Own.** Resolved. Close — no ticket. Tell the
user the Email ID / Mobile Number they provided is already the one on their own account, so
no update is needed; ask them to share a different one if they meant to update to something
else.

**STEP 4 — Registered to a Different Account.** `get_enrollment_summary(user_id=<matched_user_id>)`
for the OTHER account's enrollment counts — internal escalation note only, never shared with
the end user. First reply (`needs_clarification`): state the contact is already associated with
another account; list the impact (that account deactivated, its access lost, learning records
stay with the current account); then "Kindly review the below details and confirm whether they
are correct" with `Current Email ID` and `<Email ID / Mobile Number> to be Updated` (filled via
`{{USER_EMAIL}}` / `{{NEW_CONTACT}}` tokens, never the PII-masked value); close with "Please
confirm whether you would like us to raise a support request for this change." Stop and wait.

**STEP 4 (continuation).**
  Affirmative → STEP 4.4.
  Negative → Resolved. Close — no changes made, no ticket.
  Ambiguous → ask again for a clear Yes/No (no ticket yet).

**STEP 4.4 — Confirmed.** `escalate=true`. Escalation note: owner's user id and current email,
contact type requested, user's confirmation, the other account's org and enrollment counts,
and that the user understands the other account will be deactivated. Silent human_queue hand-off.

## SOP-A2 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| No new contact given yet | ❌ (ask for it) |
| Domain not whitelisted, MDO or YP/SPOC found | ❌ |
| Domain not whitelisted, neither found | ✅ |
| Not registered | ❌ |
| Not registered, OTP not received, MDO or YP/SPOC found | ❌ |
| Not registered, OTP not received, neither found | ✅ |
| Registered, matched account is the ticket owner's own | ❌ |
| Registered to a different account, user declines | ❌ |
| Registered to a different account, user confirms | ✅ |
