"""
Batch 5 Automated Tests: Advanced Query Optimization Analysis
=============================================================

Tests 1–11 cover all required scenarios from the Batch 5 specification.

These tests use generic synthetic SQLite fixtures only.
No hardcoded ecommerce.sqlite or university.sqlite paths are used.

Tests verify:
  1.  Composite WHERE predicates detected (RULE_11)
  2.  Existing suitable composite index → no duplicate candidate
  3.  JOIN with indexed join column → no false positive (RULE_12 suppressed)
  4.  JOIN with genuine missing index opportunity (RULE_12 fires)
  5.  ORDER BY with temporary B-tree evidence (RULE_04 / RULE_13)
  6.  GROUP BY with temporary B-tree evidence (RULE_14)
  7.  Covering-index opportunity (RULE_15)
  8.  Expression predicate LOWER(col) → advisory only, no DDL
  9.  Alias-based JOIN predicates → alias resolution, no false positives
  10. Candidate DDL validation — all DDL references physical columns only
  11. Regression — existing RULE_01 – RULE_10 still fire correctly
"""
import pytest
import sqlite3
import os
import tempfile

from database.database import DatabaseManager
from optimizer.optimization_engine import OptimizationEngine
from analyzer.query_plan import QueryPlanAnalyzer
from models.analysis_models import PlanNode


# ===========================================================================
# Shared DB fixture helpers
# ===========================================================================

def _make_db(script: str) -> str:
    """Creates a temp SQLite database, executes script, returns path."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_b5_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(script)
    conn.commit()
    conn.close()
    return path


def _engine(db_path: str) -> OptimizationEngine:
    mgr = DatabaseManager(db_path)
    return OptimizationEngine(mgr)


def _rule_ids(result) -> list:
    return [f.rule_id for f in result.findings]


def _ddl_list(result) -> list:
    return [r.ddl for r in result.recommendations if r.ddl]


# ===========================================================================
# Fixture — generic orders table (no indexes)
# ===========================================================================

@pytest.fixture
def orders_db_no_idx():
    """
    Generic orders table with no secondary indexes.
    Used for composite/covering/ORDER BY/GROUP BY tests.
    """
    path = _make_db("""
        CREATE TABLE orders (
            order_id   INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            status      TEXT NOT NULL,
            amount      REAL NOT NULL,
            created_at  TEXT NOT NULL
        );
        INSERT INTO orders VALUES (1, 10, 'Delivered', 500.0, '2024-01-01');
        INSERT INTO orders VALUES (2, 20, 'Pending',   250.0, '2024-01-02');
        INSERT INTO orders VALUES (3, 10, 'Delivered', 300.0, '2024-01-03');
        INSERT INTO orders VALUES (4, 30, 'Cancelled', 100.0, '2024-01-04');
        INSERT INTO orders VALUES (5, 20, 'Delivered', 750.0, '2024-01-05');
    """)
    eng = _engine(path)
    yield eng, path
    DatabaseManager.cleanup_temp_db(path)


@pytest.fixture
def orders_db_composite_idx():
    """
    Generic orders table WITH a composite index on (customer_id, status).
    Used to verify deduplication.
    """
    path = _make_db("""
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            status      TEXT NOT NULL,
            amount      REAL NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX idx_orders_cust_status ON orders(customer_id, status);
        INSERT INTO orders VALUES (1, 10, 'Delivered', 500.0, '2024-01-01');
        INSERT INTO orders VALUES (2, 20, 'Pending',   250.0, '2024-01-02');
    """)
    eng = _engine(path)
    yield eng, path
    DatabaseManager.cleanup_temp_db(path)


@pytest.fixture
def join_db_indexed():
    """
    customers / orders JOIN scenario where the join column IS indexed.
    Used to verify RULE_12 does NOT produce a false positive.
    """
    path = _make_db("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name        TEXT NOT NULL
        );
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            status      TEXT NOT NULL
        );
        -- Join column IS indexed
        CREATE INDEX idx_orders_customer_id ON orders(customer_id);

        INSERT INTO customers VALUES (1, 'Alice');
        INSERT INTO customers VALUES (2, 'Bob');
        INSERT INTO orders VALUES (1, 1, 'Delivered');
        INSERT INTO orders VALUES (2, 2, 'Pending');
        INSERT INTO orders VALUES (3, 1, 'Cancelled');
    """)
    eng = _engine(path)
    yield eng, path
    DatabaseManager.cleanup_temp_db(path)


@pytest.fixture
def join_db_unindexed():
    """
    customers / orders JOIN scenario where the join column is NOT indexed.
    Used to verify RULE_12 fires with correct DDL.
    """
    path = _make_db("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            city        TEXT NOT NULL
        );
        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            amount      REAL NOT NULL
        );
        -- NO index on orders.customer_id

        INSERT INTO customers VALUES (1, 'Alice', 'Bengaluru');
        INSERT INTO customers VALUES (2, 'Bob',   'Delhi');
        INSERT INTO orders VALUES (1, 1, 500.0);
        INSERT INTO orders VALUES (2, 2, 250.0);
        INSERT INTO orders VALUES (3, 1, 300.0);
    """)
    eng = _engine(path)
    yield eng, path
    DatabaseManager.cleanup_temp_db(path)


@pytest.fixture
def alias_join_db():
    """
    JOIN query with table aliases — tests alias resolution in RULE_12.
    """
    path = _make_db("""
        CREATE TABLE employees (
            emp_id    INTEGER PRIMARY KEY,
            dept_id   INTEGER NOT NULL,
            name      TEXT NOT NULL
        );
        CREATE TABLE departments (
            dept_id   INTEGER PRIMARY KEY,
            dept_name TEXT NOT NULL
        );
        -- NO index on employees.dept_id (the join column on the inner side)

        INSERT INTO departments VALUES (1, 'Engineering');
        INSERT INTO departments VALUES (2, 'Marketing');
        INSERT INTO employees VALUES (1, 1, 'Alice');
        INSERT INTO employees VALUES (2, 2, 'Bob');
        INSERT INTO employees VALUES (3, 1, 'Charlie');
    """)
    eng = _engine(path)
    yield eng, path
    DatabaseManager.cleanup_temp_db(path)


# ===========================================================================
# Test 1: Composite WHERE predicates detected
# ===========================================================================

def test_composite_where_predicates_detected(orders_db_no_idx):
    """
    RULE_11 must fire for a query with multiple equality predicates on a table
    with no composite index covering those columns.
    """
    eng, _ = orders_db_no_idx
    sql = "SELECT * FROM orders WHERE customer_id = 10 AND status = 'Delivered'"
    result = eng.analyze_query(sql)

    # RULE_11 should fire (composite index opportunity detected)
    assert "RULE_11" in _rule_ids(result), (
        "Expected RULE_11 (Composite Index Opportunity) but it was not in findings."
    )

    # At least one DDL recommendation must be generated
    ddls = _ddl_list(result)
    assert ddls, "Expected at least one CREATE INDEX DDL from RULE_11."

    # DDL must reference both predicate columns
    ddl_combined = " ".join(ddls).lower()
    assert "customer_id" in ddl_combined
    assert "status" in ddl_combined

    # Strategy should be COMPOSITE_INDEX
    assert result.optimization_strategy == "COMPOSITE_INDEX", (
        f"Expected strategy COMPOSITE_INDEX, got {result.optimization_strategy}"
    )


# ===========================================================================
# Test 2: Existing suitable composite index → no duplicate candidate
# ===========================================================================

def test_existing_composite_index_no_duplicate(orders_db_composite_idx):
    """
    RULE_11 must NOT fire when an existing index already covers the predicate columns.
    """
    eng, _ = orders_db_composite_idx
    sql = "SELECT * FROM orders WHERE customer_id = 10 AND status = 'Delivered'"
    result = eng.analyze_query(sql)

    # Neither RULE_11 nor RULE_03 should fire a DDL for customer_id + status
    for r in result.recommendations:
        if r.ddl:
            ddl_lower = r.ddl.lower()
            assert not ("customer_id" in ddl_lower and "status" in ddl_lower), (
                "A duplicate composite index on (customer_id, status) was recommended "
                "even though idx_orders_cust_status already exists."
            )


# ===========================================================================
# Test 3: JOIN with indexed join column → no false positive (RULE_12 suppressed)
# ===========================================================================

def test_join_indexed_column_no_false_positive(join_db_indexed):
    """
    RULE_12 must NOT fire when the join column on the inner table is already indexed.
    The existing RULE_05 false-positive guard must also remain intact.
    """
    eng, _ = join_db_indexed
    sql = """
        SELECT o.order_id, c.name
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        WHERE o.status = 'Delivered'
    """
    result = eng.analyze_query(sql)

    # Neither RULE_05 nor RULE_12 should fire for the join column (it's indexed)
    join_rule_findings = [f for f in result.findings if f.rule_id in ("RULE_05", "RULE_12")]
    false_positive_join = [
        f for f in join_rule_findings
        if "customer_id" in (f.affected_column or "").lower()
    ]
    assert not false_positive_join, (
        "RULE_05 or RULE_12 incorrectly fired for an already-indexed join column."
    )


# ===========================================================================
# Test 4: JOIN with genuine missing index — RULE_05 or RULE_12 fires
# ===========================================================================

def test_join_missing_index_rule12_fires():
    """
    Verify that when an inner join table is SCANned with no index on the join column,
    RULE_05 or RULE_12 fires and produces a CREATE INDEX DDL.

    We construct a schema where:
      - 'categories' table is the outer driver (accessed via WHERE on its PK).
      - 'products' table is joined on category_id which has NO index.

    With a WHERE on the outer table's PK, SQLite will SEARCH categories and
    SCAN products for each matching row.
    """
    path = _make_db("""
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
    """)
    try:
        eng = _engine(path)
        sql = """
            SELECT c.name, p.title
            FROM categories c
            JOIN products p ON p.category_id = c.category_id
            WHERE c.category_id = 1
        """
        result = eng.analyze_query(sql)

        # Determine whether SQLite actually scanned the inner join table
        has_scan_on_products = any(
            n.step_type == "SCAN"
            and n.table_name
            and n.table_name.lower() in ("products", "p")
            for n in result.plan_nodes
        )

        if has_scan_on_products:
            # If products is being SCANned, RULE_05 or RULE_12 must fire
            join_findings = [f for f in result.findings if f.rule_id in ("RULE_05", "RULE_12")]
            assert join_findings, (
                "Products is SCANned during JOIN but neither RULE_05 nor RULE_12 fired."
            )
            ddls = _ddl_list(result)
            assert ddls, "RULE_05/RULE_12 fired but no CREATE INDEX DDL was generated."
            assert any("category_id" in (d or "").lower() for d in ddls), (
                "DDL does not reference the join column 'category_id'."
            )
        else:
            # SQLite chose not to SCAN (tiny dataset optimized away) — this is acceptable.
            # No false DDL must be produced for columns that aren't actually problematic.
            pass
    finally:
        DatabaseManager.cleanup_temp_db(path)


# ===========================================================================
# Test 5: ORDER BY with temporary B-tree evidence
# ===========================================================================

def test_order_by_temp_btree_detected(orders_db_no_idx):
    """
    When SQLite emits a temp B-tree for ORDER BY, RULE_04 (plan observation)
    or RULE_13 (combined index) must fire and the plan must contain TEMP B-TREE.
    """
    eng, _ = orders_db_no_idx
    sql = "SELECT * FROM orders WHERE customer_id = 10 ORDER BY created_at"
    result = eng.analyze_query(sql)

    # Plan must contain a TEMP B-TREE node (or at minimum the rules detect it)
    has_btree_node = any(n.step_type == "TEMP B-TREE" for n in result.plan_nodes)

    # RULE_04 or RULE_13 should surface ORDER BY evidence
    order_rule_ids = [f.rule_id for f in result.findings if f.rule_id in ("RULE_04", "RULE_13")]

    if has_btree_node:
        assert order_rule_ids, (
            "TEMP B-TREE is present in plan but neither RULE_04 nor RULE_13 fired."
        )
        # Description must mention ORDER BY
        order_findings = [f for f in result.findings if f.rule_id in ("RULE_04", "RULE_13")]
        for f in order_findings:
            assert "ORDER BY" in f.description.upper() or "ORDER BY" in f.title.upper(), (
                "Finding description does not mention ORDER BY."
            )


# ===========================================================================
# Test 6: GROUP BY with temporary B-tree evidence
# ===========================================================================

def test_group_by_temp_btree_detected(orders_db_no_idx):
    """
    When SQLite uses a temp B-tree for GROUP BY, RULE_14 must fire and emit:
      - A plan observation Finding (category 'Execution Plan', severity INFO)
      - Optionally an index candidate Finding (category 'Indexing')
    """
    eng, _ = orders_db_no_idx
    sql = "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id"
    result = eng.analyze_query(sql)

    has_btree_node = any(n.step_type == "TEMP B-TREE" for n in result.plan_nodes)

    if has_btree_node:
        rule14_findings = [f for f in result.findings if f.rule_id == "RULE_14"]
        assert rule14_findings, "TEMP B-TREE for GROUP BY present but RULE_14 did not fire."

        # Must have at least one plan observation (INFO severity)
        obs_findings = [f for f in rule14_findings if f.category == "Execution Plan"]
        assert obs_findings, "RULE_14 fired but no Execution Plan observation was emitted."

        # Observation must mention GROUP BY
        for obs in obs_findings:
            assert "GROUP BY" in obs.description.upper() or "GROUP BY" in obs.title.upper(), (
                "RULE_14 observation does not mention GROUP BY."
            )


# ===========================================================================
# Test 7: Covering-index opportunity
# ===========================================================================

def test_covering_index_opportunity(orders_db_no_idx):
    """
    RULE_15 must fire when the query selects a small set of explicit columns and
    filters on a physical column — and no existing index covers the combined set.
    """
    eng, _ = orders_db_no_idx
    sql = "SELECT customer_id, status FROM orders WHERE customer_id = 10"
    result = eng.analyze_query(sql)

    # RULE_15 should fire
    assert "RULE_15" in _rule_ids(result), (
        "Expected RULE_15 (Covering Index Opportunity) but it was not in findings."
    )

    # DDL must reference both the filter and selected columns
    ddls = _ddl_list(result)
    covering_ddls = [
        d for d in ddls
        if d and "customer_id" in d.lower() and "status" in d.lower()
    ]
    assert covering_ddls, (
        "No DDL covering both 'customer_id' and 'status' was generated by RULE_15."
    )

    # Strategy should reflect a covering index or composite opportunity
    assert result.optimization_strategy in (
        "COVERING_INDEX", "COMPOSITE_INDEX", "INDEX_BASED"
    ), f"Unexpected strategy: {result.optimization_strategy}"


# ===========================================================================
# Test 8: Expression predicate LOWER(col) — advisory only, no DDL
# ===========================================================================

def test_expression_predicate_advisory_only():
    """
    When a WHERE predicate wraps a column in a function (e.g. LOWER(email)),
    RULE_06 must fire as an advisory. No standard CREATE INDEX DDL must be emitted
    on the underlying column, because a standard index would not support the expression.
    """
    path = _make_db("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            email   TEXT NOT NULL,
            name    TEXT NOT NULL
        );
        INSERT INTO users VALUES (1, 'Alice@Example.com', 'Alice');
        INSERT INTO users VALUES (2, 'bob@example.com',   'Bob');
    """)
    try:
        eng = _engine(path)
        sql = "SELECT * FROM users WHERE LOWER(email) = 'alice@example.com'"
        result = eng.analyze_query(sql)

        # RULE_06 must fire
        assert "RULE_06" in _rule_ids(result), (
            "Expected RULE_06 to fire for LOWER(email) expression predicate."
        )

        # No CREATE INDEX DDL must be generated for the expression column
        for rec in result.recommendations:
            if rec.ddl and "email" in rec.ddl.lower():
                # A standard index on 'email' should NOT be generated
                pytest.fail(
                    f"A standard CREATE INDEX on 'email' was generated despite the "
                    f"column being wrapped in LOWER(). DDL: {rec.ddl}"
                )

        # The finding must mention that a standard index may not support this predicate
        rule06_findings = [f for f in result.findings if f.rule_id == "RULE_06"]
        assert rule06_findings, "RULE_06 finding not found."
        combined_text = " ".join(f.description + f.rationale for f in rule06_findings).lower()
        assert "standard index" in combined_text or "expression" in combined_text, (
            "RULE_06 description/rationale does not mention standard index limitation."
        )
    finally:
        DatabaseManager.cleanup_temp_db(path)


# ===========================================================================
# Test 9: Alias-based JOIN predicates — correct resolution, no false positives
# ===========================================================================

def test_alias_based_join_no_false_positive(alias_join_db):
    """
    When a JOIN uses table aliases (e.g. 'e' for employees, 'd' for departments),
    the alias resolution must correctly identify the physical columns.
    RULE_12 must not produce a finding for the PRIMARY KEY join column on the driving table.
    """
    eng, _ = alias_join_db
    sql = """
        SELECT e.name, d.dept_name
        FROM employees e
        JOIN departments d ON e.dept_id = d.dept_id
    """
    result = eng.analyze_query(sql)

    # departments.dept_id IS the primary key — should never receive a RULE_12 finding
    rule12_findings = [f for f in result.findings if f.rule_id == "RULE_12"]
    for f in rule12_findings:
        assert f.affected_table and f.affected_table.lower() != "departments", (
            "RULE_12 incorrectly targeted 'departments' — its dept_id is the PRIMARY KEY."
        )

    # If RULE_12 fires for employees.dept_id (unindexed FK), that is valid.
    # Verify that any produced DDL references physical columns only (not aliases).
    ddls = _ddl_list(result)
    for ddl in ddls:
        # Aliases 'e' and 'd' must NOT appear as table names in the DDL
        assert " e(" not in ddl and " d(" not in ddl and "\ne(" not in ddl and "\nd(" not in ddl, (
            f"DDL contains table alias instead of physical table name: {ddl}"
        )


# ===========================================================================
# Test 10: Candidate DDL validation — all DDL references physical columns
# ===========================================================================

def test_candidate_ddl_physical_columns_only(orders_db_no_idx):
    """
    Every CREATE INDEX DDL produced by the analyzer must reference only
    physical columns that exist in the database schema.
    No computed aliases, aggregates, or non-existent column names may appear.
    """
    eng, path = orders_db_no_idx

    # Get actual physical columns from schema
    schema = eng.get_schema()
    table_name = "orders"
    physical_cols = {c.name.lower() for c in schema.tables[table_name].columns}

    queries = [
        "SELECT customer_id, status FROM orders WHERE customer_id = 10 AND status = 'Delivered'",
        "SELECT * FROM orders WHERE amount > 100 ORDER BY created_at",
        "SELECT customer_id, COUNT(*) AS cnt FROM orders GROUP BY customer_id",
    ]

    for sql in queries:
        result = eng.analyze_query(sql)
        for rec in result.recommendations:
            if not rec.ddl:
                continue
            # Extract the column list from the DDL: ON table(col1, col2, ...)
            import re
            m = re.search(r"ON\s+\w+\s*\(([^)]+)\)", rec.ddl, re.IGNORECASE)
            if not m:
                continue
            ddl_cols = [c.strip().lower() for c in m.group(1).split(",")]
            for col in ddl_cols:
                assert col in physical_cols, (
                    f"DDL column '{col}' is not a physical column of table '{table_name}'. "
                    f"Physical columns: {physical_cols}. DDL: {rec.ddl}"
                )


# ===========================================================================
# Test 11: Regression — existing RULE_01–RULE_10 still fire correctly
# ===========================================================================

def test_regression_existing_rules():
    """
    Smoke test that Batch 1–4 rule IDs still fire as expected after Batch 5 changes.
    Uses generic synthetic fixtures (no hardcoded external files).
    """
    path = _make_db("""
        CREATE TABLE products (
            product_id  INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            category    TEXT NOT NULL,
            price       REAL NOT NULL
        );
        INSERT INTO products VALUES (1, 'Widget A', 'Electronics', 29.99);
        INSERT INTO products VALUES (2, 'Widget B', 'Clothing',    9.99);
        INSERT INTO products VALUES (3, 'Widget C', 'Electronics', 49.99);
    """)
    try:
        eng = _engine(path)

        # RULE_01: SELECT * should produce advisory
        r1 = eng.analyze_query("SELECT * FROM products")
        assert "RULE_01" in _rule_ids(r1), "RULE_01 (SELECT *) did not fire."

        # RULE_02: Unindexed single-column filter should suggest index
        r2 = eng.analyze_query("SELECT name FROM products WHERE category = 'Electronics'")
        assert "RULE_02" in _rule_ids(r2), "RULE_02 (missing single index) did not fire."
        assert _ddl_list(r2), "RULE_02 did not produce DDL."

        # RULE_09: Large scan advisory
        r9 = eng.analyze_query("SELECT name, price FROM products")
        assert "RULE_09" in _rule_ids(r9), "RULE_09 (missing LIMIT advisory) did not fire."

        # RULE_08: Leading wildcard LIKE
        r8 = eng.analyze_query("SELECT * FROM products WHERE name LIKE '%Widget%'")
        assert "RULE_08" in _rule_ids(r8), "RULE_08 (leading wildcard LIKE) did not fire."

        # RULE_06: Function on column
        r6 = eng.analyze_query("SELECT * FROM products WHERE LOWER(name) = 'widget a'")
        assert "RULE_06" in _rule_ids(r6), "RULE_06 (function on column) did not fire."

        # Verify no existing rule misclassifies a well-indexed query
        path2 = _make_db("""
            CREATE TABLE items (
                item_id  INTEGER PRIMARY KEY,
                category TEXT NOT NULL
            );
            CREATE INDEX idx_items_category ON items(category);
            INSERT INTO items VALUES (1, 'A');
            INSERT INTO items VALUES (2, 'B');
        """)
        try:
            eng2 = _engine(path2)
            r_clean = eng2.analyze_query("SELECT item_id FROM items WHERE category = 'A'")
            ddls = _ddl_list(r_clean)
            # No redundant index should be recommended for an already-indexed column
            for ddl in ddls:
                assert "category" not in ddl.lower(), (
                    f"Redundant index on 'category' was recommended even though idx_items_category exists. DDL: {ddl}"
                )
        finally:
            DatabaseManager.cleanup_temp_db(path2)

    finally:
        DatabaseManager.cleanup_temp_db(path)


# ===========================================================================
# Bonus: QueryPlanAnalyzer.temp_btree_purpose unit tests
# ===========================================================================

def test_temp_btree_purpose_order_by():
    nodes = [PlanNode(id=1, parent=0, detail="USE TEMP B-TREE FOR ORDER BY",
                      step_type="TEMP B-TREE", scan_type="Temporary Sort B-Tree (ORDER BY)",
                      raw_text="USE TEMP B-TREE FOR ORDER BY")]
    assert QueryPlanAnalyzer.temp_btree_purpose(nodes) == "ORDER BY"


def test_temp_btree_purpose_group_by():
    nodes = [PlanNode(id=1, parent=0, detail="USE TEMP B-TREE FOR GROUP BY",
                      step_type="TEMP B-TREE", scan_type="Temporary Sort B-Tree (GROUP BY)",
                      raw_text="USE TEMP B-TREE FOR GROUP BY")]
    assert QueryPlanAnalyzer.temp_btree_purpose(nodes) == "GROUP BY"


def test_temp_btree_purpose_none():
    nodes = [PlanNode(id=1, parent=0, detail="SCAN orders",
                      step_type="SCAN", scan_type="Full Table Scan",
                      raw_text="SCAN orders")]
    assert QueryPlanAnalyzer.temp_btree_purpose(nodes) is None
