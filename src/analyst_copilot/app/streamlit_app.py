"""Product UI: an "Add filing" control with visible processing status, a
chat box, evidence shown on every answer, and an honest decline. Run with:

    streamlit run src/analyst_copilot/app/streamlit_app.py

STATUS: MVP vertical slice (see analyst_copilot.service). Only narrative
BM25 lookup is wired end-to-end today; XBRL fact lookup, table
calculation, and multi-hop synthesis are not yet connected (see the
package docstrings under ingestion/xbrl_parser.py and reasoning/planner.py
for what's pending).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

# Allow running via `streamlit run` without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analyst_copilot.config import settings  # noqa: E402
from analyst_copilot.ingestion.pipeline import ingest_filing  # noqa: E402
from analyst_copilot.llm.client import LLMClient  # noqa: E402
from analyst_copilot.service import answer_question  # noqa: E402
from analyst_copilot.storage import repository  # noqa: E402
from analyst_copilot.storage.db import get_sqlite_conn, init_db  # noqa: E402

st.set_page_config(page_title="Analyst Copilot", layout="wide")
init_db()


@st.cache_resource
def _llm_client() -> LLMClient | None:
    if not settings.llm_configured:
        return None
    return LLMClient()


def _get_conn():
    if "conn" not in st.session_state:
        st.session_state.conn = get_sqlite_conn()
    return st.session_state.conn


conn = _get_conn()

st.title("The Analyst Copilot")
st.caption(
    "Ask analyst-style questions about a filing. Every answer carries its exact "
    "source location, or the system declines with “Not found in this filing.”"
)

with st.sidebar:
    st.header("Add filing")
    uploaded = st.file_uploader("Upload a filing (HTML)", type=["htm", "html"])
    company = st.text_input("Company (optional)")
    form_type = st.text_input("Form type (optional)", placeholder="10-K, 10-Q, ...")
    filing_date = st.text_input("Filing date (optional)", placeholder="YYYY-MM-DD")

    if uploaded is not None and st.button("Process filing", type="primary"):
        tmp_path = settings.raw_dir / f"_upload_{uploaded.name}"
        settings.raw_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(uploaded.getvalue())

        status = st.status("Processing filing...", expanded=True)
        status.write("Parsing HTML into the typed document tree...")
        t0 = time.monotonic()
        record, block_count, elapsed = ingest_filing(
            str(tmp_path),
            company=company or None,
            form_type=form_type or None,
            filing_date=filing_date or None,
            conn=conn,
        )
        tmp_path.unlink(missing_ok=True)
        status.write(f"Indexed {block_count} blocks in {elapsed:.1f}s.")
        status.update(label="Filing ready", state="complete", expanded=False)
        st.session_state.selected_filing_id = record.filing_id
        st.rerun()

    st.divider()
    st.header("Filings")
    filings = repository.list_filings(conn)
    if not filings:
        st.info("No filings ingested yet. Upload one above.")
    else:
        options = {
            f"{f['company'] or f['filing_id'][:8]} "
            f"({f['form_type'] or '?'}, {f['filing_date'] or '?'})": f["filing_id"]
            for f in filings
        }
        default_label = next(
            (
                label
                for label, fid in options.items()
                if fid == st.session_state.get("selected_filing_id")
            ),
            list(options.keys())[0],
        )
        chosen_label = st.selectbox("Active filing", list(options.keys()), index=list(options.keys()).index(default_label))
        st.session_state.selected_filing_id = options[chosen_label]

selected_filing_id = st.session_state.get("selected_filing_id")

if not selected_filing_id:
    st.info("Add a filing from the sidebar to start asking questions.")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])
        if msg.get("evidence"):
            with st.expander("Evidence"):
                for loc in msg["evidence"]:
                    st.markdown(
                        f"- **Section:** {loc.section_path or 'N/A'}  \n"
                        f"  **Location:** `{loc.dom_id_or_xpath}`"
                        + (f", table `{loc.table_id}` row {loc.row} col {loc.column}" if loc.table_id else "")
                        + (f"  \n  **Page:** {loc.rendered_page}" if loc.rendered_page else "")
                    )

question = st.chat_input("Ask an analyst-style question about this filing...")
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("Retrieving evidence..."):
            result = answer_question(conn, selected_filing_id, question, llm=_llm_client())
        st.write(result.answer_text)
        evidence_payload = None
        if result.evidence:
            evidence_payload = result.evidence
            with st.expander("Evidence", expanded=True):
                for loc in result.evidence:
                    st.markdown(
                        f"- **Section:** {loc.section_path or 'N/A'}  \n"
                        f"  **Location:** `{loc.dom_id_or_xpath}`"
                        + (f", table `{loc.table_id}` row {loc.row} col {loc.column}" if loc.table_id else "")
                        + (f"  \n  **Page:** {loc.rendered_page}" if loc.rendered_page else "")
                    )
        if result.verifier_failed_checks:
            st.caption(f"Declined because: {', '.join(result.verifier_failed_checks)}")

    st.session_state.messages.append(
        {"role": "assistant", "content": result.answer_text, "evidence": evidence_payload}
    )
