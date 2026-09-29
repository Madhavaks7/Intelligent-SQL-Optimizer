import pytest
import sqlite3
import tempfile
import os
import statistics
from unittest.mock import MagicMock, patch

from database.database import DatabaseManager
from optimizer.optimization_engine import OptimizationEngine
from performance.benchmark import PerformanceBenchmarker
from analyzer.history_manager import HistoryManager
from models.analysis_models import (
    Recommendation, BenchmarkMetrics, BenchmarkResult, EquivalenceResult, HistoryEntry
)


@pytest.fixture
def ecommerce_bench_db():
    """Creates a temporary SQLite database populated with test customer and order records."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_bench_ecom.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name TEXT,
            city TEXT,
            age INTEGER,
            country TEXT
        );
    """)

    cur.execute("""
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            order_date TEXT,
            total_amount REAL,
            status TEXT
        );
    """)

    # Populate 200 customers
    customers_data = [
        (i, f"Customer_{i}", "London" if i % 4 == 0 else ("Paris" if i % 4 == 1 else "Berlin"), 20 + (i % 50), "UK" if i % 4 == 0 else "EU")
        for i in range(1, 201)
    ]
    cur.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?);", customers_data)

    # Populate 500 orders
    orders_data = [
        (i, (i % 200) + 1, "2026-01-01", 50.0 + (i * 2.5), "Completed" if i % 2 == 0 else "Pending")
        for i in range(1, 501)
    ]
    cur.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?);", orders_data)

    conn.commit()
    conn.close()

    db_mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(db_mgr)

    yield engine, db_mgr, db_path

    # Teardown
    try:
        os.remove(db_path)
        os.rmdir(temp_dir)
    except Exception:
        pass


class TestRobustMultiRunBenchmarking:
    """Test suite for Phase 2 — Batch 6 Robust Multi-Run Benchmarking."""

    def test_default_warmup_and_measured_runs_configuration(self, ecommerce_bench_db):
        """1. Verifies default configuration uses 3 warm-up runs and 7 measured runs."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs
        )

        assert res.warmup_runs == 3
        assert res.measured_runs == 7
        assert len(res.original_warmup_times_ms) == 3
        assert len(res.optimized_warmup_times_ms) == 3
        assert len(res.original_times_ms) == 7
        assert len(res.optimized_times_ms) == 7

    def test_warmup_runs_excluded_from_measured_statistics(self, ecommerce_bench_db):
        """2. Verifies that warm-up run timings are excluded from measured statistics."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs,
            warmup_runs=3,
            measured_runs=5
        )

        assert len(res.original_metrics.raw_times_ms) == 5
        assert len(res.original_metrics.warmup_times_ms) == 3
        assert res.original_metrics.raw_times_ms == res.original_times_ms
        assert res.original_metrics.warmup_times_ms == res.original_warmup_times_ms

    def test_median_calculation_correctness(self, ecommerce_bench_db):
        """3. Verifies that median execution time is calculated using statistics.median."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs,
            measured_runs=7
        )

        expected_orig_median = round(float(statistics.median(res.original_times_ms)), 2)
        expected_opt_median = round(float(statistics.median(res.optimized_times_ms)), 2)

        assert res.original_median_ms == expected_orig_median
        assert res.optimized_median_ms == expected_opt_median
        assert res.original_metrics.median_time_ms == expected_orig_median
        assert res.optimized_metrics.median_time_ms == expected_opt_median
        assert res.original_metrics.exec_time_ms == expected_orig_median
        assert res.optimized_metrics.exec_time_ms == expected_opt_median

    def test_min_and_max_calculation_correctness(self, ecommerce_bench_db):
        """4. Verifies that minimum and maximum execution times are properly computed."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs,
            measured_runs=7
        )

        assert res.original_min_ms == round(float(min(res.original_times_ms)), 2)
        assert res.original_max_ms == round(float(max(res.original_times_ms)), 2)
        assert res.optimized_min_ms == round(float(min(res.optimized_times_ms)), 2)
        assert res.optimized_max_ms == round(float(max(res.optimized_times_ms)), 2)

    def test_percentage_difference_calculation(self, ecommerce_bench_db):
        """5. Verifies percentage difference formula: ((opt_median - orig_median) / orig_median) * 100."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs
        )

        if res.original_median_ms > 0:
            expected_pct = round(((res.optimized_median_ms - res.original_median_ms) / res.original_median_ms) * 100.0, 2)
            assert res.percentage_difference == expected_pct

    def test_classification_improved_when_faster_than_tolerance(self):
        """6. Verifies PERFORMANCE_IMPROVED classification when runtime decreases (negative pct < -tolerance)."""
        orig_times = [10.0, 10.2, 9.8, 10.0, 10.1, 9.9, 10.0]
        opt_times = [4.8, 5.0, 5.2, 5.0, 4.9, 5.1, 5.0]

        orig_median = float(statistics.median(orig_times))
        opt_median = float(statistics.median(opt_times))
        pct_diff = ((opt_median - orig_median) / orig_median) * 100.0

        assert pct_diff == -50.0
        assert pct_diff < -5.0

    def test_classification_regression_when_slower_than_tolerance(self):
        """7. Verifies PERFORMANCE_REGRESSION classification when runtime increases (positive pct > tolerance)."""
        orig_times = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
        opt_times = [8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0]

        orig_median = float(statistics.median(orig_times))
        opt_median = float(statistics.median(opt_times))
        pct_diff = ((opt_median - orig_median) / orig_median) * 100.0

        assert pct_diff == 60.0
        assert pct_diff > 5.0

    def test_classification_no_measurable_improvement_within_tolerance(self):
        """8. Verifies NO_MEASURABLE_IMPROVEMENT classification within ±5.0% tolerance band."""
        orig_times = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0]
        opt_times = [9.8, 9.8, 9.8, 9.8, 9.8, 9.8, 9.8]

        orig_median = float(statistics.median(orig_times))
        opt_median = float(statistics.median(opt_times))
        pct_diff = round(((opt_median - orig_median) / orig_median) * 100.0, 2)

        assert pct_diff == -2.0
        assert -5.0 <= pct_diff <= 5.0

    def test_batch_7_4_difference_and_speedup_semantics(self):
        """8b. Batch 7.4: Verifies faster, slower, equal difference, percentage, and speedup semantics."""
        # 1. Faster case: 8.0 ms -> 5.0 ms
        orig_fast = 8.0
        opt_fast = 5.0
        diff_fast = opt_fast - orig_fast
        pct_fast = round(((opt_fast - orig_fast) / orig_fast) * 100.0, 1)
        speedup_fast = round(orig_fast / opt_fast, 2)

        assert diff_fast == -3.0
        assert pct_fast == -37.5
        assert speedup_fast == 1.60
        assert speedup_fast > 1.0

        # 2. Slower case: 5.26 ms -> 8.06 ms
        orig_slow = 5.26
        opt_slow = 8.06
        diff_slow = round(opt_slow - orig_slow, 2)
        pct_slow = round(((opt_slow - orig_slow) / orig_slow) * 100.0, 1)
        speedup_slow = round(orig_slow / opt_slow, 2)

        assert diff_slow == 2.80
        assert pct_slow == 53.2
        assert speedup_slow == 0.65
        assert speedup_slow < 1.0

        # 3. Equal case: 5.0 ms -> 5.0 ms
        orig_eq = 5.0
        opt_eq = 5.0
        diff_eq = opt_eq - orig_eq
        pct_eq = round(((opt_eq - orig_eq) / orig_eq) * 100.0, 1)
        speedup_eq = round(orig_eq / opt_eq, 2)

        assert diff_eq == 0.0
        assert pct_eq == 0.0
        assert speedup_eq == 1.00

    def test_custom_tolerance_parameter_respected(self, ecommerce_bench_db):
        """9. Verifies that custom tolerance_percent threshold is respected."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        # Run with large tolerance of 50%
        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs,
            tolerance_percent=50.0
        )

        if abs(res.percentage_difference) <= 50.0 and res.equivalence_result.is_equivalent:
            assert res.performance_classification == "NO_MEASURABLE_IMPROVEMENT"

    def test_result_equivalence_priority_over_timing(self, ecommerce_bench_db):
        """10. Verifies result equivalence is a strict prerequisite before performance approval."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        # Recommendation with bad suggested query that alters results
        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        # Suggested query filters differently (returns 0 rows vs 50 rows)
        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs,
            suggested_query="SELECT * FROM customers WHERE city = 'NonExistentCity';"
        )

        assert res.equivalence_result.is_equivalent is False
        assert res.benchmark_applied is False
        assert "Result Equivalence Failed" in res.note

    def test_planner_unused_index_handled_cleanly(self, ecommerce_bench_db):
        """11. Verifies that if SQLite query planner ignores the index, benchmark_applied is False."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        # Index on country, but query filters on primary key customer_id = 1
        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(country)",
            ddl="CREATE INDEX idx_customers_country ON customers(country);",
            table="customers",
            columns=["country"],
            strategy="INDEX_BASED"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE customer_id = 1;",
            recommendations=recs
        )

        # Primary key lookup already does SEARCH on INTEGER PRIMARY KEY, idx_customers_country is not used
        assert res.benchmark_applied is False
        assert "SQLite query planner selected a different execution plan" in res.note

    def test_candidate_identity_and_strategy_propagation(self, ecommerce_bench_db):
        """12. Verifies candidate name, strategy, and candidate_ddl are preserved in BenchmarkResult."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Composite index on customers(city, age)",
            ddl="CREATE INDEX idx_cust_city_age ON customers(city, age);",
            table="customers",
            columns=["city", "age"],
            strategy="COMPOSITE_INDEX"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London' AND age > 25;",
            recommendations=recs
        )

        assert res.candidate_name == "customers(city, age)"
        assert res.strategy == "COMPOSITE_INDEX"
        assert res.candidate_ddl == "CREATE INDEX idx_cust_city_age ON customers(city, age);"

    def test_original_database_protection(self, ecommerce_bench_db):
        """13. Verifies original database remains read-only and no indexes are created in it."""
        engine, db_mgr, db_path = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs
        )

        # Verify on the actual database file
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type = 'index';")
        indexes = [row[0] for row in cur.fetchall()]
        conn.close()

        assert "idx_customers_city" not in indexes

    def test_history_manager_records_multi_run_metrics(self, ecommerce_bench_db):
        """14. Verifies HistoryManager.record_test_result persists all multi-run fields."""
        engine, db_mgr, _ = ecommerce_bench_db
        hm = HistoryManager()

        query = "SELECT * FROM customers WHERE city = 'London';"
        hm.record_analysis(
            db_name="test.db",
            query=query,
            optimization_name="customers(city)",
            strategy="INDEX_BASED",
            query_name="Query 1"
        )

        benchmarker = PerformanceBenchmarker(db_mgr)
        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        b_res = benchmarker.run_benchmark(query=query, recommendations=recs)

        entry = hm.record_test_result(
            db_name="test.db",
            query=query,
            optimization_name="customers(city)",
            strategy="INDEX_BASED",
            candidate_ddl="CREATE INDEX idx_customers_city ON customers(city);",
            benchmark_result=b_res,
            query_name="Query 1"
        )

        assert entry.is_tested is True
        assert entry.warmup_runs == 3
        assert entry.measured_runs == 7
        assert entry.original_median_ms == b_res.original_median_ms
        assert entry.optimized_median_ms == b_res.optimized_median_ms
        assert entry.original_min_ms == b_res.original_min_ms
        assert entry.optimized_min_ms == b_res.optimized_min_ms
        assert entry.original_max_ms == b_res.original_max_ms
        assert entry.optimized_max_ms == b_res.optimized_max_ms
        assert entry.percentage_difference == b_res.percentage_difference
        assert entry.performance_classification == b_res.performance_classification
        assert len(entry.original_times_ms) == 7
        assert len(entry.optimized_times_ms) == 7

    def test_history_manager_legacy_backward_compatibility(self):
        """15. Verifies HistoryEntry without multi-run fields renders/operates safely."""
        legacy_entry = HistoryEntry(
            id="leg123",
            timestamp="2026-09-01 12:00:00",
            db_name="legacy.db",
            query="SELECT * FROM customers;",
            optimization_name="customers(id)",
            strategy="INDEX_BASED",
            is_tested=True,
            orig_time_ms=12.5,
            opt_time_ms=2.1,
            speedup_factor=5.95,
            validation_status="✓ Validated Performance Improvement"
        )

        assert legacy_entry.warmup_runs is None
        assert legacy_entry.measured_runs is None
        assert legacy_entry.original_median_ms is None
        assert legacy_entry.orig_time_ms == 12.5
        assert legacy_entry.speedup_factor == 5.95

    def test_history_repeated_test_creates_separate_record(self, ecommerce_bench_db):
        """16. Verifies repeated benchmarking creates separate history records."""
        engine, db_mgr, _ = ecommerce_bench_db
        hm = HistoryManager()
        query = "SELECT * FROM customers WHERE city = 'London';"

        benchmarker = PerformanceBenchmarker(db_mgr)
        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        b_res1 = benchmarker.run_benchmark(query=query, recommendations=recs)
        hm.record_test_result("test.db", query, "customers(city)", "INDEX_BASED", None, b_res1)

        b_res2 = benchmarker.run_benchmark(query=query, recommendations=recs)
        hm.record_test_result("test.db", query, "customers(city)", "INDEX_BASED", None, b_res2)

        assert len(hm.get_entries()) == 2

    def test_unsafe_query_rejection_in_benchmark(self, ecommerce_bench_db):
        """17. Verifies unsafe non-SELECT queries are blocked prior to benchmarking."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        res = benchmarker.run_benchmark(
            query="DROP TABLE customers;",
            recommendations=[]
        )

        assert res.benchmark_applied is False
        assert "Only SELECT queries are permitted" in res.note

    def test_no_recommendations_ddl_handling(self, ecommerce_bench_db):
        """18. Verifies benchmark returns cleanly when query requires no index."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE customer_id = 5;",
            recommendations=[]
        )

        assert res.benchmark_applied is False
        assert "No database indexes were required" in res.note
        assert res.warmup_runs == 3
        assert res.measured_runs == 7
        assert len(res.original_times_ms) == 7

    def test_custom_iterations_backward_compatibility(self, ecommerce_bench_db):
        """19. Verifies iterations parameter maps to measured_runs for backward compatibility."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs,
            iterations=10
        )

        assert res.measured_runs == 10
        assert len(res.original_times_ms) == 10
        assert len(res.optimized_times_ms) == 10

    def test_order_by_index_strategy_multi_run_benchmarking(self, ecommerce_bench_db):
        """20. Verifies ORDER_BY_INDEX strategy runs multi-run benchmarking and eliminates sort."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_02",
            type="CREATE INDEX",
            description="Index on orders(customer_id, order_date)",
            ddl="CREATE INDEX idx_orders_cust_date ON orders(customer_id, order_date);",
            table="orders",
            columns=["customer_id", "order_date"],
            strategy="ORDER_BY_INDEX"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM orders WHERE customer_id = 5 ORDER BY order_date;",
            recommendations=recs
        )

        assert res.strategy == "ORDER_BY_INDEX"
        assert res.candidate_name == "orders(customer_id, order_date)"
        assert res.warmup_runs == 3
        assert res.measured_runs == 7
        assert res.original_median_ms is not None
        assert res.optimized_median_ms is not None
        assert res.equivalence_result.is_equivalent is True

    def test_covering_index_strategy_multi_run_benchmarking(self, ecommerce_bench_db):
        """21. Verifies COVERING_INDEX strategy runs multi-run benchmarking."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_03",
            type="CREATE INDEX",
            description="Covering index on customers(city, name)",
            ddl="CREATE INDEX idx_cust_cov ON customers(city, name);",
            table="customers",
            columns=["city", "name"],
            strategy="COVERING_INDEX"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT name FROM customers WHERE city = 'London';",
            recommendations=recs
        )

        assert res.strategy == "COVERING_INDEX"
        assert res.candidate_name == "customers(city, name)"
        assert res.warmup_runs == 3
        assert res.measured_runs == 7
        assert res.equivalence_result.is_equivalent is True

    def test_zero_warmup_runs_edge_case(self, ecommerce_bench_db):
        """22. Verifies warmup_runs=0 edge case executes correctly without errors."""
        engine, db_mgr, _ = ecommerce_bench_db
        benchmarker = PerformanceBenchmarker(db_mgr)

        recs = [Recommendation(
            id="REC_01",
            type="CREATE INDEX",
            description="Index on customers(city)",
            ddl="CREATE INDEX idx_customers_city ON customers(city);",
            table="customers",
            columns=["city"],
            strategy="INDEX_BASED"
        )]

        res = benchmarker.run_benchmark(
            query="SELECT * FROM customers WHERE city = 'London';",
            recommendations=recs,
            warmup_runs=0,
            measured_runs=5
        )

        assert res.warmup_runs == 0
        assert len(res.original_warmup_times_ms) == 0
        assert len(res.optimized_warmup_times_ms) == 0
        assert len(res.original_times_ms) == 5
        assert len(res.optimized_times_ms) == 5
        assert res.equivalence_result.is_equivalent is True
