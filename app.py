"""Ghana Tier 2 Baseline Dashboard on Streamlit.

Reads the clean data that the Kobo → Apps Script pipeline writes to the Google Sheet
"Ghana Tier 2 – Dashboard Data", and shows it in the existing HTML dashboard (dashboard.html).

Secrets (Streamlit Cloud → App settings → Secrets):
    SHEET_ID = "the long ID from the Google Sheet's address"
    GCP_SERVICE_ACCOUNT_JSON = '''{ ...contents of the service-account JSON key file... }'''
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import gspread
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


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def load_payload() -> dict:
    """Same shape as getDashboardData() in the Apps Script, so the dashboard needs no changes."""
    test_file = os.environ.get("GT2_TEST_PAYLOAD")          # local testing only
    if test_file:
        return json.loads(Path(test_file).read_text(encoding="utf-8"))

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
            with st.spinner("Loading the latest data from the Google Sheet…"):
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
