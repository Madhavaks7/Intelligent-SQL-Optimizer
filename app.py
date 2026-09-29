import streamlit as st
import os
import pandas as pd
import tempfile
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from database.database import DatabaseManager, validate_sqlite_db
from optimizer.optimization_engine import OptimizationEngine
from analyzer.history_manager import HistoryManager
from analyzer.playground_manager import PlaygroundManager
from ai.explainer import AIExplainer, DEFAULT_HF_MODEL
from utils.helpers import format_sql, format_ms
from models.analysis_models import Severity

# Set Streamlit Page Configuration
st.set_page_config(
    page_title="Intelligent SQL Query Optimizer",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS Styling
st.markdown("""
<style>
    /* Dark Theme Customizations */
    .stApp {
        background-color: #0e1117;
        color: #e0e0e0;
    }
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(90deg, #4F46E5, #9333EA, #06B6D4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.0rem;
        color: #9CA3AF;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background: #1F2937;
        border: 1px solid #374151;
        border-radius: 8px;
        padding: 12px 16px;
        text-align: center;
    }
    .metric-val {
        font-size: 1.6rem;
        font-weight: 700;
        color: #38BDF8;
    }
    .metric-lbl {
        font-size: 0.85rem;
        color: #9CA3AF;
    }
    .finding-card-high {
        border-left: 4px solid #EF4444;
        background: #1F1924;
        padding: 12px;
        border-radius: 4px;
        margin-bottom: 10px;
    }
    .finding-card-medium {
        border-left: 4px solid #F59E0B;
        background: #242018;
        padding: 12px;
        border-radius: 4px;
        margin-bottom: 10px;
    }
    .finding-card-info {
        border-left: 4px solid #3B82F6;
        background: #18202A;
        padding: 12px;
        border-radius: 4px;
        margin-bottom: 10px;
    }
    .badge-high {
        background: #7F1D1D;
        color: #FECACA;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .badge-medium {
        background: #78350F;
        color: #FEF3C7;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .badge-info {
        background: #1E3A8A;
        color: #BFDBFE;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .plan-scan {
        background: #450A0A;
        color: #FCA5A5;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
    .plan-search {
        background: #064E3B;
        color: #6EE7B7;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
    .upload-card {
        background-color: #1F2937;
        border: 2px dashed #4F46E5;
        border-radius: 12px;
        padding: 30px;
        text-align: center;
        margin-top: 20px;
        margin-bottom: 20px;
    }
    .playground-panel {
        background: #111827;
        border: 1px solid #374151;
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 18px;
    }
    .query-status-tested {
        color: #10B981;
        font-size: 0.82rem;
        font-weight: 600;
    }
    .query-status-analyzed {
        color: #38BDF8;
        font-size: 0.82rem;
        font-weight: 600;
    }
    .query-status-none {
        color: #6B7280;
        font-size: 0.82rem;
    }
</style>
""", unsafe_allow_html=True)


def clean_plan_detail(detail: str) -> str:
    """Strips leading SCAN/SEARCH step keywords from EXPLAIN detail string to avoid display duplication."""
    if not detail:
        return ""
    return re.sub(r'^(SCAN\s+TABLE|SCAN|SEARCH\s+TABLE|SEARCH)\s+', '', detail, flags=re.IGNORECASE)


def reset_database_state():
    """Resets all database and session analysis states, including Query Playground."""
    old_path = st.session_state.get("uploaded_db_path")
    if old_path and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except Exception:
            pass
    for k in [
        "uploaded_db_path", "uploaded_db_filename", "db_manager", "engine",
        "last_analysis", "test_result", "formatted_sql", "history_manager",
        "playground_manager",
        # Batch 4 transient UI state keys
        "_pg_rename_mode", "_pg_rename_value",
    ]:
        if k in st.session_state:
            del st.session_state[k]


def load_uploaded_database(uploaded_file):
    """Saves uploaded database file, validates SQLite format, and initializes engine."""
    temp_dir = tempfile.gettempdir()
    clean_name = os.path.basename(uploaded_file.name)
    temp_path = os.path.join(temp_dir, f"sqlo_uploaded_{clean_name}")

    with open(temp_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    is_valid, err_msg = validate_sqlite_db(temp_path)
    if not is_valid:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        return False, err_msg

    # Reset previous database state (includes playground reset)
    reset_database_state()

    st.session_state["uploaded_db_path"] = temp_path
    st.session_state["uploaded_db_filename"] = clean_name
    db_mgr = DatabaseManager(temp_path)
    st.session_state["db_manager"] = db_mgr
    st.session_state["engine"] = OptimizationEngine(db_mgr)
    return True, None


# App Header
st.markdown('<div class="main-header">⚡ Intelligent SQL Query Optimization & Performance System</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Deterministic SQL AST Parsing • SQLite Execution Plan Analysis • Rule-Based Index Recommendation • Isolated Performance Benchmarking</div>', unsafe_allow_html=True)

# Main Application Logic
if "db_manager" not in st.session_state:
    # START SCREEN: Empty state asking user to upload a SQLite database
    st.markdown("""
    <div class="upload-card">
        <h2 style="color: #F3F4F6; margin-bottom: 8px;">📁 Upload a SQLite Database to Begin</h2>
        <p style="color: #9CA3AF; font-size: 1.05rem;">
            Select your local <code>.db</code>, <code>.sqlite</code>, or <code>.sqlite3</code> database file to inspect schema and analyze query performance.
        </p>
    </div>
    """, unsafe_allow_html=True)

    col_u1, col_u2, col_u3 = st.columns([1, 2, 1])
    with col_u2:
        uploaded_file = st.file_uploader(
            "Choose SQLite Database File:",
            type=["db", "sqlite", "sqlite3"],
            key="initial_db_uploader"
        )

        if uploaded_file is not None:
            with st.spinner("Validating and loading SQLite database..."):
                success, err = load_uploaded_database(uploaded_file)
                if success:
                    st.success(f"Successfully loaded '{uploaded_file.name}'!")
                    st.rerun()
                else:
                    st.error(f"❌ **Invalid Database File:** {err}")

    st.info("🔒 **Privacy & Security Guarantee:** Your database is analyzed strictly locally in read-only mode. Original files are never modified.")

else:
    # ACTIVE DATABASE WORKFLOW
    engine = st.session_state["engine"]
    schema_info = engine.get_schema()
    total_rows = sum(t.row_count for t in schema_info.tables.values())
    db_filename = st.session_state.get("uploaded_db_filename", "uploaded_database.db")

    if "history_manager" not in st.session_state:
        st.session_state["history_manager"] = HistoryManager()
    history_manager = st.session_state["history_manager"]

    # ---------------------------------------------------------------
    # Batch 4: Initialize PlaygroundManager
    # ---------------------------------------------------------------
    if "playground_manager" not in st.session_state:
        pm = PlaygroundManager()
        st.session_state["playground_manager"] = pm
    pm: PlaygroundManager = st.session_state["playground_manager"]
    pm.ensure_default()

    # Sidebar Controls
    with st.sidebar:
        st.header("⚙️ Control Panel")

        st.subheader("🗄️ Active Database")
        st.markdown(f"**Filename:** `{db_filename}`")
        st.write(f"**Tables:** {len(schema_info.tables)}")
        st.write(f"**Total Records:** ~{total_rows:,}")

        if st.button("🔄 Upload / Switch Database", use_container_width=True):
            reset_database_state()
            st.rerun()

        st.markdown("---")

        # AI Explanation Layer (Developer-configured)
        st.subheader("🤖 AI Explanation Layer")
        explainer_status = AIExplainer()
        if explainer_status.is_configured:
            st.caption(f"✓ Configured ({explainer_status.model})")
        else:
            st.caption("AI explanation is not configured by developer")

        st.markdown("---")

        # Batch 4: Query list summary in sidebar
        st.subheader("🎮 Query Playground")
        all_queries = pm.get_all()
        active_id = pm.get_active_id()
        for q in all_queries:
            label = pm.status_label(q)
            is_active = (q.id == active_id)
            bullet = "▶" if is_active else "·"
            if q.status == "tested":
                st.markdown(f"{bullet} **{q.name}** — <span class='query-status-tested'>{label}</span>", unsafe_allow_html=True)
            elif q.status == "analyzed":
                st.markdown(f"{bullet} **{q.name}** — <span class='query-status-analyzed'>{label}</span>", unsafe_allow_html=True)
            else:
                st.markdown(f"{bullet} **{q.name}** — <span class='query-status-none'>{label}</span>", unsafe_allow_html=True)

    # Header Banner for Loaded Database
    st.markdown(f"""
    <div style="background: #18202A; border: 1px solid #374151; border-radius: 8px; padding: 10px 16px; margin-bottom: 20px;">
        <span style="color: #38BDF8; font-weight: 700;">DATABASE:</span> <code>{db_filename}</code> | 
        <span style="color: #38BDF8; font-weight: 700;">TABLES:</span> {len(schema_info.tables)} | 
        <span style="color: #38BDF8; font-weight: 700;">TOTAL RECORDS:</span> ~{total_rows:,}
    </div>
    """, unsafe_allow_html=True)

    # ================================================================
    # Batch 4: Query Playground Panel
    # ================================================================
    st.markdown('<div class="playground-panel">', unsafe_allow_html=True)
    st.markdown("### 🎮 Query Playground")

    all_queries = pm.get_all()
    active_query = pm.get_active()

    # ---------------------------------------------------------------
    # Row 1: Query selector + New Query button
    # ---------------------------------------------------------------
    def _query_label(q):
        return f"{q.name}  [{pm.status_label(q)}]"

    sel_col, new_col = st.columns([3, 1])

    with sel_col:
        query_options = [q.id for q in all_queries]
        query_labels = [_query_label(q) for q in all_queries]
        current_index = query_options.index(active_query.id) if active_query and active_query.id in query_options else 0

        selected_idx = st.selectbox(
            "Active Query:",
            options=range(len(query_options)),
            format_func=lambda i: query_labels[i],
            index=current_index,
            key="pg_query_selector",
            label_visibility="collapsed"
        )
        selected_id = query_options[selected_idx]
        if selected_id != pm.get_active_id():
            pm.set_active(selected_id)
            st.rerun()

    with new_col:
        if st.button("＋ New Query", use_container_width=True, key="pg_new_query"):
            pm.create_query()
            st.rerun()

    # Refresh after potential selection change
    active_query = pm.get_active()

    # ---------------------------------------------------------------
    # Row 2: Query name display, rename, delete
    # ---------------------------------------------------------------
    name_col, rename_col, del_col = st.columns([3, 1.2, 0.8])

    rename_mode = st.session_state.get("_pg_rename_mode", False)

    with name_col:
        if rename_mode:
            new_name_val = st.text_input(
                "Query name:",
                value=st.session_state.get("_pg_rename_value", active_query.name),
                key="pg_rename_input",
                label_visibility="collapsed"
            )
            st.session_state["_pg_rename_value"] = new_name_val
        else:
            st.markdown(f"**Name:** {active_query.name}")

    with rename_col:
        if rename_mode:
            if st.button("✔ Save Name", use_container_width=True, key="pg_save_rename"):
                new_val = st.session_state.get("_pg_rename_value", "").strip()
                if new_val:
                    pm.rename_query(active_query.id, new_val)
                st.session_state["_pg_rename_mode"] = False
                st.session_state.pop("_pg_rename_value", None)
                st.rerun()
        else:
            if st.button("✏ Rename", use_container_width=True, key="pg_rename_btn"):
                st.session_state["_pg_rename_mode"] = True
                st.session_state["_pg_rename_value"] = active_query.name
                st.rerun()

    with del_col:
        can_delete = len(pm.get_all()) > 1
        if st.button(
            "🗑 Delete",
            use_container_width=True,
            key="pg_delete_btn",
            disabled=not can_delete,
            help="Cannot delete the last remaining query." if not can_delete else "Delete this query from the playground. History entries are preserved."
        ):
            pm.delete_query(active_query.id)
            st.session_state["_pg_rename_mode"] = False
            st.session_state.pop("_pg_rename_value", None)
            st.rerun()

    st.markdown("---")

    # ---------------------------------------------------------------
    # Row 3: SQL Editor + Analyze button
    # ---------------------------------------------------------------
    # Refresh active query after possible mutations
    active_query = pm.get_active()

    # Build default SQL hint for empty queries (database-agnostic)
    default_table_name = list(schema_info.tables.keys())[0] if schema_info.tables else None
    sql_placeholder = f"SELECT *\nFROM {default_table_name}\nLIMIT 10;" if default_table_name else "-- Enter your SQL query here"

    current_sql = active_query.sql if active_query.sql.strip() else ""

    # Handle formatted SQL (persisted via session state, scoped to active query)
    fmt_key = f"_pg_fmt_{active_query.id}"
    if fmt_key in st.session_state:
        current_sql = st.session_state.pop(fmt_key)
        pm.update_sql(active_query.id, current_sql)

    sql_input = st.text_area(
        "SQL Query (SELECT statements):",
        value=current_sql,
        height=160,
        placeholder=sql_placeholder,
        key=f"pg_sql_{active_query.id}",
        label_visibility="collapsed"
    )

    # Detect SQL change and invalidate state
    if sql_input.strip() != active_query.sql.strip():
        pm.update_sql(active_query.id, sql_input)

    btn_col1, btn_col2, btn_col3 = st.columns([1.1, 1.1, 2.8])
    with btn_col1:
        analyze_clicked = st.button("⚡ Analyze Query", type="primary", use_container_width=True, key="pg_analyze_btn")
    with btn_col2:
        if st.button("🧹 Format SQL", use_container_width=True, key="pg_format_btn"):
            st.session_state[fmt_key] = format_sql(sql_input)
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)  # close playground-panel

    # ================================================================
    # Analyze Action (Batch 4: operates on active query only)
    # ================================================================
    if analyze_clicked:
        active_query = pm.get_active()
        clean_sql = sql_input.strip()
        if not clean_sql:
            st.warning("Please enter a SQL query to analyze.")
        else:
            is_safe, err_msg = DatabaseManager.is_safe_read_query(clean_sql)
            if not is_safe:
                st.error(f"⚠️ **Safety Check Failed:** {err_msg}")
            else:
                with st.spinner("Analyzing Query AST & Execution Plan..."):
                    analysis_result = engine.analyze_query(clean_sql, run_benchmark=False)

                    # Store result in the active playground query (isolated)
                    pm.set_analysis(active_query.id, analysis_result)

                    # Phase 2 Batch 3: Record analysis in session history
                    cand_name = HistoryManager.extract_candidate_name(analysis_result.recommendations)
                    cand_ddl = next((r.ddl for r in analysis_result.recommendations if r.type == "CREATE INDEX" and r.ddl), None)
                    history_manager.record_analysis(
                        db_name=db_filename,
                        query=clean_sql,
                        optimization_name=cand_name,
                        strategy=analysis_result.optimization_strategy,
                        candidate_ddl=cand_ddl,
                        query_name=active_query.name
                    )
                    st.rerun()

    # ================================================================
    # Results: always display state for the ACTIVE query only
    # ================================================================
    active_query = pm.get_active()
    res = active_query.analysis_result if active_query else None

    # Quick Metrics (right-aligned, displayed when analysis is available)
    if res is not None:
        p_query = res.parsed_query
        m_cols = st.columns(4)
        with m_cols[0]:
            st.markdown(f'<div class="metric-card"><div class="metric-val">{len(p_query.tables)}</div><div class="metric-lbl">Tables</div></div>', unsafe_allow_html=True)
        with m_cols[1]:
            st.markdown(f'<div class="metric-card"><div class="metric-val">{len(p_query.joins)}</div><div class="metric-lbl">Joins</div></div>', unsafe_allow_html=True)
        with m_cols[2]:
            st.markdown(f'<div class="metric-card"><div class="metric-val">{len(p_query.where_predicates)}</div><div class="metric-lbl">Filters</div></div>', unsafe_allow_html=True)
        with m_cols[3]:
            speedup = res.benchmark_result.speedup_factor if res.benchmark_result else 1.0
            st.markdown(f'<div class="metric-card"><div class="metric-val">{speedup}x</div><div class="metric-lbl">Speedup</div></div>', unsafe_allow_html=True)

        st.markdown("---")
        tab_findings, tab_plan, tab_rewrite, tab_bench, tab_history, tab_schema, tab_ai = st.tabs([
            "⚠️ Findings & Recommendations",
            "🔍 Execution Plan (EXPLAIN)",
            "🛠️ Optimization Result & Index DDL",
            "⚡ Performance Benchmark",
            "📜 Optimization History",
            "🗂️ Schema Inspector",
            "🤖 AI Explanation"
        ])

        # TAB 1: FINDINGS & RECOMMENDATIONS
        with tab_findings:
            st.subheader("Optimization Findings")
            if res.parsed_query.parse_error:
                st.error(f"❌ **Error:** {res.parsed_query.parse_error}")
            elif not res.findings:
                st.success("✅ **No Query Inefficiencies Detected!** The query is well-structured and utilizes existing indexes efficiently.")
            else:
                # Batch 5: Separate plan observations (INFO / Execution Plan category) from
                # actionable findings so they are visually distinct.
                plan_observations = [
                    f for f in res.findings
                    if f.category == "Execution Plan" and f.severity == Severity.INFO
                ]
                raw_actionable = [
                    f for f in res.findings
                    if not (f.category == "Execution Plan" and f.severity == Severity.INFO)
                ]

                # Batch 5.1: Deduplicate actionable findings for UI card display
                has_r11 = any(f.rule_id == "RULE_11" for f in raw_actionable)
                has_r12 = any(f.rule_id == "RULE_12" for f in raw_actionable)

                actionable_findings = []
                for f in raw_actionable:
                    if f.rule_id == "RULE_03" and has_r11:
                        if any(o.rule_id == "RULE_11" and (o.affected_table or "").lower() == (f.affected_table or "").lower() for o in raw_actionable):
                            continue
                    if f.rule_id == "RULE_05" and has_r12:
                        if any(o.rule_id == "RULE_12" and (o.affected_table or "").lower() == (f.affected_table or "").lower() for o in raw_actionable):
                            continue
                    actionable_findings.append(f)

                # Actionable findings
                for f in actionable_findings:
                    badge_class = "badge-high" if f.severity == Severity.HIGH else ("badge-medium" if f.severity == Severity.MEDIUM else "badge-info")
                    card_class = "finding-card-high" if f.severity == Severity.HIGH else ("finding-card-medium" if f.severity == Severity.MEDIUM else "finding-card-info")

                    st.markdown(f"""
                    <div class="{card_class}">
                        <span class="{badge_class}">{f.severity.value.upper()}</span> <strong>[{f.rule_id}] {f.title}</strong>
                        <br><small><strong>Category:</strong> {f.category} | <strong>Table:</strong> {f.affected_table or 'N/A'}</small>
                        <p style="margin-top: 6px; margin-bottom: 4px;">{f.description}</p>
                        <small style="color: #9CA3AF;"><em><strong>Rationale:</strong> {f.rationale}</em></small>
                    </div>
                    """, unsafe_allow_html=True)

                # Batch 5: Query Plan Observations section (visually distinct)
                if plan_observations:
                    st.markdown("---")
                    st.markdown("#### 🔍 Query Plan Observations")
                    st.caption("These are observations derived directly from the SQLite EXPLAIN QUERY PLAN output. They are evidence of what the engine is doing — not guaranteed performance issues.")
                    for obs in plan_observations:
                        st.markdown(f"""
                        <div style="background: #111827; border-left: 3px solid #6366F1; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px;">
                            <span style="color: #818CF8; font-weight: 600;">📋 {obs.title}</span>
                            <p style="margin-top: 6px; margin-bottom: 2px; color: #D1D5DB;">{obs.description}</p>
                            <small style="color: #6B7280;"><em>{obs.rationale}</em></small>
                        </div>
                        """, unsafe_allow_html=True)

                st.markdown("---")
                st.subheader("Recommended Actions")
                for r in res.recommendations:
                    st.markdown(f"**[{r.type}]** {r.description}")
                    if r.ddl:
                        st.code(r.ddl, language="sql")

                # Phase 2 Batch 1 / Batch 5.1: Test Optimization Button Condition
                _TESTABLE_STRATEGIES = {"INDEX_BASED", "COMPOSITE_INDEX", "JOIN_INDEX", "ORDER_BY_INDEX", "COVERING_INDEX"}
                executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
                if res.optimization_strategy in _TESTABLE_STRATEGIES and executable_recs:
                    st.markdown("---")
                    st.markdown("### 🧪 Test Optimization Workflow")
                    st.caption("Click to test the candidate index in an isolated temporary database copy. Your original database file remains strictly read-only.")

                    if st.button("🧪 Test Optimization", type="primary", use_container_width=True, key="pg_btn_test_optimization"):
                        active_query = pm.get_active()
                        with st.spinner("Creating isolated database copy, applying candidate index & running benchmark..."):
                            from performance.benchmark import PerformanceBenchmarker
                            benchmarker = PerformanceBenchmarker(st.session_state["db_manager"])
                            b_res = benchmarker.run_benchmark(
                                query=res.query,
                                recommendations=res.recommendations,
                                suggested_query=res.suggested_query
                            )

                            # Store test result in the active playground query (isolated)
                            pm.set_test_result(active_query.id, b_res)

                            # Phase 2 Batch 3: Record test result in session history
                            cand_name = HistoryManager.extract_candidate_name(res.recommendations)
                            cand_ddl = next((r.ddl for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl), None)
                            history_manager.record_test_result(
                                db_name=db_filename,
                                query=res.query,
                                optimization_name=cand_name,
                                strategy=res.optimization_strategy,
                                candidate_ddl=cand_ddl,
                                benchmark_result=b_res,
                                query_name=active_query.name
                            )

                            st.success("✅ Optimization Test Completed! View results in the 'Performance Benchmark' tab.")
                            st.rerun()

        # TAB 2: EXECUTION PLAN
        with tab_plan:
            st.subheader("SQLite EXPLAIN QUERY PLAN Analysis")
            st.caption("Derived directly from SQLite database engine. Distinguishes between full table SCANs and direct index SEARCH lookups.")

            col_p1, col_p2 = st.columns(2)
            with col_p1:
                st.write("#### 🔴 Original Execution Plan")
                if not res.plan_nodes:
                    st.write("No plan nodes found.")
                else:
                    for node in res.plan_nodes:
                        plan_badge = '<span class="plan-scan">SCAN</span>' if node.step_type == "SCAN" else ('<span class="plan-search">SEARCH</span>' if node.step_type == "SEARCH" else f'<span>{node.step_type}</span>')
                        c_detail = clean_plan_detail(node.detail)
                        st.markdown(f"- {plan_badge} `{c_detail}`", unsafe_allow_html=True)

            with col_p2:
                st.write("#### 🟢 Post-Optimization Execution Plan")
                if res.benchmark_result and res.benchmark_result.optimized_metrics:
                    opt_nodes = res.benchmark_result.optimized_metrics.plan_nodes
                    if opt_nodes:
                        for node in opt_nodes:
                            plan_badge = '<span class="plan-scan">SCAN</span>' if node.step_type == "SCAN" else ('<span class="plan-search">SEARCH</span>' if node.step_type == "SEARCH" else f'<span>{node.step_type}</span>')
                            c_detail = clean_plan_detail(node.detail)
                            st.markdown(f"- {plan_badge} `{c_detail}`", unsafe_allow_html=True)
                    else:
                        st.write("Execution plan unchanged.")
                else:
                    st.info("Test not run yet.")

        # TAB 3: OPTIMIZATION RESULT & INDEX DDL
        with tab_rewrite:
            st.subheader("Optimization Result & Index DDL")
            strategy = getattr(res, "optimization_strategy", "INDEX_BASED" if any(r.ddl for r in res.recommendations) else "NO_OPTIMIZATION_REQUIRED")

            # Batch 5: Human-readable labels for all strategy types
            _STRATEGY_LABELS = {
                "INDEX_BASED":       ("⚡", "Index-Based Database Optimization",    "Optimization is provided by the recommended database index. SQL query text is unchanged."),
                "COMPOSITE_INDEX":   ("🧩", "Potential Composite Index Opportunity", "A composite index on multiple filter columns may improve query performance. SQL query text is unchanged."),
                "COVERING_INDEX":    ("📦", "Potential Covering Index Opportunity",  "A covering index containing both filter and select columns may allow SQLite to satisfy the query without table row lookups. SQL query text is unchanged."),
                "ORDER_BY_INDEX":    ("↕️", "Potential ORDER BY Index Opportunity",  "A combined WHERE + ORDER BY index may eliminate the temporary B-tree sort. SQL query text is unchanged."),
                "JOIN_INDEX":        ("🔗", "Potential JOIN Index Opportunity",      "An index on the join column of the inner table may speed up the JOIN lookup. SQL query text is unchanged."),
            }

            if strategy in _STRATEGY_LABELS:
                icon, label, note = _STRATEGY_LABELS[strategy]
                st.success(f"{icon} **Optimization Strategy:** {label}")
                st.markdown(f"**Note:** {note}")
                st.markdown("#### Candidate DDL Index Creation Command(s)")
                ddl_list = [r.ddl for r in res.recommendations if r.ddl]
                if ddl_list:
                    st.code("\n\n".join(ddl_list), language="sql")

            elif strategy == "QUERY_REWRITE":
                st.success("🛠️ **Optimization Strategy:** SQL Query Rewrite")
                st.markdown("#### Suggested Rewritten Query")
                if res.suggested_query:
                    st.code(res.suggested_query, language="sql")
                    st.warning("⚠️ **Note:** Verify semantic equivalence before executing suggested query in production environments.")

            elif strategy == "NO_OPTIMIZATION_REQUIRED":
                st.info("✅ **Optimization Strategy:** No Optimization Required\n\nNo SQL rewrite or database index required. The current query already uses an optimal execution plan.")

            else:  # INFORMATIONAL_ADVISORY
                st.info("💡 **Optimization Strategy:** Informational / Advisory Finding\n\nNo automatic SQL rewrite or physical index recommended for these findings.")

        # TAB 4: PERFORMANCE BENCHMARK (Phase 2 Batch 1, Batch 2 & Batch 6 Robust Multi-Run)
        with tab_bench:
            st.subheader("⚡ Robust Multi-Run Performance Benchmark & Test Result")

            if res.benchmark_result and res.benchmark_result.original_metrics:
                b = res.benchmark_result
                orig = b.original_metrics
                opt = b.optimized_metrics

                # Format human-readable candidate name (table(column))
                cand_human = b.candidate_name or HistoryManager.extract_candidate_name(res.recommendations)
                if not cand_human or cand_human == "No Index Candidate":
                    cand_human = ", ".join(b.indexes_created) if b.indexes_created else "Candidate Index"

                _STRATEGY_BENCH_MAP = {
                    "INDEX_BASED": "Index-Based Database Optimization",
                    "COMPOSITE_INDEX": "Potential Composite Index Opportunity",
                    "COVERING_INDEX": "Potential Covering Index Opportunity",
                    "ORDER_BY_INDEX": "Potential ORDER BY Index Opportunity",
                    "JOIN_INDEX": "Potential JOIN Index Opportunity",
                }
                curr_strat = b.strategy or getattr(res, "optimization_strategy", "INDEX_BASED")
                strat_display = _STRATEGY_BENCH_MAP.get(curr_strat, curr_strat.replace("_", " ").title())

                st.markdown(f"""
                <div style="background: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 16px; margin-bottom: 20px;">
                    <h4 style="margin-top:0; color: #38BDF8;">🧪 Optimization Test Result</h4>
                    <p style="margin-bottom: 4px;"><strong>Optimization:</strong> <code>{cand_human}</code></p>
                    <p style="margin-bottom: 4px;"><strong>Strategy:</strong> {strat_display}</p>
                    <p style="margin-bottom: 4px;"><strong>Original SQL:</strong> <code>{res.query}</code></p>
                    <p style="margin-bottom: 4px;"><strong>SQL Query Text:</strong> Unchanged — only the isolated database environment was modified.</p>
                    <p style="margin-bottom: 0; color: #10B981;"><strong>Database Safety:</strong> ✓ Original database was not modified (tested on isolated temporary copy)</p>
                </div>
                """, unsafe_allow_html=True)

                # Phase 2 — Batch 2: Result Equivalence Card
                if b.equivalence_result:
                    eq = b.equivalence_result
                    eq_card_border = "#10B981" if eq.is_equivalent else "#EF4444"
                    eq_badge_color = "#6EE7B7" if eq.is_equivalent else "#FCA5A5"
                    eq_status_text = "✓ Results Equivalent" if eq.is_equivalent else "✗ Results Not Equivalent"

                    st.markdown(f"""
                    <div style="background: #111827; border: 1px solid {eq_card_border}; border-radius: 8px; padding: 16px; margin-bottom: 20px;">
                        <h4 style="margin-top:0; color: {eq_badge_color};">⚖️ Result Equivalence Validation</h4>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-bottom: 12px;">
                            <div><small style="color: #9CA3AF;">Original Rows</small><br><strong>{eq.orig_row_count:,}</strong></div>
                            <div><small style="color: #9CA3AF;">Optimized Rows</small><br><strong>{eq.opt_row_count:,}</strong></div>
                            <div><small style="color: #9CA3AF;">Columns</small><br><strong style="color: {'#10B981' if eq.columns_match else '#EF4444'};">{'✓ Same' if eq.columns_match else '✗ Differ'}</strong></div>
                            <div><small style="color: #9CA3AF;">Values</small><br><strong style="color: {'#10B981' if eq.values_match else '#EF4444'};">{'✓ Same' if eq.values_match else '✗ Differ'}</strong></div>
                            <div><small style="color: #9CA3AF;">Result</small><br><strong style="color: {'#10B981' if eq.is_equivalent else '#EF4444'};">{eq_status_text}</strong></div>
                        </div>
                        <p style="margin-bottom: 0; color: #D1D5DB; font-size: 0.95rem;">{eq.explanation}</p>
                    </div>
                    """, unsafe_allow_html=True)

                # Batch 6: Performance Classification Outcome Banner
                is_equiv = b.equivalence_result.is_equivalent if b.equivalence_result else True
                orig_med = b.original_median_ms if b.original_median_ms is not None else orig.exec_time_ms
                opt_med = b.optimized_median_ms if b.optimized_median_ms is not None else (opt.exec_time_ms if opt else orig_med)
                pct_diff = b.percentage_difference
                speedup = b.median_speedup if b.median_speedup is not None else b.speedup_factor

                abs_diff = opt_med - orig_med
                if not is_equiv:
                    outcome_class = "background: #450A0A; border-left: 4px solid #EF4444; color: #FCA5A5;"
                    outcome_title = "✗ Result Equivalence Failed"
                    diff_str = "N/A"
                    delta_col = "off"
                elif b.performance_classification == "PERFORMANCE_IMPROVED":
                    outcome_class = "background: #064E3B; border-left: 4px solid #10B981; color: #6EE7B7;"
                    outcome_title = f"✓ Validated Performance Improvement ({abs(pct_diff):.1f}% faster)"
                    diff_str = f"↓ {abs(abs_diff):.2f} ms ({pct_diff:.1f}%)"
                    delta_col = "normal"
                elif b.performance_classification == "PERFORMANCE_REGRESSION":
                    outcome_class = "background: #450A0A; border-left: 4px solid #EF4444; color: #FCA5A5;"
                    outcome_title = f"✗ Performance Regression (+{pct_diff:.1f}% slower)"
                    diff_str = f"↑ {abs_diff:.2f} ms (+{pct_diff:.1f}%)"
                    delta_col = "inverse"
                else:
                    outcome_class = "background: #1F2937; border-left: 4px solid #9CA3AF; color: #E5E7EB;"
                    outcome_title = f"≈ No Measurable Performance Improvement ({pct_diff:+.1f}%)"
                    diff_str = f"≈ {abs_diff:+.2f} ms ({pct_diff:+.1f}%)"
                    delta_col = "off"

                st.markdown(f"""
                <div style="{outcome_class} padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-weight: 700; font-size: 1.1rem;">
                    {outcome_title}
                </div>
                """, unsafe_allow_html=True)

                st.write("#### 🔍 Execution Plan Evidence")
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    st.write("**Original Plan:**")
                    for node in orig.plan_nodes:
                        plan_badge = '<span class="plan-scan">SCAN</span>' if node.step_type == "SCAN" else ('<span class="plan-search">SEARCH</span>' if node.step_type == "SEARCH" else f'<span>{node.step_type}</span>')
                        st.markdown(f"- {plan_badge} `{clean_plan_detail(node.detail)}`", unsafe_allow_html=True)

                with col_p2:
                    st.write("**Optimized Plan:**")
                    if opt:
                        for node in opt.plan_nodes:
                            plan_badge = '<span class="plan-scan">SCAN</span>' if node.step_type == "SCAN" else ('<span class="plan-search">SEARCH</span>' if node.step_type == "SEARCH" else f'<span>{node.step_type}</span>')
                            st.markdown(f"- {plan_badge} `{clean_plan_detail(node.detail)}`", unsafe_allow_html=True)

                st.markdown(f"**Plan Change:** `{b.plan_change_desc}`")

                st.markdown("---")

                st.write("#### ⏱️ Runtime Performance Evidence (Multi-Run)")
                st.caption(f"Benchmark Configuration: **{b.warmup_runs} warm-up runs** (excluded from statistics), **{b.measured_runs} measured runs** (median-based analysis with ±5.0% variance threshold).")

                b_c1, b_c2, b_c3, b_c4 = st.columns(4)
                with b_c1:
                    st.metric("Original Median", f"{orig_med:.2f} ms")
                with b_c2:
                    st.metric("Optimized Median", f"{opt_med:.2f} ms" if opt else "N/A")
                with b_c3:
                    st.metric("Median Difference", diff_str)
                with b_c4:
                    st.metric("Median Speedup", f"{speedup:.2f}x")

                # Multi-Run Summary Table
                orig_min = b.original_min_ms if b.original_min_ms is not None else orig_med
                orig_max = b.original_max_ms if b.original_max_ms is not None else orig_med
                opt_min = b.optimized_min_ms if b.optimized_min_ms is not None else (opt_med if opt else 0.0)
                opt_max = b.optimized_max_ms if b.optimized_max_ms is not None else (opt_med if opt else 0.0)

                summary_rows = [
                    {
                        "Statistic Metric": "Median Execution Time",
                        "Original Query": f"{orig_med:.2f} ms",
                        "Optimized Query": f"{opt_med:.2f} ms" if opt else "N/A",
                        "Difference / Change": f"{opt_med - orig_med:+.2f} ms ({pct_diff:+.1f}%)" if opt else "N/A"
                    },
                    {
                        "Statistic Metric": "Minimum Execution Time",
                        "Original Query": f"{orig_min:.2f} ms",
                        "Optimized Query": f"{opt_min:.2f} ms" if opt else "N/A",
                        "Difference / Change": f"{opt_min - orig_min:+.2f} ms" if opt else "N/A"
                    },
                    {
                        "Statistic Metric": "Maximum Execution Time",
                        "Original Query": f"{orig_max:.2f} ms",
                        "Optimized Query": f"{opt_max:.2f} ms" if opt else "N/A",
                        "Difference / Change": f"{opt_max - orig_max:+.2f} ms" if opt else "N/A"
                    }
                ]
                st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

                # Expandable Run Inspector
                with st.expander("🔍 Show individual benchmark runs"):
                    if b.original_warmup_times_ms or b.optimized_warmup_times_ms:
                        st.markdown("**Warm-up Runs (Excluded from statistics):**")
                        st.caption("Warm-up runs prime database buffer caches and engine state to avoid cold-cache timing bias.")
                        w_cols = st.columns(2)
                        with w_cols[0]:
                            st.write(f"Original Warmup: `{[f'{t:.2f} ms' for t in b.original_warmup_times_ms]}`")
                        with w_cols[1]:
                            st.write(f"Optimized Warmup: `{[f'{t:.2f} ms' for t in b.optimized_warmup_times_ms]}`" if opt else "N/A")

                    st.markdown("**Measured Runs (Included in statistics):**")
                    runs_data = []
                    max_runs = max(len(b.original_times_ms), len(b.optimized_times_ms))
                    for i in range(max_runs):
                        t_orig = b.original_times_ms[i] if i < len(b.original_times_ms) else None
                        t_opt = b.optimized_times_ms[i] if i < len(b.optimized_times_ms) else None
                        diff = (t_opt - t_orig) if (t_orig is not None and t_opt is not None) else None
                        runs_data.append({
                            "Run #": f"Run {i + 1}",
                            "Original Query (ms)": f"{t_orig:.2f}" if t_orig is not None else "N/A",
                            "Optimized Query (ms)": f"{t_opt:.2f}" if t_opt is not None else "N/A",
                            "Difference (ms)": f"{diff:+.2f}" if diff is not None else "N/A"
                        })
                    st.dataframe(pd.DataFrame(runs_data), use_container_width=True, hide_index=True)

                if opt:
                    chart_data = pd.DataFrame({
                        "Query Variant": pd.Categorical(["Original Query (Median)", "Optimized Query (Median)"], categories=["Original Query (Median)", "Optimized Query (Median)"], ordered=True),
                        "Execution Time (ms)": [orig_med, opt_med]
                    })
                    st.bar_chart(chart_data, x="Query Variant", y="Execution Time (ms)", color="#4F46E5")

                st.write("#### 💡 Test Interpretation")
                st.info(f"{b.note}")

            else:
                executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
                if executable_recs:
                    st.info("ℹ️ Candidate index available! Go to **'⚠️ Findings & Recommendations'** tab and click **'🧪 Test Optimization'** to test execution in an isolated database copy.")
                else:
                    st.info("ℹ️ No candidate index available for testing. The current query either already uses optimal indexes or contains only informational advisories.")

        # TAB 5: OPTIMIZATION HISTORY (Phase 2 Batch 3 & Batch 6 Backward Compatible)
        with tab_history:
            st.subheader("📜 Optimization & Analysis History")
            st.caption("Session-only history of analyzed queries and empirical benchmark tests for this database.")

            entries = history_manager.get_entries()
            if not entries:
                st.info("ℹ️ No optimization history recorded yet for this database session. Analyze queries or test optimizations to populate history.")
            else:
                for entry in reversed(entries):
                    # Choose border / badge styling based on validation_status
                    if entry.validation_status == "Analyzed — Not Tested":
                        status_color = "#3B82F6"
                        border_color = "#1E3A8A"
                        badge_bg = "#18202A"
                    elif "Validated Performance Improvement" in entry.validation_status:
                        status_color = "#10B981"
                        border_color = "#064E3B"
                        badge_bg = "#064E3B"
                    elif "Performance Regression" in entry.validation_status:
                        status_color = "#EF4444"
                        border_color = "#7F1D1D"
                        badge_bg = "#450A0A"
                    elif "No Measurable" in entry.validation_status:
                        status_color = "#9CA3AF"
                        border_color = "#374151"
                        badge_bg = "#1F2937"
                    else:  # Validation Failed
                        status_color = "#F59E0B"
                        border_color = "#78350F"
                        badge_bg = "#242018"

                    timing_med_orig = entry.original_median_ms if entry.original_median_ms is not None else entry.orig_time_ms
                    timing_med_opt = entry.optimized_median_ms if entry.optimized_median_ms is not None else entry.opt_time_ms
                    timing_summary = f"{timing_med_orig:.2f} ms → {timing_med_opt:.2f} ms ({entry.speedup_factor:.2f}x)" if entry.is_tested and timing_med_orig is not None and timing_med_opt is not None else "Not Tested"
                    equiv_summary = "Equivalent" if entry.is_equivalent is True else ("Differ" if entry.is_equivalent is False else "N/A")
                    runs_info = f" | <strong>Runs:</strong> {entry.measured_runs} measured" if entry.measured_runs else ""

                    st.markdown(f"""
                    <div style="background: #1F2937; border: 1px solid {border_color}; border-left: 4px solid {status_color}; border-radius: 6px; padding: 12px 16px; margin-bottom: 12px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <span style="font-size: 1.05rem; font-weight: 700; color: #F3F4F6;">🧪 {entry.optimization_name}</span>
                            <span style="background: {badge_bg}; color: {status_color}; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; font-weight: 600;">{entry.validation_status}</span>
                        </div>
                        {f'<div style="font-size: 0.78rem; color: #6366F1; font-weight: 600; margin-bottom: 4px;">📋 {entry.query_name}</div>' if entry.query_name else ''}
                        <div style="font-size: 0.9rem; color: #9CA3AF; margin-bottom: 4px;">
                            <strong>Timing:</strong> {timing_summary}{runs_info} | <strong>Results:</strong> {equiv_summary} | <strong>Timestamp:</strong> {entry.timestamp}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    with st.expander(f"🔍 Details: {entry.optimization_name} ({entry.timestamp})"):
                        if entry.query_name:
                            st.write(f"**Originating Query:** `{entry.query_name}`")
                        st.write(f"**Database:** `{entry.db_name}`")
                        st.write(f"**Strategy:** `{entry.strategy}`")
                        st.write("**SQL Query:**")
                        st.code(entry.query, language="sql")
                        if entry.candidate_ddl:
                            st.write("**Candidate Index DDL:**")
                            st.code(entry.candidate_ddl, language="sql")
                        if entry.is_tested:
                            st.write("#### Benchmark & Validation Details")
                            col_h1, col_h2, col_h3, col_h4 = st.columns(4)
                            with col_h1:
                                st.metric("Original Median", f"{timing_med_orig:.2f} ms" if timing_med_orig is not None else "N/A")
                            with col_h2:
                                st.metric("Optimized Median", f"{timing_med_opt:.2f} ms" if timing_med_opt is not None else "N/A")
                            with col_h3:
                                st.metric("Speedup", f"{entry.speedup_factor:.2f}x" if entry.speedup_factor is not None else "N/A")
                            with col_h4:
                                st.metric("Percentage Diff", f"{entry.percentage_difference:+.1f}%" if entry.percentage_difference is not None else "N/A")

                            if entry.original_min_ms is not None and entry.original_max_ms is not None:
                                st.caption(f"Original Range: [{entry.original_min_ms:.2f} ms – {entry.original_max_ms:.2f} ms] | Optimized Range: [{entry.optimized_min_ms:.2f} ms – {entry.optimized_max_ms:.2f} ms]")

                            st.write(f"**Original Plan:** `{entry.orig_plan}`")
                            st.write(f"**Optimized Plan:** `{entry.opt_plan}`")
                            if entry.equivalence_details:
                                st.write(f"**Equivalence Details:** {entry.equivalence_details}")
                            if entry.ai_explanation:
                                st.write("**🤖 AI Explanation:**")
                                st.info(entry.ai_explanation)

        # TAB 6: SCHEMA INSPECTOR
        with tab_schema:
            st.subheader("Database Schema Explorer")
            tables_dict = res.schema.tables

            if not tables_dict:
                st.info("No user tables found in the uploaded SQLite database.")
            else:
                selected_tbl = st.selectbox("Select Table to Inspect:", list(tables_dict.keys()))
                if selected_tbl:
                    tschema = tables_dict[selected_tbl]
                    st.write(f"**Table:** `{tschema.name}` | **Rows:** `{tschema.row_count:,}`")

                    st.write("#### Columns")
                    col_df = pd.DataFrame([
                        {
                            "Column Name": c.name,
                            "Data Type": c.data_type,
                            "Not Null": c.not_null,
                            "Primary Key": c.pk
                        } for c in tschema.columns
                    ])
                    st.dataframe(col_df, use_container_width=True)

                    st.write("#### Indexes")
                    if tschema.indexes:
                        idx_df = pd.DataFrame([
                            {
                                "Index Name": idx.name,
                                "Unique": idx.is_unique,
                                "Columns": ", ".join(idx.columns)
                            } for idx in tschema.indexes
                        ])
                        st.dataframe(idx_df, use_container_width=True)
                    else:
                        st.info("No secondary indexes defined on this table.")

        # TAB 7: AI EXPLANATION (Phase 2 — Batch 7 Hugging Face Integration)
        with tab_ai:
            st.subheader("🤖 AI Explanation")
            st.caption("Translates verified execution plans, optimization rules, result equivalence, and multi-run benchmark facts into educational natural language explanations.")

            explainer = AIExplainer()

            if res.ai_explanation:
                st.markdown(f"""
                <div style="background: #111827; border: 1px solid #374151; border-left: 4px solid #6366F1; border-radius: 8px; padding: 16px; margin-bottom: 16px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span style="color: #818CF8; font-weight: 700; font-size: 1.05rem;">🤖 Verified Optimization Explanation</span>
                        <span style="color: #9CA3AF; font-size: 0.8rem; background: #1F2937; padding: 2px 8px; border-radius: 12px;">Model: {explainer.model}</span>
                    </div>
                    <div style="color: #E5E7EB; line-height: 1.6; font-size: 0.95rem;">
                """, unsafe_allow_html=True)
                st.markdown(res.ai_explanation)
                st.markdown('</div></div>', unsafe_allow_html=True)

                if st.button("🔄 Regenerate AI Explanation", key=f"btn_regen_ai_{active_query.id}"):
                    with st.spinner(f"Generating explanation using {explainer.model}..."):
                        new_explanation = explainer.generate_explanation(res)
                        res.ai_explanation = new_explanation
                        history_manager.update_ai_explanation(res.query, new_explanation)
                        st.rerun()

            else:
                if not explainer.is_configured:
                    st.info("AI explanation is not configured by the developer.")
                else:
                    st.info(f"Click the button below to generate an educational AI explanation of the verified optimization and benchmark facts using **{explainer.model}**.")
                    if st.button("🤖 Generate AI Explanation", type="primary", key=f"btn_gen_ai_{active_query.id}"):
                        with st.spinner(f"Generating explanation using {explainer.model}..."):
                            explanation = explainer.generate_explanation(res)
                            res.ai_explanation = explanation
                            history_manager.update_ai_explanation(res.query, explanation)
                            st.rerun()

    else:
        # No analysis yet — show schema inspector and history tabs while waiting
        st.markdown("---")
        tab_history, tab_schema = st.tabs([
            "📜 Optimization History",
            "🗂️ Schema Inspector",
        ])

        with tab_history:
            st.subheader("📜 Optimization & Analysis History")
            st.caption("Session-only history of analyzed queries and empirical benchmark tests for this database.")
            entries = history_manager.get_entries()
            if not entries:
                st.info("ℹ️ No optimization history recorded yet. Analyze a query above to begin.")
            else:
                for entry in reversed(entries):
                    if entry.validation_status == "Analyzed — Not Tested":
                        status_color = "#3B82F6"
                        border_color = "#1E3A8A"
                        badge_bg = "#18202A"
                    elif "Validated Performance Improvement" in entry.validation_status:
                        status_color = "#10B981"
                        border_color = "#064E3B"
                        badge_bg = "#064E3B"
                    elif "Performance Regression" in entry.validation_status:
                        status_color = "#EF4444"
                        border_color = "#7F1D1D"
                        badge_bg = "#450A0A"
                    elif "No Measurable" in entry.validation_status:
                        status_color = "#9CA3AF"
                        border_color = "#374151"
                        badge_bg = "#1F2937"
                    else:
                        status_color = "#F59E0B"
                        border_color = "#78350F"
                        badge_bg = "#242018"

                    timing_summary = f"{entry.orig_time_ms:.2f} ms → {entry.opt_time_ms:.2f} ms ({entry.speedup_factor:.2f}x)" if entry.is_tested and entry.orig_time_ms is not None and entry.opt_time_ms is not None else "Not Tested"
                    equiv_summary = "Equivalent" if entry.is_equivalent is True else ("Differ" if entry.is_equivalent is False else "N/A")
                    st.markdown(f"""
                    <div style="background: #1F2937; border: 1px solid {border_color}; border-left: 4px solid {status_color}; border-radius: 6px; padding: 12px 16px; margin-bottom: 12px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <span style="font-size: 1.05rem; font-weight: 700; color: #F3F4F6;">🧪 {entry.optimization_name}</span>
                            <span style="background: {badge_bg}; color: {status_color}; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; font-weight: 600;">{entry.validation_status}</span>
                        </div>
                        {f'<div style="font-size: 0.78rem; color: #6366F1; font-weight: 600; margin-bottom: 4px;">📋 {entry.query_name}</div>' if entry.query_name else ''}
                        <div style="font-size: 0.9rem; color: #9CA3AF; margin-bottom: 4px;">
                            <strong>Timing:</strong> {timing_summary} | <strong>Results:</strong> {equiv_summary} | <strong>Timestamp:</strong> {entry.timestamp}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

        with tab_schema:
            st.subheader("Database Schema Explorer")
            tables_dict = schema_info.tables
            if not tables_dict:
                st.info("No user tables found in the uploaded SQLite database.")
            else:
                selected_tbl = st.selectbox("Select Table to Inspect:", list(tables_dict.keys()), key="schema_tbl_pre_analysis")
                if selected_tbl:
                    tschema = tables_dict[selected_tbl]
                    st.write(f"**Table:** `{tschema.name}` | **Rows:** `{tschema.row_count:,}`")
                    st.write("#### Columns")
                    col_df = pd.DataFrame([
                        {
                            "Column Name": c.name,
                            "Data Type": c.data_type,
                            "Not Null": c.not_null,
                            "Primary Key": c.pk
                        } for c in tschema.columns
                    ])
                    st.dataframe(col_df, use_container_width=True)
                    st.write("#### Indexes")
                    if tschema.indexes:
                        idx_df = pd.DataFrame([
                            {
                                "Index Name": idx.name,
                                "Unique": idx.is_unique,
                                "Columns": ", ".join(idx.columns)
                            } for idx in tschema.indexes
                        ])
                        st.dataframe(idx_df, use_container_width=True)
                    else:
                        st.info("No secondary indexes defined on this table.")
