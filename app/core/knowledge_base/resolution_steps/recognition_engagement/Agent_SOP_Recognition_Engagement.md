# Agent SOP — Recognition & Engagement Issues Resolution
**Platform:** iGOT Karmayogi | **Agent Framework:** LangGraph
**Applies to:** L1 AI Agent — Karma Points, Weekly Claps, Learning Hours, and Leaderboard Use Cases

---

## Global Agent Principles
- **Tool-first, ask-last.** Fetch relevant status via API immediately — before asking the user anything.
- **Single-pass diagnosis.** Run all relevant API calls upfront and deliver one complete, informed response.
- Be empathetic, concise, and professional in every response.

---

## Tool Registry

| Tool | Signature | Purpose |
|------|-----------|---------|
| `get_completed_courses` | `(email)` | Fetch the user's 5 most recent completed courses — fallback list when no course is named in the ticket (SOP-RE1 Flow A STEP 0) |
| `get_completed_events` | `(email)` | Fetch the user's 5 most recent completed events, each pre-annotated with `hours_since_start` / `is_live_participation` — fallback list + timing check (SOP-RE1 Flow B STEP 0/1) |
| `get_karma_course_status` | `(email, course_id)` | Fetch a course's karma credit status (`completion_credited`, `rating_credited`, `completion_points`, `rating_points`, `acbp`, `has_assessment`, `monthly_rank`) from `/api/karmapoints/read` (SOP-RE1 Flow A STEP 1-3, Edge Case 1) |
| `get_karma_event_status` | `(email, event_id)` | Fetch whether karma points were credited for a specific event (SOP-RE1 Flow B STEP 1) |
| `get_user_ehrms_details` | `(email)` | Fetch `ehrms_id` / `external_system_name` from `profileDetails.additionalProperties` via the User Search API (SOP-RE3 STEP 1) |
| `get_mdo_details` | `(email)` | Fetch the user's MDO Admin contact (Organization, Name, Email) when eHRMS mapping data is missing (SOP-RE3 STEP 1, SOP-RE5 STEP 4.2) |
| `get_user_first_name` | `(email)` | Fetch just the user's first name for the email greeting — no diagnostic value (SOP-RE3/RE4) |
| `get_user_profile` | `(email)` | Fetch org (`rootOrgName`/`channel`), verification status, designation, and group — reused from Profile Update tools (SOP-RE5 STEP 1) |
| `get_user_cbp_plan` | `(email)` | Fetch the user's CBP/training plan, returning `apar_count`/`apar_plans` — reused from CA/APAR tools (SOP-RE5 STEP 4.2) |
| `get_yp_am_details` | `(ministry_or_state)` | Fetch YP/AM contact when no MDO is found — reused from CA/APAR tools (SOP-RE5 STEP 4.2) |
| `get_cap_hierarchy` | `(cap_id)` | Fetch a CAP's child courses, classifying each as Assessment/SCORM/Non-SCORM — reused from CA/APAR SOP-3 tools (SOP-RE5 STEP 4.5) |
| `get_user_enrollments` | `(email, content_id)` | Fetch a course's completion/certificate status — reused from CA/APAR tools (SOP-RE5 STEP 4.5) |
| `get_sparrow_training_data_video` | `()` | Static resource, no API call — returns the "How to Fetch iGOT Training Data into SPARROW" reference video's title and pre-built clickable HTML link (SOP-RE5 STEP 4.5, completed branch) |
| `get_user_cadre_details` | `(email)` | Fetch All India Service (IAS/IPS/IFS) cadre details from `profileDetails.cadreDetails` via the User Search API (SOP-RE5 STEP 6) |
| `get_weekly_clap_status` | `(email)` | Fetch 12 weeks of platform time-spent and diagnose a weekly-clap reset — precomputes the w1→w12 threshold scan (SOP-RE2 STEP 1/2) |

---
---

# SOP-RE1: Karma Points Issue

Adapted from the original chatbot SOP for ticket resolution (single-pass, ask-once-then-close
instead of a multi-turn chat loop; "raise a ticket" maps to `escalate=true` + reason on the
already-open ticket — there is no separate ticket-creation mechanism in this codebase).

## Flow A — Course karma points not credited

**STEP 0 — Identify the course.** If the ticket message already names a course, use it
directly. Otherwise call `get_completed_courses`, share the list, and ask the user to
confirm (ask once — `needs_clarification=true`, stop; if the reply still doesn't name a
course, close unresolved). Match case-insensitively / partially against the results.

**STEP 1 — Credit status.** `get_karma_course_status(email, course_id)`.

| completion_credited | rating_credited | Action |
|---|---|---|
| true | true | Resolved — already credited. Close. |
| false | false | **escalate=true** immediately — no completion or rating credit at all. Skip STEP 2/3. |
| false | true | → STEP 2 |
| true | false | Resolved — treated the same as "both credited." No ticket. |

**STEP 2 — Training Plan check.** Read `acbp`.
- `true` → **escalate=true** immediately (Training Plan course, completion points missing).
- `false` → STEP 3.

**STEP 3 — Monthly cap check.** Read `monthly_rank` (already computed by the tool — counts
only non-Training-Plan course completions in the same calendar month).
- `>= 5` → Resolved — only the first 4 completed courses/month are eligible. Close.
- `<= 4` (or unavailable) → **escalate=true** — discrepancy.

## Flow B — Event karma points not credited

**STEP 0 — Identify the event.** Same ask-once pattern as Flow A STEP 0, via
`get_completed_events`.

**STEP 1 — Timing + credit check.** Read `is_live_participation` /
`hours_since_start` from the matched event (≤4 hours from the event's start counts as live
participation — covers both "during the event" and shortly after). If absent (start time
unknown), skip straight to the credited check.
- `is_live_participation = false` → Resolved — points are only credited for live
  participation. Close.
- `is_live_participation = true` (or unavailable) → `get_karma_event_status(email,
  event_id)`.
  - `credited = true` → Resolved — already credited. Close.
  - `credited = false` → **escalate=true** — live participation confirmed, points missing.

## Edge Case 1 — Incorrect karma points (5 vs 10 vs 15)

Course identification same as Flow A STEP 0. `get_karma_course_status(email, course_id)`.

Expected points: `acbp=true, has_assessment=true` → 15. `acbp=true, has_assessment=false`
→ 10. `acbp=false` → 5 (subject to the same monthly-cap check as Flow A STEP 3, via
`monthly_rank` from the same result).

- `completion_points` matches expected → Resolved — points are correct. Close.
- Mismatch, `acbp=true` → **escalate=true**.
- Mismatch, `acbp=false`, `monthly_rank <= 4` → **escalate=true**.
- Mismatch, `acbp=false`, `monthly_rank >= 5` → Resolved — first-4-per-month rule explained.

## Edge Case 2 — Leaderboard vs overall karma points mismatch

No tools. Resolved — informational close: Leaderboard/Top Karmayogi shows last month's
points only; Overall/Individual shows cumulative points since joining. Expected to differ.

## Edge Case 3 — Learner pathway points (+25)

**Not implemented.** The source SOP itself flags this as pending Product Team
clarification on whether the monthly limit applies within pathways, and no tool exists to
check a pathway-specific monthly limit. **escalate=true** — same treatment as CA/APAR
SOP-3's unimplemented branches (skipped rather than half-implemented), pending
clarification.

## Edge Case 4 — Course already completed, added later to a Training Plan

No tools. Resolved — informational close: guide the user to the course's TOC page →
"Claim Karma Point" button.

---

## SOP-RE1 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| Both completion + rating credited | ❌ |
| Neither credited at all | ✅ |
| Only completion credited, rating not | ❌ |
| Only rating credited, Training Plan course | ✅ |
| Only rating credited, not Training Plan, `monthly_rank<=4` | ✅ |
| Only rating credited, not Training Plan, `monthly_rank>=5` | ❌ |
| Course/event name not confirmed after one ask | ❌ (closed unresolved) |
| Event live (≤4h) and credited | ❌ |
| Event live (≤4h) and not credited | ✅ |
| Event not live (>4h) | ❌ |
| Edge Case 1 — points match expected | ❌ |
| Edge Case 1 — mismatch, Training Plan | ✅ |
| Edge Case 1 — mismatch, not Training Plan, `rank<=4` | ✅ |
| Edge Case 1 — mismatch, not Training Plan, `rank>=5` | ❌ |
| Edge Case 2 — leaderboard vs overall | ❌ |
| Edge Case 3 — learner pathway | ✅ (pending clarification) |
| Edge Case 4 — claim button | ❌ |

---
---

# SOP-RE2: Weekly Claps Issue

Adapted from the UC-WC chatbot SOP / API Integration Guide (single-pass, ask-once-then-close
instead of a multi-turn chat loop; "raise a Zoho ticket" maps to `escalate=true` + reason on
the already-open ticket). Covers all three ticket phrasings — "not updated", "reset to
zero", "not credited" — as one flow: they all resolve via the same 12-week scan.

**STEP 1 — Fetch and diagnose.** `get_weekly_clap_status(email)`. The tool internally:
fetches the user's `rootOrgId` (via the private user search API), then calls
`/api/chatbot/v2/insights` for 12 weeks of platform time spent (`w1` = most recent week,
`w12` = oldest available), and scans `w1 → w12` for the first week with `minutes < 60` (the
60-minute weekly threshold that maintains a clap). Returns one of four `status` values.

| status | Meaning | Action |
|---|---|---|
| `not_found` | User profile could not be resolved | Resolved — inform the user their profile could not be verified; no ticket. |
| `no_activity_data` | Insights API 404, or all 12 weeks null | Resolved — inform the user no activity data was found; no ticket. |
| `api_error` | Insights API failed/timed out | Resolved — ask the user to retry later; no ticket. |
| `reset_found` | First week `< 60` min found (`reset_week`) | → STEP 2 |
| `discrepancy` | All 12 weeks `>= 60` min | **escalate=true** — "Weekly Clap Discrepancy — Threshold Met But Clap Not Credited". |

**STEP 2 — Explain the reset / detect disagreement.** On `reset_found`, tell the user the
exact week (`reset_week.label`) and minutes spent (`reset_week.minutes`), and that the
60-minute weekly rule caused the reset. Since this system is single-pass (no live
back-and-forth), "the user disagrees" is inferred from the ticket message itself — not a
follow-up turn:
- Ticket message reads as a plain report (no prior explanation referenced/disputed) →
  Resolved. Close. No ticket.
- Ticket message itself already disputes/rejects a prior reset explanation (e.g. a
  reopened ticket, or text explicitly rejecting the 60-minute rule as applied to them) →
  **escalate=true** — "Weekly Clap Issue — User Disputes Reset Explanation".

## Edge Case — Streak older than 12 weeks

The Insights API covers only the last 12 weeks. If the user's claimed clap streak predates
that window, the reset point is outside the available data and `get_weekly_clap_status`
will report `status: "discrepancy"` (no week `< 60` found in the 12 it can see) — handled
identically to STEP 1's discrepancy branch. No separate tool support exists for tracing
further back.

---

## SOP-RE2 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| `not_found` — profile unresolved | ❌ |
| `no_activity_data` — 404 or all weeks null | ❌ |
| `api_error` — insights API failed | ❌ |
| `reset_found`, ticket reads as plain report | ❌ |
| `reset_found`, ticket disputes a prior explanation | ✅ |
| `discrepancy` — all 12 weeks ≥ 60 min | ✅ |
| Streak older than 12 weeks (surfaces as `discrepancy`) | ✅ |

---
---

# SOP-RE3: Learning Hours Issue - eHRMS

Covers reports that learning progress, course completion, training plans, assessments, or
learning records are not reflecting in eHRMS.

**STEP 1.** `get_user_ehrms_details(email)`. Route on `ehrms_id` / `external_system_name`:

| ehrms_id | external_system_name | Action |
|---|---|---|
| present | present | Resolved. Close. Not an iGOT-side issue — direct the user to eHRMS's own support team; they should be ready to share their Name, Email, and eHRMS ID with that team (information for them to bring, not something to ask the user for here). |
| missing | — | `get_mdo_details(email)`. MDO found → Resolved. Close — eHRMS ID can only be updated by the org's MDO, not the user; share the MDO contact; note up to 24h for the sync to reflect. MDO not found → **escalate=true** — no MDO Admin found for the org. |
| present | missing | `get_mdo_details(email)`. MDO found → Resolved. Close — External System Name not updated; mandatory field, MDO/Admin-only; share MDO contact; note up to 24h for the sync to reflect. MDO not found → **escalate=true** — no MDO Admin found for the org. |

`get_mdo_details` returns the contact as masked placeholder tokens (`{{MDO_ADMIN_NAME}}`,
`{{MDO_ADMIN_EMAIL}}`) — copy them exactly as returned, never invent a name/email.

## SOP-RE3 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| Both eHRMS ID and External System Name present | ❌ |
| eHRMS ID missing, MDO found | ❌ |
| eHRMS ID missing, MDO not found | ✅ |
| External System Name missing (eHRMS ID present), MDO found | ❌ |
| External System Name missing (eHRMS ID present), MDO not found | ✅ |

---
---

# SOP-RE4: Learning Hours Issue - Shiksha Path

No tool calls, no eligibility checks — Shiksha Path is not managed or operated by
Karmayogi Bharat or DoPT.

**STEP 1.** Resolved. Close. Tell the user Shiksha Path is managed by the Directorate of
Training, CBDT; Karmayogi Bharat and DoPT do not manage or operate the Shiksha Path portal;
ask them to coordinate directly with the Directorate of Training / CBDT team for any
Shiksha Path-related issues; share the support email `aed4.training@incometax.gov.in`.

---
---

# SOP-RE5: Learning Hours Issue - SPARROW / APAR

Covers reports that APAR training/completion data is not reflecting in SPARROW/APAR. Reuses
CA/APAR and Profile Update tools rather than adding new ones — the underlying data (profile,
CBP/training plan, enrollments, MDO/YP contacts) is the same.

**STEP 1.** `get_user_profile(email)` — also the greeting-name source. Read
`rootOrgName`/`channel` (organization) and `profileDetails.profileStatus` (verification).

**STEP 2 — Organization mapping.** Mapped to "iGOT" or "Karmayogi Prarambh Trainee"
(placeholder org) → Resolved. Close — APAR courses/training plans are only assigned once
mapped to the correct department; ask the user to raise a Transfer Request. Otherwise → STEP 3.

**STEP 3 — Verification status** (same STEP 1 result). Not verified → STEP 6. Verified → STEP 4.

**STEP 4 — Verified Profile Flow.**
- 4.1: Organization/Designation/Group are for internal routing only (STEP 2's org-mapping
  check) — do not restate them to the user or ask for confirmation; keep the response
  focused only on the course/assessment/CAP status. Proceed directly to 4.2.
- 4.2: `get_user_cbp_plan(email)`. Read `apar_count`/`apar_plans`.

| apar_count | Action |
|---|---|
| 0 | `get_mdo_details(email)`. MDO found → Resolved. Close — share MDO contact; no APAR/CAP currently assigned; only APAR-assigned + completed courses reflect in SPARROW/APAR. MDO not found → `get_yp_am_details(ministry_or_state)`. YP/AM found → Resolved. Close — same message, share YP/AM contact. Neither found → **escalate=true**. |
| > 0, course named in ticket, no match in `apar_plans` | Resolved. Close — tell the user the named course is not among their currently assigned APAR courses. Add, formally: please ensure all courses/assessments assigned under their APAR/CAP plans are completed; it may take up to 24 hours for completion data to reflect in SPARROW. |
| > 0, course named in ticket, matched to one `apar_plans` entry | `get_cap_hierarchy(cap_id=content_id)` — same tool as CA/APAR SOP-3, used only to locate the Assessment child (`assessment_child_id`); prerequisite SCORM/Non-SCORM children are out of scope here. No Assessment child (flat course) → `get_user_enrollments(email, content_id)` on the course directly. Assessment child found → `get_user_enrollments(email, content_id=assessment_child_id)` — check the CAP assessment's own completion, not the top-level course. Not completed → Resolved. Close — complete/reattempt and pass the course/assessment. Completed → `get_sparrow_training_data_video()` — static resource, returns the reference video's title + pre-built clickable link (`link_html`). Resolved. Close — share it. No completion-timestamp field is available from any tool, so there's no 24h distinction — completed is completed, regardless of when. |
| > 0, no course named in ticket | Resolved. Close — generic message: APAR course(s) assigned, all courses + assessments must be completed to reflect in SPARROW, and it may take up to 24 hours for completion data to reflect. As its own separate paragraph, keep it short: ask the user to share the course name and link they're facing an issue with, so it can be investigated further — no "one particular course" or "even after completing it" qualifiers. |

**STEP 5 — Incorrect Profile Details.** Triggered when the ticket message itself describes a
wrong field (not a missing-mapping case). Check the ticket text for which field: Organization
wrong → same Transfer Request guidance as STEP 2. Designation wrong → Resolved. Close — raise a
Designation Update request. Group/other → Resolved. Close — use Profile Update. Always tell the
user to recheck APAR courses/training plans after the update. Field not identifiable from the
ticket → `needs_clarification=true`.

**STEP 6 — Profile Not Verified.** `get_user_cadre_details(email)` checks All India Service
(IAS/IPS/IFS) eligibility entirely in the background — never ask the user. Some of
`cadre_name`/`service_details`/`batch_details`/`central_deputation` populated but not all →
Resolved. Close — tell the user some mandatory service-related details are missing/incomplete
(Cadre Details, Service Details, Batch Information, Central Deputation Details); ask them to
update; APAR plans may reflect within 2-3 hours after update. All four empty, or all four
populated → Resolved. Close — complete profile verification; APAR courses/training plans may
reflect once verified.

## SOP-RE5 Outcome Rules — Quick Reference

| Scenario | Escalate? |
|----------|:-------------:|
| Mapped to iGOT/Karmayogi Prarambh Trainee (placeholder org) | ❌ |
| Not verified, cadre fields partially complete | ❌ |
| Not verified, cadre fields all empty or all complete | ❌ |
| APAR/CAP not assigned, MDO or YP/AM found | ❌ |
| APAR/CAP not assigned, neither MDO nor YP/AM found | ✅ |
| Named course not among assigned APAR courses | ❌ |
| Named course matched, not completed | ❌ |
| Named course matched, completed (video shared) | ❌ |
| No course named, APAR assigned (generic guidance) | ❌ |
| Incorrect profile detail, field identified from ticket | ❌ |
| Incorrect profile detail, field not identified | (needs_clarification) |

---
---

# SOP-RE6: Leader Board Issue

**Status:** Not yet defined — placeholder.
