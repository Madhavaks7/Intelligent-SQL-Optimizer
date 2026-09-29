"""
Batch 4 Polish Tests: Originating Query Name in Optimization History
====================================================================

Tests 1–4 cover the four required scenarios from the polish spec:
1. History entry created from a named query stores/displays that name.
2. Renaming the PlaygroundQuery after creation does not change the historical name.
3. Multiple queries with different names produce correctly attributed history entries.
4. Existing history entries without a query_name still render without errors.
"""
import pytest
from analyzer.history_manager import HistoryManager
from analyzer.playground_manager import PlaygroundManager
from models.analysis_models import (
    HistoryEntry, BenchmarkResult, BenchmarkMetrics, EquivalenceResult,
)


def _make_benchmark_result(orig_ms: float = 50.0, opt_ms: float = 10.0) -> BenchmarkResult:
    """Creates a minimal BenchmarkResult stub."""
    orig = BenchmarkMetrics(exec_time_ms=orig_ms, rows_returned=50, plan_nodes=[], plan_summary="SCAN t")
    opt = BenchmarkMetrics(exec_time_ms=opt_ms, rows_returned=50, plan_nodes=[], plan_summary="SEARCH t USING INDEX idx")
    eq = EquivalenceResult(
        is_equivalent=True, columns_match=True, column_count_match=True,
        row_count_match=True, values_match=True, order_match=True,
        has_order_by=False, orig_row_count=50, opt_row_count=50,
        orig_col_count=4, opt_col_count=4,
        explanation="Results are equivalent."
    )
    return BenchmarkResult(
        original_metrics=orig,
        optimized_metrics=opt,
        speedup_factor=round(orig_ms / opt_ms, 2),
        time_saved_ms=orig_ms - opt_ms,
        benchmark_applied=True,
        equivalence_result=eq,
    )


# ================================================================
# TEST 1: History entry created from a named query stores that name
# ================================================================

def test_1_query_name_stored_in_analysis_history_entry():
    """record_analysis() with query_name stores the name as a snapshot on the HistoryEntry."""
    hm = HistoryManager()
    entry = hm.record_analysis(
        db_name="test.db",
        query="SELECT * FROM customers WHERE city = 'Bengaluru';",
        optimization_name="customers(city)",
        strategy="INDEX_BASED",
        candidate_ddl="CREATE INDEX idx_customers_city ON customers(city);",
        query_name="Customer Lookup"
    )

    assert entry.query_name == "Customer Lookup"
    assert hm.get_entries()[0].query_name == "Customer Lookup"


def test_1b_query_name_stored_in_test_result_history_entry():
    """record_test_result() with query_name stores the name on the HistoryEntry."""
    hm = HistoryManager()
    query = "SELECT * FROM customers WHERE city = 'Bengaluru';"
    hm.record_analysis("test.db", query, "customers(city)", "INDEX_BASED", query_name="Customer Lookup")

    entry = hm.record_test_result(
        db_name="test.db",
        query=query,
        optimization_name="customers(city)",
        strategy="INDEX_BASED",
        candidate_ddl="CREATE INDEX idx_customers_city ON customers(city);",
        benchmark_result=_make_benchmark_result(),
        query_name="Customer Lookup"
    )

    assert entry.query_name == "Customer Lookup"
    assert entry.is_tested is True


# ================================================================
# TEST 2: Renaming PlaygroundQuery after history creation does not
#         change the historical name (snapshot semantics)
# ================================================================

def test_2_renaming_playground_query_does_not_update_history():
    """The query_name in a HistoryEntry is a snapshot; renaming the source query has no effect."""
    pm = PlaygroundManager()
    hm = HistoryManager()

    q = pm.create_query("Original Name")
    pm.update_sql(q.id, "SELECT 1;")

    # Record history with the original name
    hm.record_analysis(
        db_name="test.db",
        query="SELECT 1;",
        optimization_name="No Candidate",
        strategy="NO_OPTIMIZATION_REQUIRED",
        query_name=q.name   # snapshot: "Original Name"
    )

    assert hm.get_entries()[0].query_name == "Original Name"

    # Now rename the playground query
    pm.rename_query(q.id, "Renamed Query")
    assert pm.get_query(q.id).name == "Renamed Query"

    # History entry must still show the original name
    assert hm.get_entries()[0].query_name == "Original Name", (
        "Renaming PlaygroundQuery must not retroactively change historical query_name"
    )


# ================================================================
# TEST 3: Multiple queries produce correctly attributed history entries
# ================================================================

def test_3_multiple_queries_produce_correctly_attributed_history():
    """History entries from different playground queries carry their respective query names."""
    hm = HistoryManager()

    hm.record_analysis(
        db_name="test.db",
        query="SELECT * FROM customers WHERE city = 'Bengaluru';",
        optimization_name="customers(city)",
        strategy="INDEX_BASED",
        query_name="Customer Lookup"
    )

    hm.record_test_result(
        db_name="test.db",
        query="SELECT * FROM customers WHERE city = 'Bengaluru';",
        optimization_name="customers(city)",
        strategy="INDEX_BASED",
        candidate_ddl=None,
        benchmark_result=_make_benchmark_result(50.0, 10.0),
        query_name="Customer Lookup"
    )

    # A second query from a different playground slot
    hm.record_analysis(
        db_name="test.db",
        query="SELECT * FROM attendance WHERE status = 'Absent';",
        optimization_name="attendance(status)",
        strategy="INDEX_BASED",
        query_name="Attendance Analysis"
    )

    hm.record_test_result(
        db_name="test.db",
        query="SELECT * FROM attendance WHERE status = 'Absent';",
        optimization_name="attendance(status)",
        strategy="INDEX_BASED",
        candidate_ddl=None,
        benchmark_result=_make_benchmark_result(45.67, 47.33),
        query_name="Attendance Analysis"
    )

    entries = hm.get_entries()
    assert len(entries) == 2

    customer_entry = next(e for e in entries if "customers" in e.query)
    attendance_entry = next(e for e in entries if "attendance" in e.query)

    assert customer_entry.query_name == "Customer Lookup"
    assert attendance_entry.query_name == "Attendance Analysis"


# ================================================================
# TEST 4: Existing history entries without query_name render safely
# ================================================================

def test_4_history_entry_without_query_name_is_safe():
    """HistoryEntry.query_name defaults to None; existing entries without it do not error."""
    hm = HistoryManager()

    # Simulate a legacy call without query_name (as Batch 3 tests do)
    entry = hm.record_analysis(
        db_name="test.db",
        query="SELECT * FROM orders;",
        optimization_name="No Candidate",
        strategy="NO_OPTIMIZATION_REQUIRED"
        # query_name intentionally omitted
    )

    assert entry.query_name is None

    # Simulated display logic: if entry.query_name conditional must not raise
    query_name_display = entry.query_name if entry.query_name else ""
    assert query_name_display == ""

    # Simulated f-string conditional (same pattern used in app.py)
    html_fragment = f'<div>{entry.query_name}</div>' if entry.query_name else ""
    assert html_fragment == ""


def test_4b_test_result_entry_without_query_name_is_safe():
    """record_test_result() without query_name leaves query_name as None safely."""
    hm = HistoryManager()
    query = "SELECT * FROM orders;"
    hm.record_analysis("test.db", query, "No Candidate", "NO_OPTIMIZATION_REQUIRED")

    entry = hm.record_test_result(
        db_name="test.db",
        query=query,
        optimization_name="No Candidate",
        strategy="NO_OPTIMIZATION_REQUIRED",
        candidate_ddl=None,
        benchmark_result=_make_benchmark_result()
        # query_name intentionally omitted
    )

    assert entry.query_name is None
    # Display guard
    label = f"📋 {entry.query_name}" if entry.query_name else ""
    assert label == ""
