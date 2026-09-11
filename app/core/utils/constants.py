"""
app/core/utils/constants.py
----------------------------
Application-wide constants, feature flags, LLM model selectors,
and the customer-facing email HTML template.

Feature flags are read from settings (pydantic-settings) so they can be
toggled via environment variables without code changes.
"""
from app.core.utils.config import settings


# ── Feature Flags ──────────────────────────────────────────────────────────────
VALIDATE_EMAIL = settings.VALIDATE_EMAIL
RESTRICT_TO_EMAIL_CHANNEL = settings.RESTRICT_TO_EMAIL_CHANNEL
ENABLE_ZOHO_TICKET_UPDATE = settings.ENABLE_ZOHO_TICKET_UPDATE

# List of enabled ticket categories. Tickets classified under any category not in this
# list will gracefully skip execution without executing subgraphs or updating Zoho Desk.
ENABLED_CATEGORIES: list[str] = [
    "ca_apar_issue"
]

# ── Email HTML Template ────────────────────────────────────────────────────────
# Used by all graph nodes that produce a final customer-facing email response.
# `name`  : user's first name (use "User" when name is unknown)
# `body`  : HTML fragment produced by the LLM (<p>, <ul>, <ol>, <li>, <strong>, <br>)
EMAIL_HTML_TEMPLATE = """<html><body><p>Hi {name},</p><p>Greetings from Karmayogi Bharat Support.</p><div>{body}</div><br><p>Regards,<br>Support Team<br>Karmayogi Bharat</p></body></html>"""


def build_email_html(body: str, name: str = "User") -> str:
    """
    Wrap *body* (an HTML fragment) in the standard Karmayogi email template.

    Args:
        body : HTML content for the <div> section (paragraphs, lists, etc.)
        name : User's first name. Defaults to "User" when unknown.

    Returns:
        A complete HTML document string.
    """
    greeting_name = name.strip() if name and name.strip() else "User"
    # Guard against double-wrapping
    if body.strip().lower().startswith("<!doctype") or body.strip().startswith("<html"):
        return body
    return EMAIL_HTML_TEMPLATE.format(name=greeting_name, body=body.strip())


# ── Intake Node — Categories ───────────────────────────────────────────────────

FALLBACK_CATEGORIES = [
    "certificate", "course", "program", "login_issue",
    "profile_update", "user_service_request", "ca_apar_issue",
    "organisation_request", "mobile_application", "virtual_event",
    "general",
]

JUNK_CONFIDENCE_THRESHOLD = 0.75


# ── Intake Node — Pre-built HTML response bodies ──────────────────────────────
# These are HTML *fragments* (no <html>/<body> wrappers) for use with
# build_email_html().  Placeholders use .format() syntax.

# Domain not whitelisted — {email} and {domain} filled at call-site
DOMAIN_INVALID_HTML_BODY = (
    "<p>Thank you for reaching out to the iGOT Karmayogi support team.</p>"
    "<p>We noticed that your query was submitted from <strong>{email}</strong>, "
    "which is associated with the domain <strong>{domain}</strong>. "
    "Unfortunately, this domain is not registered with the iGOT Karmayogi platform.</p>"
    "<p>Only users with government-approved email domains can raise support requests "
    "through this channel. If you believe this is a mistake, please contact your "
    "organisation&#39;s nodal officer or reach out to the platform administrator.</p>"
    "<p>This ticket has been closed.</p>"
)

# Junk / unrecognised message — no placeholders
JUNK_HTML_BODY = (
    "<p>Thank you for contacting iGOT Karmayogi Support.</p>"
    "<p>We were unable to identify a valid support request in your message. "
    "If you have a specific query or issue related to the platform, please write to us "
    "with more details and we will be happy to assist you.</p>"
    "<p>This ticket has been closed.</p>"
)

# Unregistered email — {email} filled at call-site
UNREGISTERED_HTML_BODY = (
    "<p>We were unable to find an account associated with <strong>{email}</strong> "
    "on the iGOT Karmayogi platform.</p>"
    "<p>If you have registered with a different email address, please resubmit "
    "your query using that registered email ID.</p>"
    "<p>If you believe this is an error or need help with registration, "
    "please visit the iGOT Karmayogi portal or contact your organisation&#39;s admin.</p>"
)
# ── LLM Models ────────────────────────────────────────────────────────────────

# Define the models we use across the graph
MODEL_LITE      = "gemini-2.5-flash"
MODEL_REASONING = "gemini-3.5-flash"  # Set to flash for now, can be changed to gemini-1.5-pro or gemini-2.5-pro
MODEL_NO_THINKING = "gemini-2.5-flash"

class GraphStage:
    JUNK_CLASSIFICATION = "junk_classification"
    TICKET_ROUTING      = "ticket_routing"
    SUBGRAPH_PLANNING   = "subgraph_planning"
    SUBGRAPH_EXECUTION  = "subgraph_execution"
    SUBGRAPH_DECISION   = "subgraph_decision"
    QUALITY_GATE        = "quality_gate"
    TICKET_STORE        = "ticket_store"

# ── LLM Model Mapping ─────────────────────────────────────────────────────────
# Maps graph stage/node actions to the specific LLM model to use
LLM_MODEL_MAP = {
    GraphStage.JUNK_CLASSIFICATION: MODEL_LITE,
    GraphStage.TICKET_ROUTING:      MODEL_LITE,
    GraphStage.SUBGRAPH_PLANNING:   MODEL_REASONING,
    GraphStage.SUBGRAPH_EXECUTION:  MODEL_NO_THINKING,
    GraphStage.SUBGRAPH_DECISION:   MODEL_REASONING,
    GraphStage.QUALITY_GATE:        MODEL_LITE,
    GraphStage.TICKET_STORE:        MODEL_LITE,
}

def get_llm_model(stage: str) -> str:
    """Returns the model string for a given graph stage, defaulting to MODEL_LITE."""
    return LLM_MODEL_MAP.get(stage, MODEL_LITE)
