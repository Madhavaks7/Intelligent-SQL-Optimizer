import pytest
import sqlite3
import os
import tempfile
import hashlib
from analyzer.history_manager import HistoryManager
from models.analysis_models import (
    BenchmarkResult, BenchmarkMetrics, Recommendation, EquivalenceResult, PlanNode
)
from database.database import DatabaseManager
from optimizer.optimization_engine import OptimizationEngine
from performance.benchmark import PerformanceBenchmarker


def get_file_hash(filepath: str) -> str:
    """Calculates SHA256 hash of a file to verify read-only safety."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()


@pytest.fixture
def ecommerce_db():
    """Creates a temporary test ecommerce database fixture."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_p2b3_ecom_")
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


# =====================================================================
# UNIT & INTEGRATION TESTS: Batch 3 Optimization History
# =====================================================================

def test_empty_history_clean():
    """Verifies that an empty or newly created history manager returns an empty list without error."""
    hm = HistoryManager()
    assert hm.get_entries() == []
    assert len(hm.get_entries()) == 0


def test_analysis_creates_analyzed_not_tested_entry():
    """Verifies that performing an analysis creates an 'Analyzed — Not Tested' history entry."""
    hm = HistoryManager()
    entry = hm.record_analysis(
        db_name="test.db",
        query="SELECT * FROM customers WHERE city = 'Bengaluru';",
        optimization_name="customers(city)",
        strategy="INDEX_BASED",
        candidate_ddl="CREATE INDEX idx_customers_city ON customers(city);"
    )

    assert entry is not None
    assert entry.is_tested is False
    assert entry.validation_status == "Analyzed — Not Tested"
    assert entry.optimization_name == "customers(city)"
    assert entry.strategy == "INDEX_BASED"
    assert entry.candidate_ddl == "CREATE INDEX idx_customers_city ON customers(city);"
    assert len(hm.get_entries()) == 1


def test_completed_optimization_test_updates_history():
    """Verifies that completing a test updates the pending entry with test metrics."""
    hm = HistoryManager()
    query = "SELECT * FROM customers WHERE city = 'Bengaluru';"
    hm.record_analysis(
        db_name="test.db",
        query=query,
        optimization_name="customers(city)",
        strategy="INDEX_BASED",
        candidate_ddl="CREATE INDEX idx_customers_city ON customers(city);"
    )

    # Mock completed benchmark result
    b_res = BenchmarkResult(
        original_metrics=BenchmarkMetrics(exec_time_ms=50.0, rows_returned=50, plan_nodes=[], plan_summary="SCAN customers"),
        optimized_metrics=BenchmarkMetrics(exec_time_ms=10.0, rows_returned=50, plan_nodes=[], plan_summary="SEARCH customers USING INDEX idx_customers_city"),
        speedup_factor=5.0,
        time_saved_ms=40.0,
        indexes_created=["CREATE INDEX idx_customers_city ON customers(city)"],
        benchmark_applied=True,
        plan_changed=True,
        plan_change_desc="SCAN → SEARCH",
        equivalence_result=EquivalenceResult(
            is_equivalent=True,
            columns_match=True,
            column_count_match=True,
            row_count_match=True,
            values_match=True,
            order_match=True,
            has_order_by=False,
            orig_row_count=50,
            opt_row_count=50,
            orig_col_count=4,
            opt_col_count=4,
            explanation="The optimized query returned the same result set as the original query."
        ),
        note="Performance improved."
    )

    tested_entry = hm.record_test_result(
        db_name="test.db",
        query=query,
        optimization_name="customers(city)",
        strategy="INDEX_BASED",
        candidate_ddl="CREATE INDEX idx_customers_city ON customers(city);",
        benchmark_result=b_res
    )

    assert tested_entry.is_tested is True
    assert tested_entry.orig_time_ms == 50.0
    assert tested_entry.opt_time_ms == 10.0
    assert tested_entry.speedup_factor == 5.0
    assert tested_entry.is_equivalent is True
    assert tested_entry.validation_status == "✓ Validated Performance Improvement"
    assert tested_entry.orig_plan == "SCAN customers"
    assert tested_entry.opt_plan == "SEARCH customers USING INDEX idx_customers_city"
    assert len(hm.get_entries()) == 1


def test_result_equivalence_failure_recorded_in_history():
    """Verifies that result-equivalence failure is recorded as '⚠ Validation Failed' in history."""
    hm = HistoryManager()
    query = "SELECT * FROM orders WHERE status = 'completed';"
    hm.record_analysis("test.db", query, "orders(status)", "INDEX_BASED")

    # Benchmark result with equivalence failure
    b_res = BenchmarkResult(
        original_metrics=BenchmarkMetrics(exec_time_ms=30.0, rows_returned=100, plan_nodes=[], plan_summary="SCAN orders"),
        optimized_metrics=BenchmarkMetrics(exec_time_ms=5.0, rows_returned=90, plan_nodes=[], plan_summary="SEARCH orders"),
        speedup_factor=6.0,
        time_saved_ms=25.0,
        benchmark_applied=False,
        equivalence_result=EquivalenceResult(
            is_equivalent=False,
            columns_match=True,
            column_count_match=True,
            row_count_match=False,
            values_match=False,
            order_match=False,
            has_order_by=False,
            orig_row_count=100,
            opt_row_count=90,
            orig_col_count=5,
            opt_col_count=5,
            explanation="Row count mismatch: Original returned 100 rows, Optimized returned 90 rows.",
            mismatch_reason="Different row count"
        ),
        note="Result Equivalence Failed."
    )

    tested_entry = hm.record_test_result("test.db", query, "orders(status)", "INDEX_BASED", None, b_res)

    assert tested_entry.is_tested is True
    assert tested_entry.is_equivalent is False
    assert tested_entry.validation_status == "⚠ Validation Failed"
    assert "Row count mismatch" in tested_entry.equivalence_details


def test_slower_equivalent_records_performance_regression():
    """Verifies that an equivalent but slower result records '✗ Performance Regression'."""
    hm = HistoryManager()
    query = "SELECT * FROM attendance WHERE status = 'Absent';"
    hm.record_analysis("test.db", query, "attendance(status)", "INDEX_BASED")

    b_res = BenchmarkResult(
        original_metrics=BenchmarkMetrics(exec_time_ms=40.0, rows_returned=25, plan_nodes=[], plan_summary="SCAN attendance"),
        optimized_metrics=BenchmarkMetrics(exec_time_ms=50.0, rows_returned=25, plan_nodes=[], plan_summary="SEARCH attendance"),
        speedup_factor=0.8,
        time_saved_ms=0.0,
        benchmark_applied=True,
        equivalence_result=EquivalenceResult(
            is_equivalent=True,
            columns_match=True,
            column_count_match=True,
            row_count_match=True,
            values_match=True,
            order_match=True,
            has_order_by=False,
            orig_row_count=25,
            opt_row_count=25,
            orig_col_count=3,
            opt_col_count=3,
            explanation="Results are equivalent."
        )
    )

    entry = hm.record_test_result("test.db", query, "attendance(status)", "INDEX_BASED", None, b_res)
    assert entry.validation_status == "✗ Performance Regression"


def test_essentially_unchanged_records_no_measurable_improvement():
    """Verifies that an equivalent result with < 3% diff records '≈ No Measurable Performance Improvement'."""
    hm = HistoryManager()
    query = "SELECT * FROM customers WHERE city = 'Delhi';"
    hm.record_analysis("test.db", query, "customers(city)", "INDEX_BASED")

    b_res = BenchmarkResult(
        original_metrics=BenchmarkMetrics(exec_time_ms=40.0, rows_returned=50, plan_nodes=[], plan_summary="SCAN customers"),
        optimized_metrics=BenchmarkMetrics(exec_time_ms=39.5, rows_returned=50, plan_nodes=[], plan_summary="SEARCH customers"),
        speedup_factor=1.01,
        time_saved_ms=0.5,
        benchmark_applied=True,
        equivalence_result=EquivalenceResult(
            is_equivalent=True,
            columns_match=True,
            column_count_match=True,
            row_count_match=True,
            values_match=True,
            order_match=True,
            has_order_by=False,
            orig_row_count=50,
            opt_row_count=50,
            orig_col_count=4,
            opt_col_count=4,
            explanation="Results are equivalent."
        )
    )

    entry = hm.record_test_result("test.db", query, "customers(city)", "INDEX_BASED", None, b_res)
    assert entry.validation_status == "≈ No Measurable Performance Improvement"


def test_repeated_tests_create_separate_history_entries():
    """Verifies that running multiple test runs on the same candidate creates separate distinct history entries."""
    hm = HistoryManager()
    query = "SELECT * FROM attendance WHERE status = 'Absent';"

    # Initial Analysis
    hm.record_analysis("test.db", query, "attendance(status)", "INDEX_BASED")

    # Run 1
    b_res1 = BenchmarkResult(
        original_metrics=BenchmarkMetrics(exec_time_ms=44.54, rows_returned=50, plan_nodes=[], plan_summary="SCAN"),
        optimized_metrics=BenchmarkMetrics(exec_time_ms=40.48, rows_returned=50, plan_nodes=[], plan_summary="SEARCH"),
        speedup_factor=1.10,
        equivalence_result=EquivalenceResult(True, True, True, True, True, True, False, 50, 50, 3, 3, "Same results.")
    )
    hm.record_test_result("test.db", query, "attendance(status)", "INDEX_BASED", None, b_res1)

    assert len(hm.get_entries()) == 1
    assert hm.get_entries()[0].orig_time_ms == 44.54
    assert hm.get_entries()[0].opt_time_ms == 40.48

    # Run 2 (repeated test on same candidate)
    b_res2 = BenchmarkResult(
        original_metrics=BenchmarkMetrics(exec_time_ms=40.53, rows_returned=50, plan_nodes=[], plan_summary="SCAN"),
        optimized_metrics=BenchmarkMetrics(exec_time_ms=40.21, rows_returned=50, plan_nodes=[], plan_summary="SEARCH"),
        speedup_factor=1.01,
        equivalence_result=EquivalenceResult(True, True, True, True, True, True, False, 50, 50, 3, 3, "Same results.")
    )
    hm.record_test_result("test.db", query, "attendance(status)", "INDEX_BASED", None, b_res2)

    # Both runs must be preserved as separate history entries
    entries = hm.get_entries()
    assert len(entries) == 2
    assert entries[0].orig_time_ms == 44.54
    assert entries[0].opt_time_ms == 40.48
    assert entries[1].orig_time_ms == 40.53
    assert entries[1].opt_time_ms == 40.21


def test_database_switching_clears_history():
    """Verifies that clearing history removes all entries and prevents leaking previous DB data."""
    hm = HistoryManager()
    hm.record_analysis("db1.db", "SELECT 1;", "No Candidate", "NO_OPTIMIZATION_REQUIRED")
    assert len(hm.get_entries()) == 1

    # Switch database -> clear
    hm.clear()
    assert len(hm.get_entries()) == 0

    hm.record_analysis("db2.db", "SELECT 2;", "No Candidate", "NO_OPTIMIZATION_REQUIRED")
    entries = hm.get_entries()
    assert len(entries) == 1
    assert entries[0].db_name == "db2.db"
    assert entries[0].query == "SELECT 2;"


def test_no_history_written_to_sqlite_database(ecommerce_db):
    """Verifies that analyzing and testing with history never alters the original SQLite database file."""
    engine, db_path = ecommerce_db
    initial_hash = get_file_hash(db_path)

    hm = HistoryManager()
    query = "SELECT * FROM customers WHERE city = 'Bengaluru';"
    res = engine.analyze_query(query, run_benchmark=False)

    cand_name = HistoryManager.extract_candidate_name(res.recommendations)
    hm.record_analysis("ecommerce.db", query, cand_name, res.optimization_strategy)

    benchmarker = PerformanceBenchmarker(engine.db_manager)
    b_res = benchmarker.run_benchmark(query, res.recommendations)

    hm.record_test_result("ecommerce.db", query, cand_name, res.optimization_strategy, None, b_res)

    assert len(hm.get_entries()) == 1
    assert hm.get_entries()[0].is_tested is True

    # Check database file hash
    final_hash = get_file_hash(db_path)
    assert initial_hash == final_hash, "Original SQLite database file was modified!"
