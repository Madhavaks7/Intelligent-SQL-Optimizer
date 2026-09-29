import pytest
import sqlite3
import os
import tempfile
import hashlib
from typing import List, Dict, Any
from analyzer.result_validator import ResultValidator
from models.analysis_models import EquivalenceResult
from database.database import DatabaseManager
from optimizer.optimization_engine import OptimizationEngine
from performance.benchmark import PerformanceBenchmarker


def get_file_hash(filepath: str) -> str:
    """Calculates SHA256 hash of a file to verify read-only safety."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()


# =====================================================================
# UNIT TESTS: ResultValidator (Section 26 Test Matrix: Cases A - J)
# =====================================================================

def test_case_a_identical_result_sets():
    """Case A: Identical row count, same columns, same values -> Equivalent (True)."""
    cols = ["id", "name", "val"]
    orig_rows = [{"id": 1, "name": "Alice", "val": 10.5}, {"id": 2, "name": "Bob", "val": 20.0}]
    opt_rows = [{"id": 1, "name": "Alice", "val": 10.5}, {"id": 2, "name": "Bob", "val": 20.0}]

    res = ResultValidator.compare_results(cols, orig_rows, cols, opt_rows, has_order_by=False)
    assert res.is_equivalent is True
    assert res.columns_match is True
    assert res.row_count_match is True
    assert res.values_match is True
    assert res.order_match is True
    assert res.mismatch_reason is None


def test_case_b_same_rows_different_order_without_order_by():
    """Case B: Same rows, different order, without ORDER BY -> Equivalent (True)."""
    cols = ["id", "name"]
    orig_rows = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    opt_rows = [{"id": 2, "name": "B"}, {"id": 1, "name": "A"}]

    res = ResultValidator.compare_results(cols, orig_rows, cols, opt_rows, has_order_by=False)
    assert res.is_equivalent is True
    assert res.columns_match is True
    assert res.row_count_match is True
    assert res.values_match is True
    assert "row order was ignored" in res.explanation


def test_case_c_same_rows_different_order_with_order_by():
    """Case C: Same rows, different order, with ORDER BY -> Not Equivalent (False)."""
    cols = ["id", "val"]
    orig_rows = [{"id": 1, "val": 100}, {"id": 2, "val": 200}]
    opt_rows = [{"id": 2, "val": 200}, {"id": 1, "val": 100}]

    res = ResultValidator.compare_results(cols, orig_rows, cols, opt_rows, has_order_by=True)
    assert res.is_equivalent is False
    assert res.columns_match is True
    assert res.row_count_match is True
    assert res.order_match is False
    assert res.mismatch_reason == "Different ordered result"


def test_case_d_column_count_mismatch():
    """Case D: Different number of columns -> Not Equivalent (False)."""
    orig_cols = ["id", "name"]
    opt_cols = ["id", "name", "extra"]
    orig_rows = [{"id": 1, "name": "A"}]
    opt_rows = [{"id": 1, "name": "A", "extra": "X"}]

    res = ResultValidator.compare_results(orig_cols, orig_rows, opt_cols, opt_rows)
    assert res.is_equivalent is False
    assert res.column_count_match is False
    assert res.mismatch_reason == "Different column count"


def test_case_e_column_name_or_order_mismatch():
    """Case E: Same column count but different names or order -> Not Equivalent (False)."""
    orig_cols = ["first_name", "last_name"]
    opt_cols = ["last_name", "first_name"]
    orig_rows = [{"first_name": "John", "last_name": "Doe"}]
    opt_rows = [{"last_name": "Doe", "first_name": "John"}]

    res = ResultValidator.compare_results(orig_cols, orig_rows, opt_cols, opt_rows)
    assert res.is_equivalent is False
    assert res.columns_match is False
    assert res.mismatch_reason == "Different column names/order"


def test_case_f_row_count_mismatch():
    """Case F: Row count differs -> Not Equivalent (False)."""
    cols = ["id", "score"]
    orig_rows = [{"id": 1, "score": 90}, {"id": 2, "score": 80}]
    opt_rows = [{"id": 1, "score": 90}]

    res = ResultValidator.compare_results(cols, orig_rows, cols, opt_rows)
    assert res.is_equivalent is False
    assert res.row_count_match is False
    assert res.orig_row_count == 2
    assert res.opt_row_count == 1
    assert res.mismatch_reason == "Different row count"


def test_case_g_row_value_mismatch():
    """Case G: Same count and columns but values differ -> Not Equivalent (False)."""
    cols = ["id", "city"]
    orig_rows = [{"id": 1, "city": "Bengaluru"}]
    opt_rows = [{"id": 1, "city": "Delhi"}]

    res = ResultValidator.compare_results(cols, orig_rows, cols, opt_rows)
    assert res.is_equivalent is False
    assert res.values_match is False
    assert res.mismatch_reason == "Different row values"


def test_case_h_duplicate_row_multiplicity_mismatch():
    """Case H: Duplicate row counts differ (multiset violation) -> Not Equivalent (False)."""
    cols = ["category"]
    orig_rows = [{"category": "Electronics"}, {"category": "Electronics"}, {"category": "Books"}]
    opt_rows = [{"category": "Electronics"}, {"category": "Books"}, {"category": "Books"}]

    res = ResultValidator.compare_results(cols, orig_rows, cols, opt_rows, has_order_by=False)
    assert res.is_equivalent is False
    assert res.values_match is False
    assert res.mismatch_reason == "Different row values"


def test_case_i_data_types_nulls_and_empty_results():
    """Case I: NULL values, mixed types, floats, and empty result sets."""
    cols = ["id", "data", "num"]
    
    # 1. NULL equivalence
    orig_rows = [{"id": 1, "data": None, "num": 3.14}]
    opt_rows = [{"id": 1, "data": None, "num": 3.14}]
    res = ResultValidator.compare_results(cols, orig_rows, cols, opt_rows)
    assert res.is_equivalent is True

    # 2. NULL vs non-NULL mismatch
    opt_mismatch = [{"id": 1, "data": "", "num": 3.14}]
    res2 = ResultValidator.compare_results(cols, orig_rows, cols, opt_mismatch)
    assert res2.is_equivalent is False

    # 3. Empty result set on both sides
    res_empty = ResultValidator.compare_results(cols, [], cols, [])
    assert res_empty.is_equivalent is True
    assert res_empty.orig_row_count == 0
    assert res_empty.opt_row_count == 0


def test_case_j_safety_check_blocks_modifying_queries():
    """Case J: Non-SELECT or DDL queries are blocked by DatabaseManager safety check."""
    unsafe_queries = [
        "DROP TABLE customers;",
        "DELETE FROM orders WHERE order_id = 1;",
        "UPDATE customers SET city = 'Delhi';",
        "INSERT INTO customers (first_name, last_name, city) VALUES ('A', 'B', 'C');",
        "ALTER TABLE customers ADD COLUMN active INTEGER;",
        "PRAGMA foreign_keys = OFF;",
        "SELECT * FROM customers; SELECT * FROM orders;"
    ]
    for q in unsafe_queries:
        is_safe, msg = DatabaseManager.is_safe_read_query(q)
        assert is_safe is False, f"Expected query to be rejected as unsafe: {q}"
        assert msg is not None


# =====================================================================
# INTEGRATION TESTS: Benchmark & Equivalence Workflow on Test DBs
# =====================================================================

@pytest.fixture
def ecommerce_db():
    """Creates a temporary test ecommerce database fixture."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_p2b2_ecom_")
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
    """Creates a temporary test university database fixture."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_p2b2_univ_")
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


def test_ecommerce_test_optimization_validates_equivalence(ecommerce_db):
    """Verifies that running benchmark on customers(city) produces validated EquivalenceResult."""
    engine, db_path = ecommerce_db
    initial_hash = get_file_hash(db_path)

    query = "SELECT * FROM customers WHERE city = 'Bengaluru';"
    res = engine.analyze_query(query, run_benchmark=False)

    benchmarker = PerformanceBenchmarker(engine.db_manager)
    b_res = benchmarker.run_benchmark(query, res.recommendations, res.suggested_query)

    assert b_res.equivalence_result is not None
    eq = b_res.equivalence_result
    assert eq.is_equivalent is True
    assert eq.columns_match is True
    assert eq.row_count_match is True
    assert eq.values_match is True
    assert eq.orig_row_count == 50
    assert eq.opt_row_count == 50
    assert "result set" in eq.explanation.lower()

    # Original database remains unchanged
    assert get_file_hash(db_path) == initial_hash


def test_university_attendance_validates_equivalence(university_db):
    """Verifies that running benchmark on attendance(status) produces validated EquivalenceResult."""
    engine, db_path = university_db
    initial_hash = get_file_hash(db_path)

    query = "SELECT * FROM attendance WHERE status = 'Absent';"
    res = engine.analyze_query(query, run_benchmark=False)

    benchmarker = PerformanceBenchmarker(engine.db_manager)
    b_res = benchmarker.run_benchmark(query, res.recommendations, res.suggested_query)

    assert b_res.equivalence_result is not None
    eq = b_res.equivalence_result
    assert eq.is_equivalent is True
    assert eq.row_count_match is True
    assert eq.orig_row_count > 0

    # Original database remains untouched
    assert get_file_hash(db_path) == initial_hash


def test_ordered_query_equivalence_in_benchmark(ecommerce_db):
    """Verifies ORDER BY query preserves strict order comparison in benchmark."""
    engine, db_path = ecommerce_db
    query = "SELECT * FROM customers WHERE city = 'Bengaluru' ORDER BY customer_id ASC;"
    res = engine.analyze_query(query, run_benchmark=False)

    benchmarker = PerformanceBenchmarker(engine.db_manager)
    b_res = benchmarker.run_benchmark(query, res.recommendations, res.suggested_query)

    assert b_res.equivalence_result is not None
    eq = b_res.equivalence_result
    assert eq.is_equivalent is True
    assert eq.has_order_by is True
    assert eq.order_match is True
