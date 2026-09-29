"""
Batch 5.1 Automated Tests: Test Optimization Eligibility Restoration
====================================================================

Tests 1–11 verify that the [🧪 Test Optimization] button is properly eligible
for all valid executable index candidates across all 5 index-based optimization strategies:
  - INDEX_BASED
  - COMPOSITE_INDEX
  - JOIN_INDEX
  - ORDER_BY_INDEX
  - COVERING_INDEX

And that non-executable, advisory-only, or invalid candidates remain strictly ineligible.

Safety checks ensure the original database file is never modified (read-only).
"""
import hashlib
import os
import sqlite3
import tempfile
import pytest

from database.database import DatabaseManager
from optimizer.optimization_engine import OptimizationEngine
from performance.benchmark import PerformanceBenchmarker
from models.analysis_models import TESTABLE_OPTIMIZATION_STRATEGIES


# ===========================================================================
# Fixture Helpers
# ===========================================================================

def _make_db(script: str) -> str:
    """Creates a temporary SQLite database, executes script, and returns path."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b51_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(script)
    conn.commit()
    conn.close()
    return path


def _engine(db_path: str) -> OptimizationEngine:
    mgr = DatabaseManager(db_path)
    return OptimizationEngine(mgr)


def _file_hash(path: str) -> str:
    """Calculates SHA256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


_UI_TESTABLE_STRATEGIES = {"INDEX_BASED", "COMPOSITE_INDEX", "JOIN_INDEX", "ORDER_BY_INDEX", "COVERING_INDEX"}


def _is_button_visible(res) -> bool:
    """Mirrors the exact app.py UI condition for the Test Optimization button."""
    executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
    return res.optimization_strategy in _UI_TESTABLE_STRATEGIES and len(executable_recs) > 0


# ===========================================================================
# 1. Existing INDEX_BASED candidate → Test Optimization eligible
# ===========================================================================

def test_1_index_based_candidate_eligible():
    """Single-column WHERE filter on unindexed column -> INDEX_BASED candidate -> Eligible."""
    schema_sql = """
    CREATE TABLE customers (
        customer_id INTEGER PRIMARY KEY,
        name TEXT,
        city TEXT
    );
    """
    db_path = _make_db(schema_sql)
    try:
        engine = _engine(db_path)
        query = "SELECT name, city FROM customers WHERE city = 'Bengaluru';"
        res = engine.analyze_query(query, run_benchmark=False)

        assert res.optimization_strategy == "INDEX_BASED"
        executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(executable_recs) > 0
        assert any("customers(city)" in r.ddl or "city" in r.ddl.lower() for r in executable_recs)

        assert _is_button_visible(res) is True
        assert res.is_test_optimization_eligible is True
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 2. COMPOSITE_INDEX candidate → Test Optimization eligible (Example 1)
# ===========================================================================

def test_2_composite_index_candidate_eligible():
    """Multi-column filter (attendance query) -> COMPOSITE_INDEX candidate -> Eligible."""
    schema_sql = """
    CREATE TABLE attendance (
        id INTEGER PRIMARY KEY,
        student_id INTEGER,
        attendance_date TEXT,
        status TEXT
    );
    """
    db_path = _make_db(schema_sql)
    try:
        engine = _engine(db_path)
        query = """
        SELECT *
        FROM attendance
        WHERE student_id = 500
          AND attendance_date >= '2026-06-01';
        """
        res = engine.analyze_query(query, run_benchmark=False)

        assert res.optimization_strategy == "COMPOSITE_INDEX"
        executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(executable_recs) > 0
        assert any("student_id, attendance_date" in r.ddl for r in executable_recs)

        # BUTTON MUST BE VISIBLE
        assert _is_button_visible(res) is True
        assert res.is_test_optimization_eligible is True
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 3. ORDER_BY_INDEX candidate → Test Optimization eligible (Example 2)
# ===========================================================================

def test_3_order_by_index_candidate_eligible():
    """WHERE + ORDER BY query with temp B-tree sort -> ORDER_BY_INDEX candidate -> Eligible."""
    schema_sql = """
    CREATE TABLE orders (
        order_id INTEGER PRIMARY KEY,
        customer_id INTEGER,
        created_at TEXT,
        total_amount REAL
    );
    CREATE INDEX idx_orders_cust ON orders(customer_id);
    """
    insert_sql = schema_sql + "\n" + "\n".join(
        f"INSERT INTO orders (customer_id, created_at, total_amount) VALUES ({500 if i % 2 == 0 else i}, '2026-01-0{i%9+1}', {i*10.0});"
        for i in range(50)
    )
    db_path = _make_db(insert_sql)
    try:
        engine = _engine(db_path)
        query = """
        SELECT *
        FROM orders
        WHERE customer_id = 500
        ORDER BY created_at;
        """
        res = engine.analyze_query(query, run_benchmark=False)

        assert res.optimization_strategy == "ORDER_BY_INDEX"
        executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(executable_recs) > 0
        assert any("customer_id, created_at" in r.ddl for r in executable_recs)

        # BUTTON MUST BE VISIBLE
        assert _is_button_visible(res) is True
        assert res.is_test_optimization_eligible is True
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 4. JOIN_INDEX candidate → Test Optimization eligible
# ===========================================================================

def test_4_join_index_candidate_eligible():
    """JOIN on unindexed right table column -> JOIN_INDEX candidate -> Eligible."""
    schema_sql = """
    CREATE TABLE categories (
        category_id INTEGER PRIMARY KEY,
        name        TEXT NOT NULL
    );
    CREATE TABLE products (
        product_id  INTEGER PRIMARY KEY,
        category_id INTEGER NOT NULL,
        title       TEXT NOT NULL
    );
    INSERT INTO categories VALUES (1, 'Electronics');
    INSERT INTO categories VALUES (2, 'Clothing');
    INSERT INTO products VALUES (1, 1, 'Phone');
    INSERT INTO products VALUES (2, 1, 'Tablet');
    INSERT INTO products VALUES (3, 2, 'Shirt');
    """
    db_path = _make_db(schema_sql)
    try:
        engine = _engine(db_path)
        query = """
        SELECT c.name, p.title
        FROM categories c
        JOIN products p ON p.category_id = c.category_id
        WHERE c.category_id = 1;
        """
        res = engine.analyze_query(query, run_benchmark=False)

        assert res.optimization_strategy in ("JOIN_INDEX", "INDEX_BASED")
        executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(executable_recs) > 0
        assert any("products" in r.ddl.lower() and "category_id" in r.ddl.lower() for r in executable_recs)

        # BUTTON MUST BE VISIBLE
        assert _is_button_visible(res) is True
        assert res.is_test_optimization_eligible is True
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 5. COVERING_INDEX candidate → Test Optimization eligible
# ===========================================================================

def test_5_covering_index_candidate_eligible():
    """Covering index recommendation (RULE_15) -> COVERING_INDEX candidate -> Eligible."""
    schema_sql = """
    CREATE TABLE products (
        product_id INTEGER PRIMARY KEY,
        category TEXT,
        price REAL,
        description TEXT
    );
    """
    db_path = _make_db(schema_sql)
    try:
        engine = _engine(db_path)
        query = "SELECT price FROM products WHERE category = 'Electronics';"
        res = engine.analyze_query(query, run_benchmark=False)

        executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(executable_recs) > 0

        assert res.optimization_strategy in _UI_TESTABLE_STRATEGIES
        assert _is_button_visible(res) is True
        assert res.is_test_optimization_eligible is True
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 6. Advisory-only finding → NOT eligible
# ===========================================================================

def test_6_advisory_only_not_eligible():
    """Advisory-only findings (SELECT *, LIKE '%pattern%', expression) -> NOT eligible."""
    schema_sql = """
    CREATE TABLE logs (
        log_id INTEGER PRIMARY KEY,
        message TEXT,
        created_at TEXT
    );
    CREATE INDEX idx_logs_msg ON logs(message);
    CREATE INDEX idx_logs_created ON logs(created_at);
    """
    db_path = _make_db(schema_sql)
    try:
        engine = _engine(db_path)

        # Query A: Leading wildcard LIKE on indexed column (advisory only, no DDL)
        q_like = "SELECT log_id, message FROM logs WHERE message LIKE '%error%';"
        res_like = engine.analyze_query(q_like, run_benchmark=False)
        assert res_like.optimization_strategy == "INFORMATIONAL_ADVISORY"
        assert _is_button_visible(res_like) is False
        assert res_like.is_test_optimization_eligible is False

        # Query B: Expression wrapped predicate on indexed column (advisory only, no DDL)
        q_expr = "SELECT log_id FROM logs WHERE LOWER(message) = 'error';"
        res_expr = engine.analyze_query(q_expr, run_benchmark=False)
        executable_recs_expr = [r for r in res_expr.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(executable_recs_expr) == 0
        assert _is_button_visible(res_expr) is False
        assert res_expr.is_test_optimization_eligible is False

        # Query C: Already indexed column with SELECT * (advisory only)
        q_star = "SELECT * FROM logs WHERE created_at = '2026-01-01';"
        res_star = engine.analyze_query(q_star, run_benchmark=False)
        executable_recs_star = [r for r in res_star.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(executable_recs_star) == 0
        assert _is_button_visible(res_star) is False
        assert res_star.is_test_optimization_eligible is False
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 7. GROUP BY observation without candidate → NOT eligible
# ===========================================================================

def test_7_group_by_observation_without_candidate_not_eligible():
    """GROUP BY execution plan observation without executable candidate -> NOT eligible."""
    schema_sql = """
    CREATE TABLE metrics (
        id INTEGER PRIMARY KEY,
        category TEXT,
        val REAL
    );
    CREATE INDEX idx_metrics_cat ON metrics(category);
    """
    db_path = _make_db(schema_sql)
    try:
        engine = _engine(db_path)
        # Already indexed by category -> no new index candidate generated
        query = "SELECT category, SUM(val) FROM metrics GROUP BY category;"
        res = engine.analyze_query(query, run_benchmark=False)

        executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(executable_recs) == 0
        assert _is_button_visible(res) is False
        assert res.is_test_optimization_eligible is False
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 8. Invalid/malformed DDL → NOT eligible
# ===========================================================================

def test_8_invalid_or_malformed_ddl_not_eligible():
    """Non-physical / computed expressions (e.g. COUNT(*)) do not produce DDL -> NOT eligible."""
    schema_sql = """
    CREATE TABLE items (
        item_id INTEGER PRIMARY KEY,
        category TEXT,
        qty INTEGER
    );
    CREATE INDEX idx_items_cat ON items(category);
    """
    db_path = _make_db(schema_sql)
    try:
        engine = _engine(db_path)
        query = "SELECT category, COUNT(*) as cnt FROM items GROUP BY category ORDER BY cnt DESC;"
        res = engine.analyze_query(query, run_benchmark=False)

        # Must not generate DDL for alias 'cnt'
        executable_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        for r in executable_recs:
            assert "cnt" not in r.ddl.lower()

        # If no valid physical candidate exists, button condition evaluates to False
        if not executable_recs:
            assert _is_button_visible(res) is False
            assert res.is_test_optimization_eligible is False
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 9. Original database remains read-only
# ===========================================================================

def test_9_original_database_remains_readonly():
    """Benchmarking an eligible Batch 5 candidate does not modify the source database file."""
    schema_sql = """
    CREATE TABLE attendance (
        id INTEGER PRIMARY KEY,
        student_id INTEGER,
        attendance_date TEXT,
        status TEXT
    );
    """
    insert_sql = schema_sql + "\n" + "\n".join(
        f"INSERT INTO attendance (student_id, attendance_date, status) VALUES ({i%50}, '2026-06-0{i%9+1}', 'Present');"
        for i in range(100)
    )
    db_path = _make_db(insert_sql)
    try:
        initial_hash = _file_hash(db_path)
        engine = _engine(db_path)

        query = "SELECT * FROM attendance WHERE student_id = 5 AND attendance_date >= '2026-06-01';"
        res = engine.analyze_query(query, run_benchmark=False)

        assert res.is_test_optimization_eligible is True
        assert len(res.recommendations) > 0

        # Execute benchmark via benchmarker
        benchmarker = PerformanceBenchmarker(engine.db_manager)
        b_res = benchmarker.run_benchmark(query, res.recommendations, res.suggested_query)

        assert b_res is not None
        assert b_res.benchmark_applied is True

        # Assert database file is byte-for-byte unchanged
        post_test_hash = _file_hash(db_path)
        assert post_test_hash == initial_hash, "Original SQLite database file was modified during benchmarking!"

        # Assert no table/index changes in original DB connection
        conn = sqlite3.connect(db_path)
        indexes = conn.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()
        conn.close()
        # Original DB should have NO indexes
        assert len(indexes) == 0, f"Index was improperly committed to original DB: {indexes}"
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 10. End-to-end benchmark execution on Batch 5 candidate
# ===========================================================================

def test_10_full_benchmark_execution_batch5_candidate():
    """End-to-end test of isolated benchmarking, result equivalence, and speedup for Batch 5."""
    schema_sql = """
    CREATE TABLE orders (
        order_id INTEGER PRIMARY KEY,
        customer_id INTEGER,
        created_at TEXT,
        amount REAL
    );
    """
    insert_sql = schema_sql + "\n" + "\n".join(
        f"INSERT INTO orders (customer_id, created_at, amount) VALUES ({i%10}, '2026-01-0{i%9+1}', {i*5.0});"
        for i in range(200)
    )
    db_path = _make_db(insert_sql)
    try:
        engine = _engine(db_path)
        query = "SELECT customer_id, created_at, amount FROM orders WHERE customer_id = 3 ORDER BY created_at;"
        res = engine.analyze_query(query, run_benchmark=False)

        assert res.is_test_optimization_eligible is True
        benchmarker = PerformanceBenchmarker(engine.db_manager)
        b_res = benchmarker.run_benchmark(query, res.recommendations, res.suggested_query)

        assert b_res.benchmark_applied is True
        assert b_res.equivalence_result is not None
        assert b_res.equivalence_result.is_equivalent is True
        assert b_res.original_metrics.rows_returned == b_res.optimized_metrics.rows_returned
        assert len(b_res.indexes_created) > 0
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 11. UI button condition and TESTABLE_OPTIMIZATION_STRATEGIES contract
# ===========================================================================

def test_11_ui_button_condition_contract():
    """Verifies TESTABLE_OPTIMIZATION_STRATEGIES contains all 5 required index strategies."""
    expected_strategies = {
        "INDEX_BASED",
        "COMPOSITE_INDEX",
        "JOIN_INDEX",
        "ORDER_BY_INDEX",
        "COVERING_INDEX",
    }
    assert TESTABLE_OPTIMIZATION_STRATEGIES == expected_strategies
    assert _UI_TESTABLE_STRATEGIES == expected_strategies

    # Ineligible strategies
    ineligible = {"INFORMATIONAL_ADVISORY", "NO_OPTIMIZATION_REQUIRED", "QUERY_REWRITE"}
    for strat in ineligible:
        assert strat not in TESTABLE_OPTIMIZATION_STRATEGIES
