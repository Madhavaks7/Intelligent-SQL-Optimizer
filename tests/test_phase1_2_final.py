import pytest
import sqlite3
import os
import tempfile
import hashlib
from database.database import DatabaseManager, validate_sqlite_db
from optimizer.optimization_engine import OptimizationEngine
from models.analysis_models import Severity


def get_file_hash(filepath: str) -> str:
    """Calculates SHA256 hash of a file to verify read-only safety."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()


@pytest.fixture
def ecommerce_db():
    """Creates a temporary test database for Phase 1.2 validation."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_p12_ecom_")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            role_id INTEGER NOT NULL
        );
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            city TEXT NOT NULL
        );
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            price REAL NOT NULL
        );
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            total_amount REAL NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(order_id),
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        );

        CREATE INDEX idx_orders_customer_id ON orders(customer_id);
        CREATE INDEX idx_order_items_order_id ON order_items(order_id);
        CREATE INDEX idx_order_items_product_id ON order_items(product_id);
    """)

    # Seed test data
    users_data = [(f"user_{i}", f"user_{i}@example.com", i % 5) for i in range(1, 101)]
    conn.executemany("INSERT INTO users (username, email, role_id) VALUES (?, ?, ?)", users_data)

    cust_data = [(f"First_{i}", f"Last_{i}", "London" if i % 2 == 0 else "Paris") for i in range(1, 101)]
    conn.executemany("INSERT INTO customers (first_name, last_name, city) VALUES (?, ?, ?)", cust_data)

    prod_data = [(f"Prod_{i}", float(i * 10)) for i in range(1, 21)]
    conn.executemany("INSERT INTO products (product_name, price) VALUES (?, ?)", prod_data)

    ord_data = [(i % 100 + 1, "2025-01-15" if i % 2 == 0 else "2024-12-01", float(i * 50), "completed" if i % 3 == 0 else "pending") for i in range(1, 201)]
    conn.executemany("INSERT INTO orders (customer_id, order_date, total_amount, status) VALUES (?, ?, ?, ?)", ord_data)

    items_data = [(i % 200 + 1, i % 20 + 1, i % 5 + 1) for i in range(1, 401)]
    conn.executemany("INSERT INTO order_items (order_id, product_id, quantity) VALUES (?, ?, ?)", items_data)

    conn.commit()
    conn.close()

    mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(mgr)
    yield engine, db_path

    DatabaseManager.cleanup_temp_db(db_path)


def test_1_simple_missing_index(ecommerce_db):
    """TEST 1: Simple missing index detection and DDL generation."""
    engine, db_path = ecommerce_db
    initial_hash = get_file_hash(db_path)

    query = "SELECT username, email FROM users WHERE role_id = 2;"
    res = engine.analyze_query(query, run_benchmark=True)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_02" in rule_ids

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    assert len(ddls) > 0
    assert "users" in ddls[0].lower()
    assert "role_id" in ddls[0].lower()
    assert res.optimization_strategy == "INDEX_BASED"
    assert res.suggested_query is None

    assert get_file_hash(db_path) == initial_hash, "Original DB file was modified!"


def test_2_existing_index(ecommerce_db):
    """TEST 2: Query utilizing existing index should not recommend redundant index."""
    engine, db_path = ecommerce_db
    query = "SELECT * FROM orders WHERE customer_id = 50;"
    res = engine.analyze_query(query, run_benchmark=True)

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    assert len(ddls) == 0, f"Unexpected DDL generated for already-indexed column: {ddls}"
    assert res.optimization_strategy in ("NO_OPTIMIZATION_REQUIRED", "INFORMATIONAL_ADVISORY", "QUERY_REWRITE")


def test_3_date_range_index(ecommerce_db):
    """TEST 3: Date range query on unindexed order_date produces index recommendation."""
    engine, db_path = ecommerce_db
    query = "SELECT order_id, total_amount FROM orders WHERE order_date >= '2025-01-01' AND order_date <= '2025-01-31';"
    res = engine.analyze_query(query, run_benchmark=True)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_02" in rule_ids

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    assert len(ddls) > 0
    assert "orders" in ddls[0].lower()
    assert "order_date" in ddls[0].lower()


def test_4_join_and_filter(ecommerce_db):
    """TEST 4: JOIN + filter recommendations with no false RULE_05 join alerts."""
    engine, db_path = ecommerce_db
    query = """
    SELECT c.first_name, o.order_id, o.total_amount
    FROM customers c
    JOIN orders o ON c.customer_id = o.customer_id
    WHERE c.city = 'London';
    """
    res = engine.analyze_query(query, run_benchmark=True)

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    assert len(ddls) > 0
    assert "customers" in ddls[0].lower()
    assert "city" in ddls[0].lower()

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_05" not in rule_ids, "False positive RULE_05 unindexed join reported!"


def test_5_aggregate_computed_order_by(ecommerce_db):
    """TEST 5: ORDER BY computed aggregate alias must NOT generate invalid DDL."""
    engine, db_path = ecommerce_db
    query = """
    SELECT p.product_name, SUM(oi.quantity) AS units_sold
    FROM products p
    JOIN order_items oi ON p.product_id = oi.product_id
    JOIN orders o ON oi.order_id = o.order_id
    WHERE oi.quantity > 0
    GROUP BY p.product_id, p.product_name
    ORDER BY units_sold DESC;
    """
    res = engine.analyze_query(query, run_benchmark=True)

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    for ddl in ddls:
        assert "units_sold" not in ddl.lower(), f"Invalid DDL generated for computed alias: {ddl}"


def test_6_large_result_set_advisory(ecommerce_db):
    """TEST 6: Unbound SELECT without LIMIT generates RULE_09 advisory, not a semantic query rewrite."""
    engine, db_path = ecommerce_db
    query = "SELECT customer_id, first_name FROM customers;"
    res = engine.analyze_query(query, run_benchmark=False)

    rule9_findings = [f for f in res.findings if f.rule_id == "RULE_09"]
    assert len(rule9_findings) == 1
    f_limit = rule9_findings[0]
    assert f_limit.title == "Large Result Set Advisory"
    assert f_limit.severity == Severity.INFO

    # Optimization strategy should remain informational advisory (or no optimization required)
    assert res.suggested_query is None or res.suggested_query == query


def test_7_invalid_sql(ecommerce_db):
    """TEST 7: Invalid SQL syntax is handled gracefully without crashing."""
    engine, _ = ecommerce_db
    query = "SELEC * FORM customers;"
    res = engine.analyze_query(query, run_benchmark=False)

    assert res is not None
    assert res.parsed_query.parse_error is not None, "Expected parse_error for invalid SQL"


def test_8_nonexistent_table(ecommerce_db):
    """TEST 8: Querying non-existent table is caught and reported gracefully."""
    engine, _ = ecommerce_db
    query = "SELECT * FROM non_existent_table_xyz;"
    res = engine.analyze_query(query, run_benchmark=False)

    assert res is not None
    assert res.parsed_query.parse_error is not None
    assert "no such table" in res.parsed_query.parse_error.lower()


def test_9_nonexistent_column(ecommerce_db):
    """TEST 9: Querying non-existent column is caught gracefully."""
    engine, _ = ecommerce_db
    query = "SELECT non_existent_col FROM customers;"
    res = engine.analyze_query(query, run_benchmark=False)

    assert res is not None
    assert res.parsed_query.parse_error is not None
    assert "no such column" in res.parsed_query.parse_error.lower()


def test_10_database_switching():
    """TEST 10: Database switching clears state completely."""
    fd1, path1 = tempfile.mkstemp(suffix=".db", prefix="test_switch_a_")
    os.close(fd1)
    conn1 = sqlite3.connect(path1)
    conn1.execute("CREATE TABLE alpha (id INT);")
    conn1.close()

    fd2, path2 = tempfile.mkstemp(suffix=".db", prefix="test_switch_b_")
    os.close(fd2)
    conn2 = sqlite3.connect(path2)
    conn2.execute("CREATE TABLE beta (id INT);")
    conn2.close()

    try:
        mgr1 = DatabaseManager(path1)
        engine1 = OptimizationEngine(mgr1)
        schema1 = engine1.get_schema()
        assert "alpha" in schema1.tables
        assert "beta" not in schema1.tables

        mgr2 = DatabaseManager(path2)
        engine2 = OptimizationEngine(mgr2)
        schema2 = engine2.get_schema()
        assert "beta" in schema2.tables
        assert "alpha" not in schema2.tables
    finally:
        DatabaseManager.cleanup_temp_db(path1)
        DatabaseManager.cleanup_temp_db(path2)


def test_11_invalid_database_file():
    """TEST 11: Invalid SQLite database binary file is rejected with clear error."""
    fd, text_file = tempfile.mkstemp(suffix=".db", prefix="test_invalid_")
    os.write(fd, b"THIS IS NOT A VALID SQLITE DATABASE BINARY FILE")
    os.close(fd)

    try:
        is_valid, msg = validate_sqlite_db(text_file)
        assert is_valid is False
        assert "valid sqlite database" in msg.lower()
    finally:
        if os.path.exists(text_file):
            os.remove(text_file)


def test_12_empty_database():
    """TEST 12: Empty SQLite database with 0 user tables is handled gracefully."""
    fd, empty_db = tempfile.mkstemp(suffix=".db", prefix="test_empty_")
    os.close(fd)
    conn = sqlite3.connect(empty_db)
    conn.execute("PRAGMA user_version = 0;")  # Initializes SQLite header
    conn.close()

    try:
        is_valid, msg = validate_sqlite_db(empty_db)
        assert is_valid is True

        mgr = DatabaseManager(empty_db)
        engine = OptimizationEngine(mgr)
        schema = engine.get_schema()
        assert len(schema.tables) == 0
    finally:
        DatabaseManager.cleanup_temp_db(empty_db)


def test_select_star_advisory_wording(ecommerce_db):
    """Verify SELECT * is treated as an informational query design advisory without requiring SQL rewrite."""
    engine, _ = ecommerce_db
    query = "SELECT * FROM customers;"
    res = engine.analyze_query(query, run_benchmark=False)

    rule1_findings = [f for f in res.findings if f.rule_id == "RULE_01"]
    assert len(rule1_findings) == 1
    f_star = rule1_findings[0]
    assert f_star.severity == Severity.INFO
    assert f_star.title == "Wildcard SELECT * Usage"
    assert "Selecting only required columns can reduce data transfer and memory usage" in f_star.rationale

    rec1 = [r for r in res.recommendations if r.id == "REC_01"][0]
    assert rec1.type == "ADVISORY"
    assert "Selecting only required columns can reduce data transfer and memory usage" in rec1.description


def test_benchmark_interpretation_notes(ecommerce_db):
    """Verify exact dynamic benchmark note wording for regression, improvement, and variance."""
    from performance.benchmark import PerformanceBenchmarker
    from models.analysis_models import Recommendation

    engine, db_path = ecommerce_db
    benchmarker = PerformanceBenchmarker(engine.db_manager)

    recs = [Recommendation(id="REC_02", type="CREATE INDEX", description="test", ddl="CREATE INDEX idx_test ON customers(city);")]
    res = benchmarker.run_benchmark("SELECT * FROM customers WHERE city = 'London';", recs)

    assert res.benchmark_applied is True
    # The note must be one of the 3 dynamic interpretation messages (never hardcoded < 0.5 ms)
    assert "< 0.5 ms" not in res.note
    assert ("improved measured execution time" in res.note or
            "increased measured execution time" in res.note or
            "measured execution-time difference was very small" in res.note)



