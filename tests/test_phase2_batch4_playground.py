"""
Batch 4 Automated Tests: Query Playground & Multi-Query Workflow
================================================================

Tests 1–14 cover all 14 required scenarios from the Batch 4 specification.

These tests operate directly on PlaygroundManager and the associated data model.
No Streamlit session state is exercised here — UI integration is verified manually.
"""
import pytest
import sqlite3
import os
import tempfile

from analyzer.playground_manager import PlaygroundManager
from analyzer.history_manager import HistoryManager
from database.database import DatabaseManager
from optimizer.optimization_engine import OptimizationEngine
from performance.benchmark import PerformanceBenchmarker
from models.analysis_models import (
    AnalysisResult, ParsedQuery, DatabaseSchema, BenchmarkResult,
    BenchmarkMetrics, EquivalenceResult, PlanNode, PlaygroundQuery,
)


# ================================================================
# Helpers / Fixtures
# ================================================================

def _make_analysis_result(sql: str = "SELECT 1") -> AnalysisResult:
    """Creates a minimal AnalysisResult stub for testing state storage."""
    parsed = ParsedQuery(raw_sql=sql)
    schema = DatabaseSchema(db_path=":memory:")
    return AnalysisResult(
        query=sql,
        parsed_query=parsed,
        schema=schema,
        plan_nodes=[],
        findings=[],
        recommendations=[],
        optimization_strategy="NO_OPTIMIZATION_REQUIRED",
    )


def _make_benchmark_result() -> BenchmarkResult:
    """Creates a minimal BenchmarkResult stub."""
    metrics = BenchmarkMetrics(exec_time_ms=10.0, rows_returned=5, plan_nodes=[], plan_summary="SCAN t")
    return BenchmarkResult(
        original_metrics=metrics,
        optimized_metrics=metrics,
        speedup_factor=1.0,
    )


@pytest.fixture
def pm() -> PlaygroundManager:
    """Returns a fresh PlaygroundManager for each test."""
    return PlaygroundManager()


@pytest.fixture
def ecommerce_db():
    """Creates a temporary ecommerce test database."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_p2b4_ecom_")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            city TEXT NOT NULL
        );
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            total_amount REAL NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
        CREATE INDEX idx_orders_customer_id ON orders(customer_id);
    """)
    customers_data = [(f"First_{i}", f"Last_{i}", "Bengaluru" if i % 2 == 0 else "Delhi") for i in range(1, 101)]
    conn.executemany("INSERT INTO customers (first_name, last_name, city) VALUES (?, ?, ?)", customers_data)
    orders_data = [(i % 100 + 1, "2025-01-15", float(i * 50), "completed") for i in range(1, 201)]
    conn.executemany("INSERT INTO orders (customer_id, order_date, total_amount, status) VALUES (?, ?, ?, ?)", orders_data)
    conn.commit()
    conn.close()

    mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(mgr)
    yield engine, db_path

    DatabaseManager.cleanup_temp_db(db_path)


@pytest.fixture
def university_db():
    """Creates a temporary university test database."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_p2b4_univ_")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE attendance (
            attendance_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            status TEXT NOT NULL
        );
    """)
    a_data = [(i, "Absent" if i % 4 == 0 else "Present") for i in range(1, 200)]
    conn.executemany("INSERT INTO attendance (student_id, status) VALUES (?, ?)", a_data)
    conn.commit()
    conn.close()

    mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(mgr)
    yield engine, db_path

    DatabaseManager.cleanup_temp_db(db_path)


# ================================================================
# TEST 1: A new query starts with clean state
# ================================================================

def test_1_new_query_starts_with_clean_state(pm):
    """A new query must start with no SQL, no analysis, no test, status 'not_analyzed'."""
    q = pm.create_query()
    assert q.sql == ""
    assert q.analysis_result is None
    assert q.test_result is None
    assert q.status == "not_analyzed"
    assert q.name.startswith("Query ")


# ================================================================
# TEST 2: Multiple queries can coexist in the playground
# ================================================================

def test_2_multiple_queries_can_coexist(pm):
    """Multiple queries can be created and are all present simultaneously."""
    q1 = pm.create_query("Query A")
    q2 = pm.create_query("Query B")
    q3 = pm.create_query("Query C")

    all_q = pm.get_all()
    assert len(all_q) == 3
    ids = [q.id for q in all_q]
    assert q1.id in ids
    assert q2.id in ids
    assert q3.id in ids


# ================================================================
# TEST 3: Each query receives a unique internal identifier
# ================================================================

def test_3_unique_internal_identifiers(pm):
    """Every created query must have a distinct id."""
    ids = [pm.create_query().id for _ in range(10)]
    assert len(set(ids)) == 10, "Duplicate IDs found across playground queries"


# ================================================================
# TEST 4: Switching Query A → Query B does not mix state
# ================================================================

def test_4_switching_does_not_mix_state(pm):
    """After setting analysis on Query A and switching to Query B, Query B has no analysis."""
    qa = pm.create_query("A")
    qb = pm.create_query("B")

    # Analyze Query A
    pm.set_active(qa.id)
    res_a = _make_analysis_result("SELECT 1")
    pm.set_analysis(qa.id, res_a)

    # Switch to Query B
    pm.set_active(qb.id)
    active = pm.get_active()

    assert active.id == qb.id
    assert active.analysis_result is None, "Query B must not inherit Query A's analysis"
    assert active.status == "not_analyzed"


# ================================================================
# TEST 5: Returning to Query A restores its state
# ================================================================

def test_5_returning_restores_state(pm):
    """Returning to Query A after visiting Query B restores Query A's analysis."""
    qa = pm.create_query("A")
    qb = pm.create_query("B")

    # Analyze Query A
    pm.set_active(qa.id)
    res_a = _make_analysis_result("SELECT 1")
    pm.set_analysis(qa.id, res_a)

    # Switch to B, then back to A
    pm.set_active(qb.id)
    pm.set_active(qa.id)
    active = pm.get_active()

    assert active.id == qa.id
    assert active.analysis_result is not None
    assert active.analysis_result.query == "SELECT 1"
    assert active.status == "analyzed"


# ================================================================
# TEST 6: Editing SQL invalidates stale analysis/test state
# ================================================================

def test_6_editing_sql_invalidates_state(pm):
    """Changing a query's SQL must clear its analysis_result, test_result, and reset status."""
    q = pm.create_query()
    pm.update_sql(q.id, "SELECT 1")
    pm.set_analysis(q.id, _make_analysis_result("SELECT 1"))
    pm.set_test_result(q.id, _make_benchmark_result())

    assert pm.get_query(q.id).status == "tested"

    # Now edit the SQL
    pm.update_sql(q.id, "SELECT 2")

    updated = pm.get_query(q.id)
    assert updated.sql == "SELECT 2"
    assert updated.analysis_result is None, "analysis_result must be cleared on SQL edit"
    assert updated.test_result is None, "test_result must be cleared on SQL edit"
    assert updated.status == "not_analyzed"


# ================================================================
# TEST 7: Editing SQL does not delete previous History entries
# ================================================================

def test_7_editing_sql_does_not_delete_history(pm):
    """Updating SQL in the playground must never remove existing HistoryManager entries."""
    hm = HistoryManager()
    q = pm.create_query()
    pm.update_sql(q.id, "SELECT * FROM customers WHERE city = 'Bengaluru';")
    pm.set_analysis(q.id, _make_analysis_result("SELECT * FROM customers WHERE city = 'Bengaluru';"))

    # Record in history (as would happen in the real app)
    hm.record_analysis(
        db_name="test.db",
        query="SELECT * FROM customers WHERE city = 'Bengaluru';",
        optimization_name="customers(city)",
        strategy="INDEX_BASED",
        candidate_ddl="CREATE INDEX idx_customers_city ON customers(city);"
    )
    assert len(hm.get_entries()) == 1

    # User edits the SQL in the playground
    pm.update_sql(q.id, "SELECT * FROM customers WHERE city = 'Mumbai';")

    # Playground state is cleared
    assert pm.get_query(q.id).analysis_result is None

    # But history is untouched
    assert len(hm.get_entries()) == 1
    assert hm.get_entries()[0].query == "SELECT * FROM customers WHERE city = 'Bengaluru';"


# ================================================================
# TEST 8: Deleting a playground query removes active query state
# ================================================================

def test_8_deleting_query_removes_state(pm):
    """Deleting a query removes it and its state from the playground."""
    q = pm.create_query()
    pm.update_sql(q.id, "SELECT 1")
    pm.set_analysis(q.id, _make_analysis_result("SELECT 1"))

    # Create a second query so we can delete the first
    pm.create_query()
    pm.delete_query(q.id)

    assert pm.get_query(q.id) is None, "Deleted query must not exist in the playground"
    assert q.id not in [x.id for x in pm.get_all()]


# ================================================================
# TEST 9: Deleting a query does not delete existing History entries
# ================================================================

def test_9_deleting_query_does_not_delete_history(pm):
    """Deleting a playground query must never remove HistoryManager entries."""
    hm = HistoryManager()
    q = pm.create_query()
    pm.update_sql(q.id, "SELECT 1")

    hm.record_analysis("test.db", "SELECT 1", "No Candidate", "NO_OPTIMIZATION_REQUIRED")
    assert len(hm.get_entries()) == 1

    # Create a second query so deletion is allowed
    pm.create_query()
    pm.delete_query(q.id)

    # History must remain intact
    assert len(hm.get_entries()) == 1
    assert hm.get_entries()[0].query == "SELECT 1"


# ================================================================
# TEST 10: Only the selected query is analyzed — others unchanged
# ================================================================

def test_10_only_selected_query_is_analyzed(pm):
    """Calling set_analysis on the active query must not change other queries' state."""
    qa = pm.create_query("A")
    qb = pm.create_query("B")
    qc = pm.create_query("C")

    pm.set_active(qb.id)
    pm.set_analysis(qb.id, _make_analysis_result("SELECT 2"))

    assert pm.get_query(qa.id).analysis_result is None
    assert pm.get_query(qc.id).analysis_result is None
    assert pm.get_query(qb.id).analysis_result is not None
    assert pm.get_query(qb.id).status == "analyzed"


# ================================================================
# TEST 11: Only the selected query is tested — others unchanged
# ================================================================

def test_11_only_selected_query_is_tested(pm):
    """set_test_result on the active query must not affect other queries."""
    qa = pm.create_query("A")
    qb = pm.create_query("B")

    pm.set_active(qa.id)
    pm.update_sql(qa.id, "SELECT 1")
    pm.set_analysis(qa.id, _make_analysis_result("SELECT 1"))
    pm.set_test_result(qa.id, _make_benchmark_result())

    assert pm.get_query(qa.id).status == "tested"
    assert pm.get_query(qb.id).status == "not_analyzed"
    assert pm.get_query(qb.id).test_result is None


# ================================================================
# TEST 12: New queries do not inherit previous query state
# ================================================================

def test_12_new_queries_do_not_inherit_state(pm):
    """A newly created query must start fresh regardless of other queries' state."""
    qa = pm.create_query("A")
    pm.update_sql(qa.id, "SELECT 1")
    pm.set_analysis(qa.id, _make_analysis_result("SELECT 1"))
    pm.set_test_result(qa.id, _make_benchmark_result())

    # Create a new query
    qb = pm.create_query("B")

    assert qb.sql == ""
    assert qb.analysis_result is None
    assert qb.test_result is None
    assert qb.status == "not_analyzed"


# ================================================================
# TEST 13: Switching databases does not leak queries between databases
# ================================================================

def test_13_database_switch_clears_playground(pm, ecommerce_db, university_db):
    """Calling reset() (triggered by DB switch) must clear all playground queries."""
    engine_ecom, _ = ecommerce_db
    engine_univ, _ = university_db

    # Simulate ecommerce session queries
    qa = pm.create_query("Customer Lookup")
    pm.update_sql(qa.id, "SELECT * FROM customers WHERE city = 'Bengaluru';")
    res_ecom = engine_ecom.analyze_query("SELECT * FROM customers WHERE city = 'Bengaluru';", run_benchmark=False)
    pm.set_analysis(qa.id, res_ecom)

    assert len(pm.get_all()) == 1
    assert pm.get_active().analysis_result is not None

    # Simulate database switch: reset playground
    pm.reset()
    pm.ensure_default()

    # All previous queries are gone
    all_q = pm.get_all()
    for q in all_q:
        assert q.analysis_result is None, "Queries from old DB must not persist after switch"
        assert q.sql == "" or q.sql.strip() == ""

    # University query on new session
    qb = pm.get_active()
    pm.update_sql(qb.id, "SELECT * FROM attendance WHERE status = 'Absent';")
    res_univ = engine_univ.analyze_query("SELECT * FROM attendance WHERE status = 'Absent';", run_benchmark=False)
    pm.set_analysis(qb.id, res_univ)

    assert "attendance" in res_univ.schema.tables
    assert "customers" not in res_univ.schema.tables


# ================================================================
# TEST 14: Existing safety restrictions remain intact
# ================================================================

def test_14_safety_restrictions_remain_intact():
    """DatabaseManager.is_safe_read_query must still reject modifying/destructive SQL."""
    dangerous_queries = [
        "DROP TABLE customers;",
        "DELETE FROM orders WHERE order_id = 1;",
        "UPDATE customers SET city = 'Delhi';",
        "INSERT INTO customers (first_name, last_name, city) VALUES ('A', 'B', 'C');",
        "ALTER TABLE customers ADD COLUMN active INTEGER;",
        "PRAGMA foreign_keys = OFF;",
        "SELECT * FROM customers; SELECT * FROM orders;",
    ]
    for sql in dangerous_queries:
        is_safe, msg = DatabaseManager.is_safe_read_query(sql)
        assert is_safe is False, f"Expected unsafe query to be rejected: {sql}"
        assert msg is not None


# ================================================================
# Additional Batch 4 edge-case tests
# ================================================================

def test_ensure_default_creates_one_query_when_empty(pm):
    """ensure_default() must create exactly one query when the playground is empty."""
    assert len(pm.get_all()) == 0
    pm.ensure_default()
    assert len(pm.get_all()) == 1
    assert pm.get_active() is not None


def test_ensure_default_does_not_add_when_not_empty(pm):
    """ensure_default() must not create additional queries when some exist."""
    pm.create_query("Existing")
    pm.ensure_default()
    assert len(pm.get_all()) == 1


def test_rename_query(pm):
    """rename_query() must update the user-visible name."""
    q = pm.create_query("Original")
    pm.rename_query(q.id, "Customer Lookup")
    assert pm.get_query(q.id).name == "Customer Lookup"


def test_no_sql_change_does_not_invalidate_state(pm):
    """Setting the same SQL text must NOT invalidate existing analysis state."""
    q = pm.create_query()
    pm.update_sql(q.id, "SELECT 1")
    pm.set_analysis(q.id, _make_analysis_result("SELECT 1"))
    assert pm.get_query(q.id).status == "analyzed"

    # Re-setting the same SQL
    pm.update_sql(q.id, "SELECT 1")
    assert pm.get_query(q.id).status == "analyzed", "State must not be cleared for identical SQL"
    assert pm.get_query(q.id).analysis_result is not None


def test_delete_last_query_is_prevented_by_convention(pm):
    """The last query cannot be deleted cleanly — manager leaves playground empty and active_id None."""
    q = pm.create_query()
    pm.delete_query(q.id)
    # After deleting the only query, playground is empty
    assert len(pm.get_all()) == 0
    assert pm.get_active_id() is None
    # ensure_default() should recover
    pm.ensure_default()
    assert len(pm.get_all()) == 1


def test_status_label_reflects_state(pm):
    """status_label() returns correct labels for each state."""
    q = pm.create_query()
    assert pm.status_label(q) == "○ Not Analyzed"

    pm.update_sql(q.id, "SELECT 1")
    pm.set_analysis(q.id, _make_analysis_result("SELECT 1"))
    assert pm.status_label(q) == "✓ Analyzed"

    pm.set_test_result(q.id, _make_benchmark_result())
    assert pm.status_label(q) == "✓ Tested"


def test_integration_analyze_and_test_two_queries(ecommerce_db, university_db):
    """Integration: two queries from different analysis contexts remain isolated end-to-end."""
    engine_ecom, _ = ecommerce_db
    engine_univ, _ = university_db

    pm_local = PlaygroundManager()
    qa = pm_local.create_query("Ecommerce Q")
    qb = pm_local.create_query("University Q")

    # Analyze Query A with ecommerce engine
    pm_local.set_active(qa.id)
    sql_a = "SELECT * FROM customers WHERE city = 'Bengaluru';"
    pm_local.update_sql(qa.id, sql_a)
    res_a = engine_ecom.analyze_query(sql_a, run_benchmark=False)
    pm_local.set_analysis(qa.id, res_a)

    # Analyze Query B with university engine
    pm_local.set_active(qb.id)
    sql_b = "SELECT * FROM attendance WHERE status = 'Absent';"
    pm_local.update_sql(qb.id, sql_b)
    res_b = engine_univ.analyze_query(sql_b, run_benchmark=False)
    pm_local.set_analysis(qb.id, res_b)

    # Return to A: state is isolated
    pm_local.set_active(qa.id)
    active_a = pm_local.get_active()
    assert active_a.analysis_result.query == sql_a
    assert "customers" in active_a.analysis_result.schema.tables
    assert "attendance" not in active_a.analysis_result.schema.tables

    # Switch to B: isolated state
    pm_local.set_active(qb.id)
    active_b = pm_local.get_active()
    assert active_b.analysis_result.query == sql_b
    assert "attendance" in active_b.analysis_result.schema.tables
    assert "customers" not in active_b.analysis_result.schema.tables
