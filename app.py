"""Ghana Tier 2 Baseline Dashboard on Streamlit.

Two data sources:
  1. KoboToolbox directly (the six GH_T2 forms) – used when KOBO_TOKEN is set in Secrets.
     Names and phone numbers are removed here, on the server, before anything reaches the browser.
  2. The Google Sheet "Ghana Tier 2 – Dashboard Data" (the older four forms) – used otherwise.
     If SHEET_ID is set, its config_* tabs (sentinel register, actions, readiness, settings) are
     also read in Kobo mode.

Secrets (Streamlit Cloud → App settings → Secrets):
    KOBO_TOKEN  = "your Kobo API key"            # Kobo → Account settings → Security → API key
    KOBO_SERVER = "https://kf.kobotoolbox.org"   # optional
    [KOBO_FORMS]                                  # project IDs from each project's web address
    attendance = "…"   screening = "…"   baseline = "…"
    contact    = "…"   qa        = "…"   backcheck = "…"
    feedback   = "…"   # optional: the existing Feedback Log form, keeps the Feedback page
    SHEET_ID = "…"     GCP_SERVICE_ACCOUNT_JSON = '''{ … }'''   # optional in Kobo mode
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import gspread
import requests
import streamlit as st
import streamlit.components.v1 as components

REFRESH_SECONDS = 60
HERE = Path(__file__).parent

st.set_page_config(page_title="Ghana Tier 2 Baseline Dashboard", page_icon="📊", layout="wide",
                   initial_sidebar_state="collapsed")

# Let the dashboard fill the window: hide Streamlit's own header and padding.
st.markdown("""
<style>
  header[data-testid="stHeader"], footer, #MainMenu {display: none !important;}
  .block-container, [data-testid="stMainBlockContainer"] {padding: 0 !important; max-width: 100% !important;}
  [data-testid="stAppViewContainer"] {background: #f7f9f8;}
  iframe {height: calc(100vh - 6px) !important; height: calc(100dvh - 6px) !important; border: 0;}
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------------------- data
def _service_account_info() -> dict:
    if "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
        return json.loads(st.secrets["GCP_SERVICE_ACCOUNT_JSON"])
    return dict(st.secrets["gcp_service_account"])


def _sheet_id() -> str:
    """Accepts the bare ID or the whole Sheet address (…/spreadsheets/d/<ID>/edit…)."""
    value = str(st.secrets["SHEET_ID"]).strip().strip('"\'')
    match = re.search(r"/d/([A-Za-z0-9_-]+)", value)
    return match.group(1) if match else value


@st.cache_resource(show_spinner=False)
def _spreadsheet():
    client = gspread.service_account_from_dict(_service_account_info())
    return client.open_by_key(_sheet_id())


def _records(values: list) -> list:
    """Sheet rows → list of {column: text}. Skips empty rows and keeps one row per Kobo
    submission ID (_uuid), the last one written, so a duplicated row is never counted twice."""
    if len(values) < 2:
        return []
    header = [str(h).strip() for h in values[0]]
    rows, seen = [], {}
    for row in values[1:]:
        if not any(str(v).strip() for v in row):
            continue
        row = list(row) + [""] * (len(header) - len(row))
        rec = {h: str(v).strip() for h, v in zip(header, row) if h}
        key = rec.get("_uuid")
        if key and key in seen:
            rows[seen[key]] = rec
            continue
        if key:
            seen[key] = len(rows)
        rows.append(rec)
    return rows


# ------------------------------------------------------------ KoboToolbox
KOBO_FORM_KEYS = ["attendance", "screening", "baseline", "contact", "qa", "backcheck",
                  "feedback"]   # feedback = the existing GT2 Feedback Log form (optional)

# Fields that identify a person. They are dropped before data leaves this server.
PII_FIELDS = {
    "attendance": {"att_name", "att_phone"},
    "contact": {"con_name", "con_phone", "con_phone_confirm", "con_alt_name", "con_alt_phone",
                "con_alt_relationship", "fu_new_phone", "fu_note"},
    # free-text notes that could mention a farmer; the dashboard does not display them
    "screening": {"cons_questions", "scr_hold_note", "scr_disposition_note", "scr_source_approval",
                  "sup_query_log", "sup_notes", "scr_repl_reason"},
    "baseline": {"qa_enum_comments", "qa_discomfort_note"},
    "qa": {"inc_description", "qa_issue", "qa_query", "qa_enum_response", "k_corrections",
           "cl_recovery", "cl_escalations"},
    "backcheck": {"bc_key_result", "bc_occurred_note", "bc_consent_note", "bc_respondent_note",
                  "bc_vaccination_note", "bc_benefit_note", "bc_concern_note"},
}
# Kobo/system fields the dashboard never needs (may hold usernames, files or locations).
DROP_ALWAYS = {"_attachments", "_geolocation", "_submitted_by", "username", "deviceid", "audit",
               "_notes", "_tags", "_status", "formhub/uuid", "meta/rootUuid", "__version__", "_xform_id_string"}
PHONE_RX = re.compile(r"(?<!\d)(?:\+?233|0)\d{9}(?!\d)")
QA_STATUS = {"validation_status_approved": "approved", "validation_status_not_approved": "not_approved",
             "validation_status_on_hold": "on_hold"}


def kobo_mode() -> bool:
    return "KOBO_TOKEN" in st.secrets


def _kobo_server() -> str:
    return str(st.secrets.get("KOBO_SERVER", "https://kf.kobotoolbox.org")).rstrip("/")


def _kobo_forms() -> dict:
    forms = dict(st.secrets.get("KOBO_FORMS", {}))
    return {k: str(forms[k]).strip() for k in KOBO_FORM_KEYS if str(forms.get(k, "")).strip()}


def _fetch_kobo(uid: str) -> list:
    """All submissions of one Kobo project (follows pagination)."""
    url = f"{_kobo_server()}/api/v2/assets/{uid}/data/?format=json&limit=1000"
    headers = {"Authorization": f"Token {st.secrets['KOBO_TOKEN']}"}
    out = []
    while url:
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        out.extend(body.get("results", []))
        url = body.get("next")
    return out


def _flatten(value, out: dict) -> None:
    """Kobo keys are group paths ('sec_c/flk_c01_g/flk_c01_chicks'); the dashboard uses the last part."""
    for key, val in value.items():
        short = key.rsplit("/", 1)[-1]
        if isinstance(val, list) and val and isinstance(val[0], dict):   # repeat group → list of flat rows
            rows = []
            for item in val:
                row = {}
                _flatten(item, row)
                rows.append(row)
            out[short] = rows
        elif isinstance(val, (dict, list)):
            continue
        else:
            out[short] = val


def _clean(form: str, raw: dict, salt: str) -> dict:
    rec = {}
    _flatten({k: v for k, v in raw.items() if k not in DROP_ALWAYS and k != "_validation_status"}, rec)
    if form == "contact":   # keep a one-way code of the phone so duplicate farmers can be spotted
        phone = re.sub(r"\D", "", str(raw.get("contact/con_phone") or rec.get("con_phone") or ""))
        if phone:
            rec["_phone_hash"] = hashlib.sha256((salt + phone[-9:]).encode()).hexdigest()[:16]
    for field in PII_FIELDS.get(form, set()) | DROP_ALWAYS:
        rec.pop(field, None)
    for key, val in list(rec.items()):   # scrub phone numbers typed into any free-text answer
        if isinstance(val, str) and PHONE_RX.search(val):
            rec[key] = PHONE_RX.sub("[number removed]", val)
    vs = raw.get("_validation_status") or {}
    uid = (vs.get("uid") or "") if isinstance(vs, dict) else str(vs or "")
    rec["_qa_status"] = QA_STATUS.get(uid, "pending")
    rec["_kobo_validation"] = uid or "none"      # shown in the dashboard's Data check panel
    return rec


def _config_from_sheet() -> dict:
    empty = {"sentinels": [], "actions": [], "readiness": [], "settings": {}}
    if "SHEET_ID" not in st.secrets:
        return empty
    tabs = {ws.title: _records(ws.get_all_values()) for ws in _spreadsheet().worksheets() if ws.title.startswith("config_")}
    return {
        "sentinels": tabs.get("config_sentinels", []),
        "actions": tabs.get("config_actions", []),
        "readiness": tabs.get("config_readiness", []),
        "settings": {r["key"]: r.get("value", "") for r in tabs.get("config_settings", []) if r.get("key")},
    }


def load_kobo_payload() -> dict:
    forms_cfg = _kobo_forms()
    if not forms_cfg:
        raise KeyError("KOBO_FORMS (add the six project IDs)")
    salt = hashlib.sha256(str(st.secrets["KOBO_TOKEN"]).encode()).hexdigest()[:12]
    forms = {name: [_clean(name, r, salt) for r in _fetch_kobo(uid)] for name, uid in forms_cfg.items()}
    try:
        config = _config_from_sheet()
    except Exception:          # the Sheet is optional in Kobo mode
        config = {"sentinels": [], "actions": [], "readiness": [], "settings": {}}
    now = datetime.now(timezone.utc).isoformat()
    return {"schema": "v2", "generatedAt": now, "lastWebhook": now, "lastScheduled": now, "forms": forms, "config": config}


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def load_payload() -> dict:
    """Same shape as getDashboardData() in the Apps Script, so the dashboard reads either source."""
    test_file = os.environ.get("GT2_TEST_PAYLOAD")          # local testing only
    if test_file:
        return json.loads(Path(test_file).read_text(encoding="utf-8"))
    if kobo_mode():
        return load_kobo_payload()

    tabs = {ws.title: _records(ws.get_all_values()) for ws in _spreadsheet().worksheets()}
    log = tabs.get("sync_log", [])

    def last_ok(source):
        for r in reversed(log):
            if r.get("source") == source and not r.get("error"):
                return r.get("time", "")
        return ""

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "lastWebhook": last_ok("webhook"),
        "lastScheduled": last_ok("scheduled"),
        "forms": {k: v for k, v in tabs.items() if not k.startswith("config_") and k != "sync_log"},
        "config": {
            "sentinels": tabs.get("config_sentinels", []),
            "actions": tabs.get("config_actions", []),
            "readiness": tabs.get("config_readiness", []),
            "settings": {r["key"]: r.get("value", "") for r in tabs.get("config_settings", []) if r.get("key")},
        },
    }


# ------------------------------------------------------------ dashboard
# The dashboard asks for data through google.script.run (its Apps Script interface).
# This small bridge answers that request with the data embedded in the page.
BRIDGE = """<script>
window.__PAYLOAD = %s;
window.google = {script: {run: {
  _ok: null,
  withSuccessHandler(f) { this._ok = f; return this; },
  withFailureHandler(f) { return this; },
  getDashboardData() { const ok = this._ok; setTimeout(() => ok && ok(window.__PAYLOAD), 0); }
}}};
</script>"""


def build_html(payload: dict) -> str:
    page = (HERE / "dashboard.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")   # cannot close the <script> early
    return page.replace("<head>", "<head>" + BRIDGE % data, 1)


def friendly_error(err: Exception) -> str:
    text = str(err)
    if isinstance(err, requests.HTTPError):
        code = err.response.status_code if err.response is not None else 0
        if code in (401, 403):
            return "Kobo refused the API key. Check **KOBO_TOKEN** in Streamlit Secrets (Kobo → Account settings → Security → API key)."
        if code == 404:
            return "A Kobo project was not found. Check the six project IDs under **[KOBO_FORMS]** in Streamlit Secrets, and that each project is deployed."
        return f"Kobo returned an error ({code}). Try again in a minute."
    if isinstance(err, requests.RequestException):
        return "Could not reach KoboToolbox. Check the internet connection or KOBO_SERVER, then try again."
    if isinstance(err, gspread.exceptions.SpreadsheetNotFound) or "404" in text:
        email = _service_account_info().get("client_email", "the service account")
        sid = _sheet_id()
        return (f"The Google Sheet was not found. The app is looking for Sheet ID **{sid[:6]}…{sid[-4:]}** "
                f"({len(sid)} characters). Check it matches the Sheet's address, and share the Sheet with "
                f"**{email}** (Viewer).")
    if "403" in text or "PERMISSION_DENIED" in text:
        return "Google refused access. Enable the **Google Sheets API** in your Google Cloud project, and share the Sheet with the service account."
    if isinstance(err, KeyError):
        return f"A secret is missing in Streamlit settings: {text}"
    return f"Could not read the Google Sheet: {text}"


@st.fragment(run_every=REFRESH_SECONDS)
def live_dashboard():
    try:
        if "html" in st.session_state:
            payload = load_payload()
        else:
            with st.spinner("Loading the latest data from Kobo…" if kobo_mode() else "Loading the latest data from the Google Sheet…"):
                payload = load_payload()
    except Exception as err:   # show a clear message instead of a stack trace
        st.error(friendly_error(err))
        return
    # Only reload the page when the data actually changed, so viewers keep their place.
    sig = hashlib.sha256(json.dumps([payload["forms"], payload["config"]], sort_keys=True).encode()).hexdigest()
    if st.session_state.get("sig") != sig:
        st.session_state["sig"] = sig
        st.session_state["html"] = build_html(payload)
    if hasattr(st, "iframe"):            # Streamlit ≥ 1.57
        st.iframe(st.session_state["html"], height=900)
    else:                                # older Streamlit
        components.html(st.session_state["html"], height=900, scrolling=True)


live_dashboard()
