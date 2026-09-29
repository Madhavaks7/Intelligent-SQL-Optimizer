import pytest
import sqlite3
import os
import tempfile
from database.database import DatabaseManager
from optimizer.optimization_engine import OptimizationEngine
from models.analysis_models import Severity


@pytest.fixture
def ecommerce_db():
    """Creates a mock ecommerce SQLite database with realistic schema and indexes."""
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_ecom_")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
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

        -- Existing Indexes
        CREATE INDEX idx_orders_customer_id ON orders(customer_id);
        CREATE INDEX idx_orders_order_date ON orders(order_date);
        CREATE INDEX idx_order_items_order_id ON order_items(order_id);
        CREATE INDEX idx_order_items_product_id ON order_items(product_id);
    """)

    # Seed data
    customers_data = [(f"First_{i}", f"Last_{i}", "Bengaluru" if i % 2 == 0 else "Delhi") for i in range(1, 101)]
    conn.executemany("INSERT INTO customers (first_name, last_name, city) VALUES (?, ?, ?)", customers_data)

    products_data = [(f"Product_{i}", float(i * 10)) for i in range(1, 21)]
    conn.executemany("INSERT INTO products (product_name, price) VALUES (?, ?)", products_data)

    orders_data = [(i % 100 + 1, "2025-01-15" if i % 2 == 0 else "2024-12-01", float(i * 50), "completed" if i % 3 == 0 else "pending") for i in range(1, 201)]
    conn.executemany("INSERT INTO orders (customer_id, order_date, total_amount, status) VALUES (?, ?, ?, ?)", orders_data)

    items_data = [(i % 200 + 1, i % 20 + 1, i % 5 + 1) for i in range(1, 401)]
    conn.executemany("INSERT INTO order_items (order_id, product_id, quantity) VALUES (?, ?, ?)", items_data)

    conn.commit()
    conn.close()

    mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(mgr)
    yield engine, db_path

    DatabaseManager.cleanup_temp_db(db_path)


def test_regression_1_index_optimization(ecommerce_db):
    """TEST 1: Filter on unindexed customers(city). Should recommend customers(city) DDL."""
    engine, _ = ecommerce_db
    query = "SELECT * FROM customers WHERE city = 'Bengaluru';"
    res = engine.analyze_query(query, run_benchmark=True)

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    assert len(ddls) > 0
    assert "customers" in ddls[0].lower()
    assert "city" in ddls[0].lower()
    assert res.optimization_strategy == "INDEX_BASED"
    assert res.suggested_query is None  # SQL text UNCHANGED for index optimization


def test_regression_2_already_optimized(ecommerce_db):
    """TEST 2: Filter on indexed orders(customer_id). Existing idx_orders_customer_id used."""
    engine, _ = ecommerce_db
    query = "SELECT * FROM orders WHERE customer_id = 50;"
    res = engine.analyze_query(query, run_benchmark=True)

    # Should NOT recommend creating another index on customer_id
    ddls = [r.ddl for r in res.recommendations if r.ddl]
    assert len(ddls) == 0
    assert res.optimization_strategy in ("NO_OPTIMIZATION_REQUIRED", "INFORMATIONAL_ADVISORY", "QUERY_REWRITE")


def test_regression_3_range_filter_existing_index(ecommerce_db):
    """TEST 3: Range filter on orders(order_date). Existing idx_orders_order_date used."""
    engine, _ = ecommerce_db
    query = "SELECT order_id, customer_id, total_amount FROM orders WHERE order_date >= '2025-01-01';"
    res = engine.analyze_query(query, run_benchmark=True)

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    assert len(ddls) == 0  # Existing index used, no redundant DDL


def test_regression_4_join_alias_resolution(ecommerce_db):
    """TEST 4: JOIN with table alias c.city. Must resolve alias c -> customers."""
    engine, _ = ecommerce_db
    query = """
    SELECT c.first_name, c.last_name, o.order_id, o.total_amount
    FROM customers c
    JOIN orders o ON c.customer_id = o.customer_id
    WHERE c.city = 'Bengaluru';
    """
    res = engine.analyze_query(query, run_benchmark=True)

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    assert len(ddls) > 0
    assert "customers" in ddls[0].lower()
    assert "city" in ddls[0].lower()


def test_regression_5_no_index_on_computed_alias(ecommerce_db):
    """TEST 5: ORDER BY units_sold (SUM aggregate). Must NEVER generate DDL on units_sold."""
    engine, _ = ecommerce_db
    query = """
    SELECT p.product_name, SUM(oi.quantity) AS units_sold
    FROM products p
    JOIN order_items oi ON p.product_id = oi.product_id
    JOIN orders o ON oi.order_id = o.order_id
    WHERE o.status = 'completed'
    GROUP BY p.product_id, p.product_name
    ORDER BY units_sold DESC;
    """
    res = engine.analyze_query(query, run_benchmark=True)

    ddls = [r.ddl for r in res.recommendations if r.ddl]
    # Verify NO invalid DDL referencing units_sold was generated!
    for ddl in ddls:
        assert "units_sold" not in ddl.lower(), f"Generated invalid DDL on computed alias: {ddl}"


def test_regression_6_unindexed_join_false_positive(ecommerce_db):
    """BUG 9: Verify unindexed JOIN (RULE_05) is NOT falsely reported when join lookups use indexes."""
    engine, _ = ecommerce_db
    query = """
    SELECT p.product_name, SUM(oi.quantity) AS units_sold
    FROM products p
    JOIN order_items oi ON p.product_id = oi.product_id
    JOIN orders o ON oi.order_id = o.order_id
    WHERE o.status = 'completed'
    GROUP BY p.product_id, p.product_name
    ORDER BY units_sold DESC;
    """
    res = engine.analyze_query(query, run_benchmark=True)

    rule_ids = [f.rule_id for f in res.findings]
    # RULE_05 must NOT trigger for orders!
    assert "RULE_05" not in rule_ids, f"False positive RULE_05 reported in findings: {rule_ids}"


def test_regression_7_status_filter_wording_and_severity(ecommerce_db):
    """BUG 9: Verify single-column filter index recommendation wording and Severity.MEDIUM tuning."""
    engine, _ = ecommerce_db
    query = "SELECT * FROM orders WHERE status = 'completed';"
    res = engine.analyze_query(query, run_benchmark=True)

    rule2_findings = [f for f in res.findings if f.rule_id == "RULE_02"]
    assert len(rule2_findings) == 1
    f_orders = rule2_findings[0]
    assert "orders" in f_orders.affected_table.lower()
    assert "status" in f_orders.affected_column.lower()
    assert f_orders.severity == Severity.MEDIUM
    assert "Potential Index Opportunity" in f_orders.title

    # Check recommendation description wording
    rec2 = [r for r in res.recommendations if r.id == "REC_02"][0]
    assert "improve filtering of rows by status" in rec2.description
    assert "sorting" not in rec2.description.lower()



