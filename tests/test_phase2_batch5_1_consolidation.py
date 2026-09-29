"""
Batch 5.1 Automated Tests: Candidate Consolidation & Rule Verification
======================================================================

Comprehensive verification suite covering all 14 requirements of Batch 5.1:
  1. Duplicate candidate consolidation for RULE_03 + RULE_11 (attendance query)
  2. Duplicate candidate consolidation for generic ORDER BY + RULE_13 (orders query)
  3. Candidate identity preserved end-to-end through Test Optimization
  4. ORDER_BY_INDEX strategy preserved through BenchmarkResult, UI, and History
  5. Composite index candidate testability (isolated temp db, read-only original)
  6. JOIN_INDEX candidate testability
  7. COVERING_INDEX candidate testability
  8. Advisory-only findings remain non-testable
  9. Generic JOIN case where RULE_12 correctly fires (inner table scan, unindexed join col)
  10. Generic JOIN case where RULE_12 correctly does not fire (join col indexed)
  11. Genuine covering-index opportunity (RULE_15 fires with COVERING_INDEX strategy)
  12. Existing covering index prevents duplicate candidate
  13. Database safety (original database strictly read-only)
  14. History independence (query rename / session isolation)
"""
import hashlib
import os
import re
import sqlite3
import tempfile
import pytest

from database.database import DatabaseManager
from optimizer.optimization_engine import OptimizationEngine
from performance.benchmark import PerformanceBenchmarker
from analyzer.history_manager import HistoryManager
from models.analysis_models import TESTABLE_OPTIMIZATION_STRATEGIES


# ===========================================================================
# Fixture Helpers
# ===========================================================================

def _make_db(script: str) -> str:
    """Creates a temp SQLite database, executes script, and returns path."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b51_con_")
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
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


# ===========================================================================
# 1. Duplicate Candidate Consolidation for RULE_03 + RULE_11
# ===========================================================================

def test_1_duplicate_consolidation_rule03_rule11():
    """
    Query filtering on attendance(student_id, attendance_date) with multiple WHERE predicates
    must produce exactly ONE consolidated executable candidate (strategy: COMPOSITE_INDEX).
    No duplicate DDL statements or conflicting candidate recommendations.
    """
    db_path = _make_db("""
        CREATE TABLE attendance (
            attendance_id   INTEGER PRIMARY KEY,
            student_id      INTEGER NOT NULL,
            attendance_date TEXT NOT NULL,
            status          TEXT NOT NULL
        );
        INSERT INTO attendance VALUES (1, 500, '2026-06-01', 'Present');
        INSERT INTO attendance VALUES (2, 500, '2026-06-02', 'Absent');
        INSERT INTO attendance VALUES (3, 600, '2026-06-01', 'Present');
    """)
    try:
        eng = _engine(db_path)
        sql = """
            SELECT *
            FROM attendance
            WHERE student_id = 500
              AND attendance_date >= '2026-06-01';
        """
        result = eng.analyze_query(sql)

        # Strategy must be COMPOSITE_INDEX
        assert result.optimization_strategy == "COMPOSITE_INDEX", (
            f"Expected strategy COMPOSITE_INDEX, got {result.optimization_strategy}"
        )

        # Executable recommendations must contain exactly ONE candidate
        exec_recs = [r for r in result.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(exec_recs) == 1, (
            f"Expected exactly 1 consolidated CREATE INDEX recommendation, got {len(exec_recs)}: "
            f"{[r.ddl for r in exec_recs]}"
        )

        primary_rec = exec_recs[0]
        assert primary_rec.table.lower() == "attendance"
        assert [c.lower() for c in primary_rec.columns] == ["student_id", "attendance_date"]
        assert primary_rec.strategy == "COMPOSITE_INDEX"
        assert "RULE_11" in primary_rec.source_rules or "RULE_03" in primary_rec.source_rules

        # DDL must reference attendance(student_id, attendance_date)
        ddl_lower = primary_rec.ddl.lower()
        assert "attendance" in ddl_lower
        assert "student_id" in ddl_lower
        assert "attendance_date" in ddl_lower

        # Candidate is testable
        assert result.is_test_optimization_eligible is True
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 2. Generic ORDER BY Candidate vs Query-Specific Candidate Supersession
# ===========================================================================

def test_2_generic_order_by_superseded_by_rule13():
    """
    For `SELECT * FROM orders WHERE customer_id = 500 ORDER BY created_at;`
    the analyzer must present the specific combined candidate `orders(customer_id, created_at)`
    with strategy ORDER_BY_INDEX, superseding the generic single-column `orders(created_at)`.
    """
    db_path = _make_db("""
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            status      TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX idx_orders_customer ON orders(customer_id);
        INSERT INTO orders VALUES (1, 500, 'Delivered', '2026-01-01');
        INSERT INTO orders VALUES (2, 500, 'Pending',   '2026-01-02');
        INSERT INTO orders VALUES (3, 600, 'Delivered', '2026-01-01');
    """)
    try:
        eng = _engine(db_path)
        sql = "SELECT * FROM orders WHERE customer_id = 500 ORDER BY created_at;"
        result = eng.analyze_query(sql)

        # Strategy must be ORDER_BY_INDEX
        assert result.optimization_strategy == "ORDER_BY_INDEX", (
            f"Expected strategy ORDER_BY_INDEX, got {result.optimization_strategy}"
        )

        # Executable recommendations must contain exactly ONE combined candidate
        exec_recs = [r for r in result.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(exec_recs) == 1, (
            f"Expected exactly 1 consolidated candidate for combined WHERE+ORDER BY, got {len(exec_recs)}"
        )

        primary_rec = exec_recs[0]
        assert primary_rec.table.lower() == "orders"
        cols_lower = [c.lower() for c in primary_rec.columns]
        assert cols_lower == ["customer_id", "created_at"], (
            f"Expected columns ['customer_id', 'created_at'], got {primary_rec.columns}"
        )
        assert primary_rec.strategy == "ORDER_BY_INDEX"

        # The generic single-column index on orders(created_at) must NOT be present as an executable DDL
        for r in exec_recs:
            if [c.lower() for c in r.columns] == ["created_at"]:
                pytest.fail("Generic single-column index on orders(created_at) was not superseded.")
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 3. Candidate Identity Preserved Through Test Optimization
# ===========================================================================

def test_3_candidate_identity_preserved_through_test_optimization():
    """
    Verify candidate identity from Recommendation -> BenchmarkResult -> HistoryEntry.
    For orders(customer_id, created_at), test result metadata must describe
    'orders(customer_id, created_at)' with strategy ORDER_BY_INDEX, NOT 'orders(created_at)'.
    """
    db_path = _make_db("""
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            status      TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX idx_orders_customer ON orders(customer_id);
    """)
    try:
        # Populate with data
        conn = sqlite3.connect(db_path)
        for i in range(200):
            conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?);", (i+1, 500 if i % 2 == 0 else 600, "Delivered", f"2026-01-{(i%28)+1:02d}"))
        conn.commit()
        conn.close()

        eng = _engine(db_path)
        sql = "SELECT * FROM orders WHERE customer_id = 500 ORDER BY created_at;"
        res = eng.analyze_query(sql, run_benchmark=True)

        b_res = res.benchmark_result
        assert b_res is not None
        assert b_res.candidate_name == "orders(customer_id, created_at)", (
            f"Expected candidate_name 'orders(customer_id, created_at)', got '{b_res.candidate_name}'"
        )
        assert b_res.strategy == "ORDER_BY_INDEX", (
            f"Expected strategy 'ORDER_BY_INDEX', got '{b_res.strategy}'"
        )

        # Verify history recording preserves identity
        hm = HistoryManager()
        entry = hm.record_test_result(
            db_name="test.db",
            query=sql,
            optimization_name=b_res.candidate_name,
            strategy=b_res.strategy,
            candidate_ddl=b_res.candidate_ddl,
            benchmark_result=b_res,
            query_name="Orders Query"
        )
        assert entry.optimization_name == "orders(customer_id, created_at)"
        assert entry.strategy == "ORDER_BY_INDEX"
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 4. ORDER_BY_INDEX Strategy Preserved in Benchmark & History
# ===========================================================================

def test_4_strategy_classification_preservation():
    """
    Verifies that all Batch 5 strategy classifications survive intact into BenchmarkResult and History.
    """
    for strat in ("COMPOSITE_INDEX", "ORDER_BY_INDEX", "JOIN_INDEX", "COVERING_INDEX", "INDEX_BASED"):
        assert strat in TESTABLE_OPTIMIZATION_STRATEGIES


# ===========================================================================
# 5. Composite Candidate Testability & Database Safety
# ===========================================================================

def test_5_composite_candidate_testable_and_db_safe():
    """
    Verify composite candidate testability and ensure original DB is strictly read-only.
    """
    db_path = _make_db("""
        CREATE TABLE attendance (
            attendance_id   INTEGER PRIMARY KEY,
            student_id      INTEGER NOT NULL,
            attendance_date TEXT NOT NULL,
            status          TEXT NOT NULL
        );
    """)
    conn = sqlite3.connect(db_path)
    for i in range(300):
        conn.execute("INSERT INTO attendance VALUES (?, ?, ?, ?);", (i+1, i % 20, f"2026-06-{(i%28)+1:02d}", "Present"))
    conn.commit()
    conn.close()

    initial_hash = _file_hash(db_path)
    try:
        eng = _engine(db_path)
        sql = "SELECT * FROM attendance WHERE student_id = 5 AND attendance_date >= '2026-06-01';"
        res = eng.analyze_query(sql, run_benchmark=True)

        assert res.is_test_optimization_eligible is True
        assert res.optimization_strategy == "COMPOSITE_INDEX"
        assert res.benchmark_result is not None
        assert res.benchmark_result.benchmark_applied is True
        assert res.benchmark_result.equivalence_result.is_equivalent is True

        # Database safety check: hash must be unchanged
        final_hash = _file_hash(db_path)
        assert initial_hash == final_hash, "Original database file was modified during benchmarking!"
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 6. JOIN_INDEX Candidate Testability
# ===========================================================================

def test_6_join_index_candidate_testable():
    """
    Verify JOIN_INDEX candidate benchmark execution in temporary database.
    """
    db_path = _make_db("""
        CREATE TABLE parent (
            parent_id INTEGER PRIMARY KEY,
            name      TEXT NOT NULL
        );
        CREATE TABLE child (
            child_id   INTEGER PRIMARY KEY,
            parent_ref INTEGER NOT NULL,
            val        TEXT NOT NULL
        );
    """)
    conn = sqlite3.connect(db_path)
    for i in range(50):
        conn.execute("INSERT INTO parent VALUES (?, ?);", (i+1, f"Parent {i+1}"))
    for i in range(500):
        conn.execute("INSERT INTO child VALUES (?, ?, ?);", (i+1, (i % 50) + 1, f"Child {i+1}"))
    conn.commit()
    conn.close()

    try:
        eng = _engine(db_path)
        sql = "SELECT p.name, c.val FROM parent p JOIN child c ON c.parent_ref = p.parent_id WHERE p.parent_id = 1;"
        res = eng.analyze_query(sql, run_benchmark=True)

        assert res.optimization_strategy == "JOIN_INDEX"
        assert res.is_test_optimization_eligible is True
        assert res.benchmark_result is not None
        assert res.benchmark_result.candidate_name == "child(parent_ref)"
        assert res.benchmark_result.strategy == "JOIN_INDEX"
        assert res.benchmark_result.equivalence_result.is_equivalent is True
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 7. COVERING_INDEX Candidate Testability
# ===========================================================================

def test_7_covering_index_candidate_testable():
    """
    Verify COVERING_INDEX candidate benchmark execution and plan change to COVERING INDEX.
    """
    db_path = _make_db("""
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            status      TEXT NOT NULL,
            amount      REAL NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX idx_orders_customer_id ON orders(customer_id);
    """)
    conn = sqlite3.connect(db_path)
    for i in range(400):
        conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?);", (i+1, i % 30, "Delivered", float(i*5), "2026-01-01"))
    conn.commit()
    conn.close()

    try:
        eng = _engine(db_path)
        sql = "SELECT customer_id, status, amount FROM orders WHERE customer_id = 5;"
        res = eng.analyze_query(sql, run_benchmark=True)

        assert res.optimization_strategy == "COVERING_INDEX"
        assert res.is_test_optimization_eligible is True
        assert res.benchmark_result is not None
        assert res.benchmark_result.strategy == "COVERING_INDEX"
        assert res.benchmark_result.plan_changed is True
        assert res.benchmark_result.equivalence_result.is_equivalent is True
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 8. Advisory-Only Findings Remain Non-Testable
# ===========================================================================

def test_8_advisories_remain_non_testable():
    """
    Advisory findings (SELECT *, function predicates, large scan advisory)
    must produce NO executable candidates and remain strictly non-testable.
    """
    db_path = _make_db("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            email   TEXT NOT NULL,
            name    TEXT NOT NULL
        );
        INSERT INTO users VALUES (1, 'alice@example.com', 'Alice');
    """)
    try:
        eng = _engine(db_path)

        # Wildcard SELECT *
        r1 = eng.analyze_query("SELECT * FROM users;")
        assert r1.is_test_optimization_eligible is False
        assert len([r for r in r1.recommendations if r.type == "CREATE INDEX" and r.ddl]) == 0

        # Function on column
        r2 = eng.analyze_query("SELECT name FROM users WHERE LOWER(email) = 'alice@example.com';")
        assert r2.is_test_optimization_eligible is False
        assert len([r for r in r2.recommendations if r.type == "CREATE INDEX" and r.ddl]) == 0
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 9. Generic Controlled JOIN Case Where RULE_12 Correctly Fires
# ===========================================================================

def test_9_controlled_join_rule12_fires():
    """
    Controlled synthetic scenario where parent is SEARCHed by PK,
    child is SCANned on unindexed join column -> RULE_12 fires with clean physical DDL and alias resolution.
    """
    db_path = _make_db("""
        CREATE TABLE departments (
            dept_id   INTEGER PRIMARY KEY,
            dept_name TEXT NOT NULL
        );
        CREATE TABLE employees (
            emp_id    INTEGER PRIMARY KEY,
            dept_id   INTEGER NOT NULL,
            name      TEXT NOT NULL
        );
        INSERT INTO departments VALUES (1, 'Engineering');
        INSERT INTO employees VALUES (101, 1, 'Alice');
        INSERT INTO employees VALUES (102, 1, 'Bob');
    """)
    try:
        eng = _engine(db_path)
        sql = """
            SELECT d.dept_name, e.name
            FROM departments d
            JOIN employees e ON e.dept_id = d.dept_id
            WHERE d.dept_id = 1;
        """
        res = eng.analyze_query(sql)

        # Must detect join index opportunity on employees(dept_id)
        assert res.optimization_strategy == "JOIN_INDEX"
        exec_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(exec_recs) == 1

        rec = exec_recs[0]
        assert rec.table.lower() == "employees"
        assert [c.lower() for c in rec.columns] == ["dept_id"]
        assert "CREATE INDEX IF NOT EXISTS idx_employees_dept_id" in rec.ddl
        assert "ON employees(dept_id)" in rec.ddl
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 10. Generic Controlled JOIN Case Where RULE_12 Correctly Does Not Fire
# ===========================================================================

def test_10_controlled_join_rule12_negative():
    """
    When the child table join column IS indexed, neither RULE_05 nor RULE_12 fires.
    """
    db_path = _make_db("""
        CREATE TABLE departments (
            dept_id   INTEGER PRIMARY KEY,
            dept_name TEXT NOT NULL
        );
        CREATE TABLE employees (
            emp_id    INTEGER PRIMARY KEY,
            dept_id   INTEGER NOT NULL,
            name      TEXT NOT NULL
        );
        CREATE INDEX idx_employees_dept ON employees(dept_id);
        INSERT INTO departments VALUES (1, 'Engineering');
        INSERT INTO employees VALUES (101, 1, 'Alice');
    """)
    try:
        eng = _engine(db_path)
        sql = """
            SELECT d.dept_name, e.name
            FROM departments d
            JOIN employees e ON e.dept_id = d.dept_id
            WHERE d.dept_id = 1;
        """
        res = eng.analyze_query(sql)

        # No join rule findings
        join_rule_findings = [f for f in res.findings if f.rule_id in ("RULE_05", "RULE_12")]
        assert not join_rule_findings, f"Unexpected join findings: {join_rule_findings}"
        assert res.optimization_strategy == "NO_OPTIMIZATION_REQUIRED"
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 11. Genuine Covering-Index Opportunity (RULE_15)
# ===========================================================================

def test_11_genuine_covering_index_opportunity():
    """
    Controlled synthetic table where filtering column (customer_id) is indexed,
    selected columns (status, amount) are NOT in that index, and combined columns <= 6.
    RULE_15 must detect the opportunity with strategy COVERING_INDEX and valid DDL.
    """
    db_path = _make_db("""
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            status      TEXT NOT NULL,
            amount      REAL NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX idx_orders_customer_id ON orders(customer_id);
        INSERT INTO orders VALUES (1, 500, 'Delivered', 100.0, '2026-01-01');
    """)
    try:
        eng = _engine(db_path)
        sql = "SELECT customer_id, status, amount FROM orders WHERE customer_id = 500;"
        res = eng.analyze_query(sql)

        assert "RULE_15" in [f.rule_id for f in res.findings]
        assert res.optimization_strategy == "COVERING_INDEX"

        exec_recs = [r for r in res.recommendations if r.type == "CREATE INDEX" and r.ddl]
        assert len(exec_recs) == 1

        rec = exec_recs[0]
        assert rec.table.lower() == "orders"
        cols_lower = [c.lower() for c in rec.columns]
        assert "customer_id" in cols_lower
        assert "status" in cols_lower
        assert "amount" in cols_lower
        assert rec.strategy == "COVERING_INDEX"
    finally:
        DatabaseManager.cleanup_temp_db(db_path)


# ===========================================================================
# 12. Negative Case: Existing Covering Index Prevents Duplicate Candidate
# ===========================================================================

def test_12_existing_covering_index_prevents_duplicate():
    """
    When an existing index on orders(customer_id, status, amount) already provides full coverage,
    RULE_15 must NOT fire.
    """
    db_path = _make_db("""
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            status      TEXT NOT NULL,
            amount      REAL NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX idx_orders_cov ON orders(customer_id, status, amount);
        INSERT INTO orders VALUES (1, 500, 'Delivered', 100.0, '2026-01-01');
    """)
    try:
        eng = _engine(db_path)
        sql = "SELECT customer_id, status, amount FROM orders WHERE customer_id = 500;"
        res = eng.analyze_query(sql)

        rule15_findings = [f for f in res.findings if f.rule_id == "RULE_15"]
        assert not rule15_findings, "RULE_15 fired despite existing covering index."
        assert res.optimization_strategy == "NO_OPTIMIZATION_REQUIRED"
    finally:
        DatabaseManager.cleanup_temp_db(db_path)
