# Agent SOP — Training Plan / APAR Courses Not Visible in Profile
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Training Plan / APAR Course Visibility Use Cases

---

## Global Agent Principles
- **Tool-first.** Fetch the user's CBP plan and profile status via API immediately.
- **Never commit to approval timelines** for MDO/YP-driven assignments.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_user_profile` | `(email)` | Fetch organization, profile verification status, designation, group, ministry/state, `rootOrgId` |
| `get_user_cbp_plan` | `(email)` | Fetch the user's CBP (training) plan — `total_count`, `apar_count`, `non_apar_count`, `apar_plans`/`non_apar_plans` (each with `course_name`, `content_id`, `is_apar`, `end_date`) |
| `get_org_type` | `(root_org_id)` | Determine whether an org is a State Government entity or a Central Ministry — returns `org_type`: `"state"`, `"ministry"`, or `"unknown"` |
| `get_assigned_cap_courses` | `(email)` | Fetch the CAP(s) assigned to a user via the admin "assigned courses" API, filtered to `courseCategory: "Comprehensive Assessment Program"` — returns `plan_id` (CAP DO_ID), `course_name`, `end_date` per CAP |
| `get_user_enrollments` | `(email, status_filter=None, content_id=None)` | Fetch enrollment/progress status for the user's assigned courses. Pass `content_id` to check one specific course across the full enrollment history — returns `course_name`, `completed: true/false`, and `certificate_issued: true/false` for that course |
| `get_cap_hierarchy` | `(cap_id)` | Fetch a CAP's child courses (`identifier`, `name`, `resource_type`) plus `assessment_child_id` via `GET /api/private/content/v3/hierarchy/{cap_id}` |
| `get_access_settings` | `(content_id)` | Fetch eligibility restrictions configured for a piece of content — `accessControl.userGroups[].userGroupCriteriaList[]`, each with `criteriaKey` (`"rootOrgId"` or `"designation"`) and `criteriaValue`. Empty/no groups means no restriction. Reused from the Courses category's tools. |
| `resolve_org_names` | `(org_ids)` | Resolve a list of `rootOrgId` values to real org names via the Org Search API — needed because `get_access_settings`' org criteria are raw ids, not names |
| `get_mdo_details` | `(email)` | Fetch MDO Admin/Leader name and email for the user's organization |
| `get_yp_am_details` | `(ministry_or_state)` | Fetch YP/AM (SPOC) name, email, and contact details |
| `get_user_cap_assignment` | `(email)` | Fetch the user's assigned Comprehensive Assessment Program(s) (CAP) — returns `total_count` and `assignments` (each: `cap_id`, `cap_name`, `end_date`, `link`, `link_html`) |
| `get_assessment_attempt_count` | `(email, assessment_identifier)` | Fetch attempts made/allowed for an assessment via `GET /api/admin/assesment/retake/count`; returns `attempts_made`, `attempts_allowed`, `remaining_attempts`, `limit_exceeded`. `assessment_identifier` MUST be the CAP's actual Final Assessment child id (from `get_cap_hierarchy`'s `assessment_child_id`), never the CAP's own container id — the API rejects the CAP's own id with a server error. |

**No PII in shared contact details.** When sharing MDO/YP contact, share Name and Email only — never the Mobile number.

---
---

# SOP-1: Training Plan / APAR Courses Not Visible in Profile

## Purpose
Handle cases where a user reports that their Training Plan / APAR courses are not visible,
that unexpected courses appear, or that the wrong plan/courses were assigned.

---

## STEP 1 — Fetch the User's CBP Plan

**[TOOL CALL]**
```
get_user_cbp_plan(email = <user_email>)
```

**If `found: false`** (profile could not be located). Resolved. Close.
> "We could not verify your profile at this time; please try again later."
Do not proceed to any other step.

**Otherwise, route based on result:**

| CBP Plan Data | Action |
|----------------|--------|
| Exists (total_count > 0) | → STEP 2 |
| Does not exist (total_count = 0) | → STEP 3 |

---

## STEP 2 — CBP Plan Data Exists → Confirm Visibility and List APAR Courses

**[TOOL CALL]**
```
get_user_enrollments(email = <user_email>)
```

**Outcome:** Resolved — respond and close.

**User Message** (fill in `[APAR Course List]` as an HTML list of the real `course_name`
values from `apar_plans` — actual names, not a placeholder or a count):
> "Upon checking, we found that the APAR courses are visible in your profile. Kindly log in
> and check under the APAR Courses Section, where all assigned APAR courses will be visible
> with a green tag.
>
> [APAR Course List]
>
> We request you to kindly try the following once:
> 1. Log out and log back into the portal.
> 2. Access the platform again using an updated version of Google Chrome.
> 3. Check under the APAR Courses Section on the homepage.
>
> If the issue still persists, please share a screenshot of your APAR section, so that we can
> assist you further."

---

## STEP 3 — No CBP Plan Data Exists → Validate Profile

**[TOOL CALL]**
```
get_user_profile(email = <user_email>)   # also returns rootOrgId
```

**State Government exemption check (before anything else):**
```
get_org_type(root_org_id = <rootOrgId from get_user_profile>)
```
The DoPT O.M. (dated 04.07.2025) mandating APAR Training Plan creation does **not** apply to
State Government officials — no plan existing is expected/correct for them, not a problem to
route through Transfer Request or MDO/YP contact.

**`org_type: "state"`.** Resolved. Close.
> "The O.M. dated 04.07.2025 of the Department of Personnel and Training (DoPT), mandating
> the creation of APAR Training Plan on iGOT, is not applicable for State Government
> officials for the current year.
>
> However, if there are any specific instructions, circulars, or directives issued by your
> department/organisation/state government, regarding the creation of a Training Plan, we
> request you to share the same with us. This will enable us to forward the information to
> the concerned authority and take appropriate action accordingly."

**`org_type: "ministry"` or `"unknown"`** → proceed with the routing below.

**Route based on organization:**

| Organization | Action |
|--------------|--------|
| Mapped to **"iGOT"** or **"Karmayogi Prarambh Trainee"** (hardcoded check) | → STEP 3A |
| Mapped to any other real organization | → STEP 4 |

---

## STEP 3A — Mapped to iGOT / Karmayogi Prarambh Trainee → Guide Transfer Request

**Outcome:** Resolved — close after guiding.

**User Message:**
> "Upon checking, we found that your profile is currently mapped to iGOT/Karmayogi Prarambh Trainee.
>
> APAR courses and training plans are assigned only after a user is mapped to their respective department/organization. As your profile is not yet mapped to the correct department/organization, no APAR courses or training plans can be assigned at this stage.
>
> Kindly raise a Transfer Request to move your profile to the correct department/organization. Once the transfer request is approved and your profile is mapped to the appropriate organization, the concerned authority will be able to assign the relevant training plans and courses.
>
> To raise a Transfer Request:
> 1. Click on the **Profile Icon** (top-right corner) → **View Profile** → **Make Transfer Request**.
> 2. Select the correct Organization Name, Group, and Designation.
> 3. Click **Submit** — the request is sent to the concerned MDO Admin for approval.
>
> Once your profile is mapped to the correct organization, kindly recheck the APAR/training plan section."

---

## STEP 4 — Mapped to a Real Organization → Check Profile Verification

**Route based on `profile_status`:**

| Profile Status | Action |
|-----------------|--------|
| Verified | → STEP 5 |
| Not Verified | → STEP 6 |

---

## STEP 5 — Profile Verified → Share Assigning-Authority Contact

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If MDO details are not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

Both tools return the contact's name/email as **masked placeholder tokens** (e.g.
`{{MDO_ADMIN_NAME}}`, `{{MDO_ADMIN_EMAIL}}`, `{{YP_AM_NAME}}`, `{{YP_AM_EMAIL}}`) — not real
values, the same way user emails are masked elsewhere. Copy the tokens exactly as
returned, character for character, curly braces included. Never invent, guess, or
paraphrase a name/email — the real value is substituted in automatically after the
response is generated, but only if the token text matches exactly. Share Name and Email
only — never Mobile.

**Outcome:** Resolved, if either contact is found. Escalate only if **neither** an MDO Admin **nor** a YP/SPOC can be found.

**User Message (contact found):**
> "Upon checking, we found that no CBP/APAR training plan is currently assigned to your profile. The concerned authority is responsible for assigning training plans and courses.
>
> Kindly connect with the contact below for further assistance:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

**User Message (neither contact found — escalate):**
> Tell the user: their issue has been logged and escalated to the support team, and a specialist will assist them shortly.

---

## STEP 6 — Profile Not Verified → Guide Profile Verification

**Outcome:** Resolved — close after guiding.

**User Message:**
> "As your profile is currently not verified, we request you to complete the profile verification process. Once your profile is successfully verified, the training plans/APAR courses may reflect in your account.
>
> Kindly recheck the APAR section after the profile verification is completed."

---

## Edge Case 1 — Particular Course Not Visible in CBP Plan / Additional or Unexpected Course Visible

The course name is already in the user's message — extract it directly, do not ask for it.
Match it against `course_name` values case-insensitively and by partial match in either
direction (the user's wording may be contained in the real course name, or vice versa).

**[TOOL CALL]**
```
get_user_cbp_plan(email = <user_email>)
```
Validate whether the named course exists in the user's assigned CBP plan, and read its
`content_id` if it does.

---

### If the course EXISTS in the plan

**[TOOL CALL]**
```
get_user_enrollments(email = <user_email>, content_id = <the plan course's content_id>)
```
Validate whether the course has already been completed.

**Always pass `content_id`** when checking one specific course. Calling
`get_user_enrollments` without it only returns the 20 most recently enrolled courses; an
older enrollment can fall outside that window and get misread as not completed even though
the user has actually finished it.

**`completed: true`.** Resolved. Close. Use EXACTLY this message (fill in `[course_name]`):
> "Upon checking, we found that the course '[course_name]' is already part of your training plan and has been successfully completed.
>
> To view the completed course, please follow the steps below:
> 1. Navigate to the Homepage.
> 2. Locate the My iGOT section.
> 3. Click on the Completed tab to view all completed courses.
> 4. Search for '[course_name]' in the list of completed courses.
>
> You should be able to find and access the completed course there."

Close the conversation politely.

**`completed: false`.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that the course '[course_name]' is already available in your assigned training plan.
>
> To view the mentioned courses, please follow the steps below:
> 1. Navigate to the Homepage and locate the My iGOT section.
> 2. Under My iGOT, click on the APAR tab. Here, you will be able to view all APAR courses, which are marked with a green APAR tag.
> 3. Click on the Upcoming tab to view all upcoming courses, including both APAR and non-APAR courses.
> 4. Click on the All tab to view all assigned courses, including both APAR and non-APAR courses.
> 5. To view completed courses, click on the Completed tab and review the courses that have been completed."

Close the conversation politely.

---

### If NO CBP plan exists at all

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If MDO details are not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

Both tools return the contact's name/email as **masked placeholder tokens** — copy them
exactly as returned; never invent, guess, or paraphrase a name/email yourself. Share Name
and Email only — never Mobile.

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that no CBP/training plan is currently assigned to your profile.
>
> Training plans and courses are assigned by the concerned authority.
>
> Kindly connect with the contact details below for further assistance:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

Close the conversation politely.

**If neither contact is found**, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

### If the course does NOT exist in the plan (a plan exists, just not this course)

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
Fetch MDO Name and Email based on the user's ministry/state/organization. If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```
Same masked-placeholder-token handling as above.

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that the requested course is currently not available in your assigned training/CBP plan.
>
> Training plans and course assignments are managed by the concerned authority.
>
> We request you to kindly connect with the contact details below for further assistance regarding course allocation/addition:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

Close the conversation politely.

**If neither contact is found**, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

**No specific course named in the message** → Follow the STEP 2 pattern (full plan summary with APAR course list).

**Never invent a clickable link/URL for the course** — none of the tools return one; only use the named navigation steps above.

---

## Edge Case 2 — Wrong CBP Plan / Wrong Courses Assigned

**[TOOL CALL]**
```
get_user_profile(email = <user_email>)
```
**One single call.** Use this same result for both the profile-verification check and the
organization/designation/group details below — do not call any other profile-fetching tool
for this Edge Case.

**If profile is NOT verified** → follow STEP 6 (guide profile verification).

**If profile IS verified:**

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```
Masked-placeholder-token handling as above. Share Name and Email only — never Mobile.

**User Message** (fill in `[Department]` with organization from `get_user_profile`; if
either contact found — resolved, close):
> "If you notice that incorrect or unrelated courses are reflecting in your APAR section
> that are not relevant to your designation, we request you to reach out to the MDO Admin
> of your department, as they are responsible for creating and modifying training plans.
>
> Please find the MDO Admin details for your department below:
> **Department:** [Department]
> **MDO Leader Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email Id:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

If neither contact is found, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

## SOP-1 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| CBP plan exists — APAR course list shared | ❌ |
| No plan, State Govt org — O.M. exemption explained | ❌ |
| Mapped to iGOT/Karmayogi Prarambh Trainee — guided to raise Transfer Request | ❌ |
| No plan (Ministry org), contact (MDO or YP) found | ❌ |
| No plan (Ministry org), neither MDO nor YP found | ✅ |
| Profile not verified — guided to verify | ❌ |
| Edge Case 1/2 — contact found | ❌ |
| Edge Case 1/2 — neither MDO nor YP found | ✅ |

---
---

# SOP-2: Comprehensive Assessment Program (CAP) Issues

## Purpose
Handle cases where a user reports that their Comprehensive Assessment Program (CAP) is not
visible, or that they are unable to enroll in / have an eligibility issue with / believe an
incorrect CAP has been assigned.

Covers two subcategories:
- **Comprehensive Assessment Not Visible**
- **Comprehensive Assessment Unable to enroll**

---

## Comprehensive Assessment Not Visible

### STEP 1 — Verify Profile Status

**[TOOL CALL]**
```
get_user_profile(email = <user_email>)
```

**If profile is NOT verified.** Resolved. Close. Use EXACTLY this message:
> "As your profile is currently not verified, we request you to complete the pending profile verification. Once your profile is verified, kindly check the Comprehensive Assessment Program (CAP) again."

**If profile IS verified**, check whether `cadreDetails`, `serviceDetails`, `batch`, and
`centralDeputation` are populated on that same `get_user_profile` result — this is a
background check; never ask the user whether they belong to an All India Service.

**Some of the four fields populated but not all (partially complete).** Resolved. Close.
Use EXACTLY this message:
> "We found that the following mandatory service-related details are missing from your profile:
>
> - Cadre Details
> - Service Details
> - Batch Information
> - Central Deputation Details
>
> Kindly update the required details in your profile under the Other Details section. Once the profile is updated successfully, the APAR plans may reflect in your account within 4 hours."

**All four fields empty, OR all four fields populated** → STEP 1A.

---

### STEP 1A — Check if User Named a Specific Wrong Field

Read the user's own message directly — never ask. If the user explicitly states that a
specific field is wrong (Organization/Department, Designation, Group, Cadre, Service, or
Batch), use the matching update-flow message from SOP-1 STEP 4A (below) — reproduce it
exactly, do not write a different version here.

- Organization/Department incorrect → the **Transfer Request** message
- Designation incorrect → the **Designation Update** message
- Group/Cadre/Service/Batch incorrect → the **Profile Update** message

> **Transfer Request message:**
> "To correct your organization mapping, please raise a Transfer Request:
>
> 1. Log in to iGOT Karmayogi.
> 2. Click on the Profile Icon (top-right corner).
> 3. Click on View Profile.
> 4. Select Make Transfer Request.
> 5. Select the correct Organization Name from the dropdown.
> 6. Choose your Group and Designation (if applicable).
> 7. Ensure all details are accurate and click Submit.
>
> Once approved, kindly recheck the APAR/Training Plan section on your profile."

> **Designation Update message:**
> "To update your designation, please follow the steps below:
>
> 1. Log in to iGOT Karmayogi.
> 2. Click on View Profile from the top-right menu.
> 3. Navigate to Primary Details and click the Edit icon.
> 4. Update the correct Designation.
> 5. Click Send for Approval to submit the changes — the request will be sent to your MDO Admin for approval.
>
> Once approved, kindly recheck the APAR/Training Plan section on your profile."

> **Profile Update message:**
> "To update your Group or other profile details, please follow the steps below:
>
> 1. Log in to iGOT Karmayogi.
> 2. Click on View Profile from the top-right menu.
> 3. Navigate to Primary Details and click the Edit icon.
> 4. Update the correct Group and any other required details.
> 5. Click Send for Approval to submit the changes — the request will be sent to your MDO Admin for approval.
>
> Once approved, kindly recheck the APAR/Training Plan section on your profile."

**If the message does not name a specific field** → STEP 2.

---

### STEP 2 — Verify CAP Assignment

**[TOOL CALL]**
```
get_user_cap_assignment(email = <user_email>)
```

Check whether the user's message names a specific CAP. Match it against `cap_name` values
case-insensitively and by partial match in either direction (same approach as Edge Case 1's
course-name matching).

**Named CAP IS found in assignments.** Resolved. Close. Use EXACTLY this message (copy
`link_html` verbatim for the link — it's already a complete clickable HTML tag, never
rewrite it or use the plain `link` field):
> "Upon checking, we found that the Comprehensive Assessment Program (CAP) '[cap_name]' is already assigned to your profile.
>
> You can access it directly using the link below:
> [link_html]"

**Named CAP is NOT found in assignments** → STEP 3, using the named CAP in the message
instead of a generic reference.

**No specific CAP named:**
- `total_count = 0` → STEP 3 (no CAP assigned at all — valid regardless of which CAP they meant)
- `total_count >= 1` → do **not** assume this is the CAP they meant, even if there's only one — they may be asking about something unrelated to their own assignments. Set `needs_clarification=true`. Use EXACTLY this message (never mention how many CAPs are/aren't assigned — that's internal, not something to tell the user):
> "We understand your concern regarding the visibility of your Comprehensive Assessment Program (CAP).
>
> To help us investigate this further, kindly share the following details:
> - CAP Name
> - CAP Link"

---

### STEP 3 — No CAP Assigned → Share Assigning-Authority Contact

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```
Same masked-placeholder-token handling as SOP-1. Share Name and Email only — never Mobile.

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that no Comprehensive Assessment Program (CAP) is currently assigned to your profile.
>
> CAP creation and assignment are managed by the concerned department.
>
> Kindly connect with the below contact for further assistance regarding CAP creation and assignment:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

**If neither contact is found**, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

## Comprehensive Assessment Unable to Enroll / Eligibility-Related Issues / Incorrect CAP Assigned

Same STEP 1 / STEP 1A logic as "Comprehensive Assessment Not Visible" above (profile
verification, background AIS-field check, named-wrong-field routing) — reused as-is, not
duplicated here.

### Verify CAP Assignment

**[TOOL CALL]**
```
get_user_cap_assignment(email = <user_email>)
```

Same case-insensitive/partial `cap_name` matching as above.

**Named CAP IS found in assignments** — the response now branches on what the user's
message actually reports:

**If the message specifically reports an ENROLLMENT/eligibility error** for this CAP (e.g.
"not eligible to enrol", "unable to enroll", an eligibility error message) — not just
"I can't find/see it":

**[TOOL CALL]**
```
get_access_settings(content_id = <the matched CAP's cap_id>)
```
Returns `accessControl.userGroups[].userGroupCriteriaList[]`, each with `criteriaKey`
(`"rootOrgId"` or `"designation"`) and `criteriaValue`. Empty/no groups → no restriction,
treat as eligible.

For any `criteriaKey: "rootOrgId"` entries:
```
resolve_org_names(org_ids = <all rootOrgId values across all groups>)
```
to get real org names — never show a raw org id. `criteriaKey: "designation"` values are
already plain names, no resolution needed.

Compare against the user's own organization and designation (from `get_user_profile`,
already called this turn).

- **User's own org/designation IS covered** by the CAP's criteria (they should be
  eligible) → the error is likely a technical glitch, not a real restriction. Fall through
  to the default CAP-found message below (do NOT use the eligibility-mismatch message when
  they're actually eligible).
- **User's own org/designation is NOT covered** by any group's criteria → Resolved. Close.
  Use EXACTLY this message (fill in real values; only include the organisations line if
  `rootOrgId` criteria exist, only include the designations line if `designation` criteria
  exist):
> "We would like to inform you that the mentioned program "[NAME OF CAP]" is accessible
> only to users with specific organisations and designations.
>
> Your profile is currently mapped to [User's ORG NAME], and the designation [User's
> DESIGNATION] which is not included in the list of eligible organizations and designations
> configured for this CAP.
>
> List of organisations eligible to access the program: [real org names]
> List of designations eligible to access the program: [real designation names]
>
> Therefore, the message "User is not eligible to enrol in this course" is being displayed.
>
> For any changes in eligibility or access to this CAP, we request you to kindly connect
> with Department's Nodal Officer/MDO Admin who created this CAP, as the access settings
> are managed by the concerned department."

**Otherwise** (not an enrollment/eligibility complaint — e.g. "I can't find/see it") →
Resolved. Close. Use EXACTLY this message (fill in `[CAP NAME]` with the real name, no
link — the user is guided to search for it):
> "As per our check, the CAP assigned to your profile is "[CAP NAME]".
>
> If the same is not visible on your dashboard, we request you to kindly search for the
> [CAP NAME] created by your organization in the search bar and enroll in it.
>
> To unlock the Comprehensive Assessment, you are required to enroll in the Comprehensive
> Assessment Program (CAP) and complete all the courses mapped under the program.
>
> Once all the courses associated with the program are successfully completed, the
> Comprehensive Assessment will be automatically unlocked for you.
>
> If you are still facing any issues, kindly share a screenshot so that we can assist you
> further."

**Named CAP is NOT found in assignments** (the CAP they're asking about is not actually
theirs):

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "CAP assignment is managed by the concerned department.
>
> If you believe that an incorrect Comprehensive Assessment Program (CAP) has been assigned to your profile, kindly connect with your department MDO for further verification and necessary changes.
>
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

**If neither contact is found**, escalate natively (set `escalate=true`). Same escalation
message as above.

**No specific CAP named:**
- `total_count = 0` → No CAP Assigned (below)
- `total_count >= 1` → do not assume this is the CAP they meant. Set `needs_clarification=true`. Use EXACTLY this message:
> "We understand your concern regarding enrolling in your Comprehensive Assessment Program (CAP).
>
> To help us investigate this further, kindly share the following details:
> - CAP Name
> - CAP Link"

### No CAP Assigned → Share Assigning-Authority Contact

**[TOOL CALL]**
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```

**If either contact found.** Resolved. Close. Use EXACTLY this message:
> "Upon checking, we found that no Comprehensive Assessment Program (CAP) is currently assigned to your profile.
>
> Comprehensive Assessment Programs (CAPs) are created and assigned by the concerned department.
>
> Kindly connect with the below contact for further assistance regarding CAP creation or assignment:
> **Name:** [MDO Admin Name from `get_mdo_details` if it found one; otherwise the YP/AM Name from `get_yp_am_details`]
> **Email ID:** [MDO Admin Email from `get_mdo_details` if it found one; otherwise the YP/AM Email from `get_yp_am_details`]"

**If neither contact is found**, escalate natively (set `escalate=true`). Tell the user their
issue has been logged and escalated to the support team, and a specialist will assist them
shortly.

---

## SOP-2 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| Named CAP found (Not Visible flow) — name/link shared | ❌ |
| Named CAP found, enrollment error, user actually eligible — link shared | ❌ |
| Named CAP found, enrollment error, genuinely not eligible — criteria + MDO shared | ❌ |
| Named CAP found (Unable to Enroll flow, non-enrollment complaint) — search guidance shared | ❌ |
| No CAP named, CAPs assigned — clarification requested | ❌ (stays open) |
| No CAP assigned, contact (MDO or YP) found | ❌ |
| No CAP assigned, neither MDO nor YP found | ✅ |
| Named CAP not found, contact (MDO or YP) found | ❌ |
| Named CAP not found, neither MDO nor YP found | ✅ |
| AIS fields partially complete — guided to update | ❌ |

---
---

# SOP-3: Final Assessment Locked in Comprehensive Assessment Program (CAP)

## Purpose
Handle cases where a user reports that the Final Assessment of a Comprehensive Assessment
Program (CAP) is locked or inaccessible.

---

## STEP 1 — Fetch the User's Assigned CAP(s)

**[TOOL CALL]**
```
get_assigned_cap_courses(email = <user_email>)
```
Call this immediately regardless of whether the message names a CAP — do NOT ask the user
to name it first. Asking before checking is pointless when they might have zero CAPs
assigned at all, in which case there'd be nothing to name. Calls the admin "assigned
courses" API (`POST /api/supportportal/admin/user/v2/assignedcourses/{user_id}`, body
`{"courseCategory": "Comprehensive Assessment Program"}`), already filtered to CAPs — no
`isApar` inference needed. `caps` are the user's assigned CAP entries — `plan_id` is the CAP
DO_ID, `course_name` is the CAP name.

**If `found: false`.** Resolved. Close.
> "We could not verify your profile at this time; please try again later."

**Otherwise, route based on `caps`:**

| Condition | Action |
|---|---|
| Empty | → STEP 1A |
| Exactly 1 entry | Use it → STEP 2 |
| Multiple entries, user names a specific CAP that matches exactly 1 (case-insensitive, partial match either direction) | Use that match → STEP 2 |
| Multiple entries, no CAP named or no unique match | `needs_clarification=true` — do NOT guess which one they mean (see message below) |

**User Message (ambiguous, multiple entries)**:
> "Please share the CAP name and CAP link, along with the issue you are facing, so we can
> investigate further."

> No content link/URL is available from this API's response — do not fabricate one. The
> "never invent a link" rule in Constraints still applies; CAP-link sharing remains
> unimplemented pending a link-bearing field or endpoint.

---

## STEP 1A — No CAP Assigned

**[TOOL CALL]**
```
get_user_profile(email = <user_email>)   # designation, organization
```
```
get_mdo_details(email = <user_email>)
```
If not available:
```
get_yp_am_details(ministry_or_state = <user_profile.ministry_or_state>)
```
Masked-placeholder-token handling is identical to SOP-1 STEP 5. Share Name and Email only —
never Mobile.

**If either contact found.** Resolved. Close. Use EXACTLY this message (fill in
`[Designation]` and `[Organization]` with the real values from `get_user_profile`):
> "With reference to your concern regarding the locked Final Assessment in CAP, we would
> like to inform you that there is currently no CAP mapped to the designation
> "[Designation]" under the organization [Organization].
>
> To create and assign the appropriate CAP for your designation, kindly reach out to your
> MDO. Your MDO will create the appropriate CAP for your designation and assign it to your
> profile.
>
> **Name:** [MDO Admin Name, else YP/AM Name]
> **Email:** [MDO Admin Email, else YP/AM Email]"

**If neither found**, escalate natively (`escalate=true`). Tell the user their issue has been
logged and escalated to the support team, and a specialist will assist them shortly.

---

## STEP 2 — Verify CAP Enrollment and Completion

**[TOOL CALL]**
```
get_user_enrollments(email = <user_email>, content_id = <CAP DO_ID from STEP 1>)
```

| Condition | Action |
|---|---|
| `course_name: null` (not enrolled) | → STEP 2A |
| `course_name` present, `completed: false` or `certificate_issued: false` | Enrolled, CAP itself not yet complete → STEP 3 |
| `course_name` present, `completed: true` AND `certificate_issued: true` | The CAP/Final Assessment itself is ALREADY complete → STEP 2B. This check happens regardless of what the user's message claims ("limit exceeded", "locked", etc.) — always verify real completion status first. |

## STEP 2A — CAP Assigned But Not Enrolled

**Outcome:** Resolved. Close.
> "Upon checking, we found that the assigned Comprehensive Assessment Program (CAP)
> '[cap_name]' is not yet enrolled.
>
> Kindly enroll in the assigned CAP to unlock the Final Assessment."

## STEP 2B — CAP Itself Already Completed (Certificate Issued)

**[TOOL CALL]**
```
get_cap_hierarchy(cap_id = <CAP DO_ID from STEP 1>)   # -> assessment_child_id
```
```
get_assessment_attempt_count(email = <user_email>, assessment_identifier = <assessment_child_id if not null, otherwise the CAP DO_ID as fallback>)
```
Do **not** pass the CAP's own DO_ID directly when an `assessment_child_id` exists — the
retake-count API is scoped to the specific Assessment resource, not the CAP container, and
returns a server error if given the wrong id.

**Outcome:** Resolved. Close. Use EXACTLY this message (fill in `[CAP Name]` from STEP 1,
`[Number of Attempts]` from `remaining_attempts`):
> "Upon verification, we found that the CAP [CAP Name] has already been completed, and the
> certificate has also been generated.
>
> However, if you still wish to re-attempt the assessment, you have [Number of Attempts]
> attempts remaining. Kindly open the assessment and retry it as required."

**If `get_assessment_attempt_count` returns `found: false`.** NO ticket. Resolved. Close.
> "We were unable to verify your assessment attempt details at this time. Kindly try again
> in a few minutes."

---

## STEP 3 — Fetch Child Courses

**[TOOL CALL]**
```
get_cap_hierarchy(cap_id = <CAP DO_ID>)
```
`found: false` or empty `children` → Resolved. Close.
> "We could not verify the CAP structure at this time; please try again later."

Each child also carries a derived `resource_type`, classified from its
`primary_category` and `mime_type`:

| `primary_category` | `resource_type` |
|---|---|
| `Course Assessment` | **Assessment** — this child IS the CAP's Final Assessment, not a completion prerequisite. Exclude it from STEP 4's pending check. |
| anything else, `mime_type = application/vnd.ekstep.html-archive` | **SCORM** |
| anything else, any other `mime_type` | **Non-SCORM** |

The tool also returns `assessment_child_id` — the identifier of the `Assessment`
child, or `null` if none was found. Carry this forward for STEP 2B / STEP 5B.

---

## STEP 4 — Validate Child Course Certificate Status

For **each child with `resource_type` of `SCORM` or `Non-SCORM`** from STEP 3
(skip the `Assessment` child, if any — it is validated separately, not
as a completion prerequisite):
```
get_user_enrollments(email = <user_email>, content_id = <child course identifier>)
```
Always pass `content_id` per child course — omitting it only checks the 20 most recently
enrolled courses, which can misreport an older completed course as incomplete.

A child course is **pending** when `course_name` is null (not enrolled), OR
`completed: false`, OR `certificate_issued: false`.

| Condition | Action |
|---|---|
| No child courses pending | → STEP 5 |
| One or more pending | → STEP 4A |

## STEP 4A — Pending Child Courses Found

**Outcome:** Resolved. Close.

Share a table of pending course names + resource type (SCORM / Non-SCORM) + status
(Not enrolled / In progress / Certificate not yet generated), then:

> "We found that the following courses are still pending completion.
>
> The Final Assessment will be unlocked only after all mandatory courses under the CAP are
> completed successfully.
>
> **Steps to identify pending resources within a course:**
> 1. Login to the iGOT Karmayogi portal.
> 2. Navigate to Profile.
> 3. Open My Learning.
> 4. Open the In Progress section.
> 5. Open the respective course.
> 6. Click Resume.
> 7. Expand every module using the "+" icon.
> 8. Identify resources that do not have a Blue Tick.
> 9. Complete all pending resources until every item shows a Blue Tick."

---

## STEP 5 — All Prerequisites Complete, Final Assessment Available

Do NOT ask the user which of the three options describes their issue — combine all
guidance into a single proactive message instead.

**Outcome:** NO ticket. Resolved. Close.
> "Upon checking, we found that all prerequisite courses have been completed successfully
> and the Final Assessment is available.
>
> If you are trying to access the Final Assessment from Mobile, kindly try accessing it
> through the web portal by following the steps below:
> 1. Open Google Chrome.
> 2. Search for iGOT Karmayogi.
> 3. Open the iGOT Karmayogi portal.
> 4. Click the three-dot menu in the browser.
> 5. Enable Desktop Site.
> 6. Login to your account.
> 7. Navigate to Profile → My Learning.
> 8. Open the respective CAP.
> 9. Resume the Final Assessment.
>
> If you continue to face any other issue, kindly share the following so we can
> investigate further:
> - Error Message.
> - Screenshot (if available)."

## STEP 5-FOLLOWUP — User Reports a Further Issue After STEP 5

Reply reports the assessment attempt limit being exceeded → STEP 5B.
Reply describes any other error → STEP 5C.

This branch is only ever reached by a genuine second reply on an already-open ticket
(detected the same way any Zoho continuation reply is) — it cannot be simulated by manually
resubmitting through the test UI with the same ticket id; that does not faithfully reproduce
the real ingestion path. Logically sound and consistent with STEP 2B's pattern, but not
independently verified end-to-end.

## STEP 5B — Assessment Limit Exceeded → Verify Before Ticketing

**[TOOL CALL]**
```
get_assessment_attempt_count(email = <user_email>, assessment_identifier = <assessment_child_id from STEP 3, or the CAP DO_ID from STEP 1 if assessment_child_id was null>)
```
Calls `GET /api/admin/assesment/retake/count?assessmentIdentifier=...&userId=...&editMode=false`
(8s timeout). Returns `found`, `attempts_made`, `attempts_allowed`, `remaining_attempts`
(`attempts_allowed - attempts_made`), `limit_exceeded` (`remaining_attempts <= 0`).

**`found: false`** (API error or fields missing). NO ticket. Resolved. Close.
> "We were unable to verify your assessment attempt details at this time. Kindly try again
> in a few minutes."

**`limit_exceeded: true`.** RAISE TICKET — escalate natively (`escalate=true`) immediately,
no confirmation step.
> "We have verified that the assessment attempt limit has been exceeded. A support ticket
> has been raised and shared with the concerned team for further investigation."

**`limit_exceeded: false`.** NO ticket. Resolved. Close. Fill in `[Number of Attempts]` from
`remaining_attempts`, `[CAP Name]` from STEP 1:
> "Upon verification, we found that you still have [Number of Attempts] attempts remaining
> for the CAP [CAP Name], and the assessment is currently incomplete. Kindly retry the
> assessment and complete it."

## STEP 5C — Any Other Error

**Outcome:** RAISE TICKET — escalate natively (`escalate=true`), using the user's own
description of the error already in their message as the reason, plus a screenshot if
they've shared or offered one.
> "We have captured the reported issue and raised a support ticket for further
> investigation."

Close the conversation politely.

---

## SOP-3 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| Multiple CAPs assigned, unclear which one (STEP 1 clarifying question) | ❌ |
| No CAP assigned, contact (MDO or YP) found | ❌ |
| No CAP assigned, neither MDO nor YP found | ✅ |
| CAP not enrolled | ❌ |
| CAP itself already completed (certificate issued) | ❌ |
| Pending child courses | ❌ |
| All child courses complete and certified | ❌ |
| Attempt-count check failed (API error) | ❌ |
| Assessment limit actually exceeded (verified) | ✅ |
| Assessment attempts still remaining (verified) | ❌ |
| Any other reported error at the Final Assessment | ✅ |
