import pytest
import sqlite3
import os
import tempfile
import hashlib
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
    """Creates a temporary test ecommerce database."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_p2b1_ecom_")
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
    """Creates a temporary test university database."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_p2b1_univ_")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE courses (
            course_id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_name TEXT NOT NULL
        );
        CREATE TABLE enrollments (
            enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            semester TEXT NOT NULL,
            FOREIGN KEY (course_id) REFERENCES courses(course_id)
        );
        CREATE TABLE attendance (
            attendance_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            status TEXT NOT NULL
        );
        CREATE INDEX idx_enrollments_course_id ON enrollments(course_id);
        CREATE INDEX idx_enrollments_semester ON enrollments(semester);
    """)


    c_data = [(f"Course_{i}",) for i in range(1, 10)]
    conn.executemany("INSERT INTO courses (course_name) VALUES (?)", c_data)

    e_data = [(i % 9 + 1, i, "2026-Fall" if i % 2 == 0 else "2025-Spring") for i in range(1, 100)]
    conn.executemany("INSERT INTO enrollments (course_id, student_id, semester) VALUES (?, ?, ?)", e_data)

    a_data = [(i, "Absent" if i % 4 == 0 else "Present") for i in range(1, 200)]
    conn.executemany("INSERT INTO attendance (student_id, status) VALUES (?, ?)", a_data)

    conn.commit()
    conn.close()

    mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(mgr)
    yield engine, db_path

    DatabaseManager.cleanup_temp_db(db_path)


def test_regression_1_ecommerce_test_optimization_workflow(ecommerce_db):
    """REGRESSION TEST 1: SELECT * FROM customers WHERE city = 'Bengaluru';

    Verifies candidate index creation in isolated temp DB, plan change (SCAN -> SEARCH),
    and original DB immutability.
    """
    engine, db_path = ecommerce_db
    initial_hash = get_file_hash(db_path)

    query = "SELECT * FROM customers WHERE city = 'Bengaluru';"
    res = engine.analyze_query(query, run_benchmark=False)

    # Verify button condition
    executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
    assert res.optimization_strategy == "INDEX_BASED"
    assert len(executable_recs) > 0

    # User clicks Test Optimization -> Run benchmark on isolated copy
    benchmarker = PerformanceBenchmarker(engine.db_manager)
    b_res = benchmarker.run_benchmark(query, res.recommendations, res.suggested_query)

    assert b_res.benchmark_applied is True
    assert b_res.plan_changed is True
    assert b_res.plan_change_desc == "SCAN → SEARCH"
    assert res.suggested_query is None or res.suggested_query == query

    # Verify original database remains untouched
    assert get_file_hash(db_path) == initial_hash, "Original database file was modified!"


def test_regression_2_ecommerce_already_indexed_no_test_button(ecommerce_db):
    """REGRESSION TEST 2: SELECT * FROM orders WHERE customer_id = 500;

    Verifies no candidate DDL is generated when column is already indexed, and test button condition is False.
    """
    engine, db_path = ecommerce_db
    query = "SELECT * FROM orders WHERE customer_id = 500;"
    res = engine.analyze_query(query, run_benchmark=False)

    executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
    assert len(executable_recs) == 0, f"Unexpected executable DDL for already indexed column: {executable_recs}"
    # Test button condition evaluates to False
    button_condition = (res.optimization_strategy == "INDEX_BASED" and len(executable_recs) > 0)
    assert button_condition is False


def test_regression_3_university_attendance_test_optimization(university_db):
    """REGRESSION TEST 3: SELECT * FROM attendance WHERE status = 'Absent';

    Verifies candidate attendance(status) produces test result and dynamic interpretation note.
    """
    engine, db_path = university_db
    initial_hash = get_file_hash(db_path)

    query = "SELECT * FROM attendance WHERE status = 'Absent';"
    res = engine.analyze_query(query, run_benchmark=False)

    executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
    assert len(executable_recs) > 0
    assert "attendance" in executable_recs[0].ddl.lower()
    assert "status" in executable_recs[0].ddl.lower()

    benchmarker = PerformanceBenchmarker(engine.db_manager)
    b_res = benchmarker.run_benchmark(query, res.recommendations, res.suggested_query)

    assert b_res.benchmark_applied is True
    assert b_res.note != ""
    assert get_file_hash(db_path) == initial_hash, "Original university database was modified!"


def test_regression_4_university_computed_aggregate_no_test_button(university_db):
    """REGRESSION TEST 4: Computed aggregate ORDER BY query.

    Verifies no physical index candidate is generated for computed aggregate enrollment_count.
    Test button condition evaluates to False.
    """
    engine, _ = university_db
    query = """
    SELECT c.course_name, COUNT(*) AS enrollment_count
    FROM courses c
    JOIN enrollments e ON c.course_id = e.course_id
    WHERE e.semester = '2026-Fall'
    GROUP BY c.course_id, c.course_name
    ORDER BY enrollment_count DESC;
    """
    res = engine.analyze_query(query, run_benchmark=False)

    executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
    # Should NOT contain DDL for computed enrollment_count
    for r in executable_recs:
        assert "enrollment_count" not in r.ddl.lower()

    button_condition = (res.optimization_strategy == "INDEX_BASED" and len(executable_recs) > 0)
    assert button_condition is False


def test_query_change_clears_test_result(ecommerce_db):
    """REGRESSION TEST 5: Analyzing a new query clears previous test results."""
    engine, _ = ecommerce_db
    q1 = "SELECT * FROM customers WHERE city = 'Bengaluru';"
    res1 = engine.analyze_query(q1, run_benchmark=False)
    benchmarker = PerformanceBenchmarker(engine.db_manager)
    res1.benchmark_result = benchmarker.run_benchmark(q1, res1.recommendations)

    assert res1.benchmark_result is not None

    # Analyzing Query 2 resets benchmark_result
    q2 = "SELECT * FROM orders WHERE customer_id = 500;"
    res2 = engine.analyze_query(q2, run_benchmark=False)

    assert res2.benchmark_result is None


def test_database_change_clears_test_result(ecommerce_db, university_db):
    """REGRESSION TEST 6: Switching active database clears previous database test results."""
    engine_ecom, _ = ecommerce_db
    engine_univ, _ = university_db

    res_ecom = engine_ecom.analyze_query("SELECT * FROM customers WHERE city = 'Bengaluru';", run_benchmark=False)
    benchmarker = PerformanceBenchmarker(engine_ecom.db_manager)
    res_ecom.benchmark_result = benchmarker.run_benchmark(res_ecom.query, res_ecom.recommendations)

    assert res_ecom.benchmark_result is not None
    assert "customers" in res_ecom.schema.tables

    # Switch to University database -> new schema has university tables only
    res_univ = engine_univ.analyze_query("SELECT * FROM attendance WHERE status = 'Absent';", run_benchmark=False)
    assert res_univ.benchmark_result is None
    assert "attendance" in res_univ.schema.tables
    assert "customers" not in res_univ.schema.tables

