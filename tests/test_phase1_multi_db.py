import pytest
import sqlite3
import os
import tempfile
import hashlib
from database.database import DatabaseManager, validate_sqlite_db
from optimizer.optimization_engine import OptimizationEngine

def get_file_hash(filepath: str) -> str:
    """Calculates SHA256 hash of a file to verify it remains unmodified."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()

@pytest.fixture
def multi_databases():
    """Creates three completely distinct test SQLite databases."""
    paths = {}
    
    # 1. Database A: Social Media Schema (users, posts)
    fd, path_a = tempfile.mkstemp(suffix=".db", prefix="test_dba_")
    os.close(fd)
    conn = sqlite3.connect(path_a)
    conn.executescript("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            role_id INTEGER NOT NULL
        );
        CREATE TABLE posts (
            post_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            views INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
    """)
    # Insert rows
    users_data = [(f"user_{i}", f"user_{i}@example.com", i % 5) for i in range(1, 101)]
    conn.executemany("INSERT INTO users (username, email, role_id) VALUES (?, ?, ?)", users_data)
    posts_data = [(i % 100 + 1, f"Post Title {i}", i * 10) for i in range(1, 501)]
    conn.executemany("INSERT INTO posts (user_id, title, views) VALUES (?, ?, ?)", posts_data)
    conn.commit()
    conn.close()
    paths["DB_A"] = path_a

    # 2. Database B: E-Commerce Schema (customers, orders)
    fd, path_b = tempfile.mkstemp(suffix=".db", prefix="test_dbb_")
    os.close(fd)
    conn = sqlite3.connect(path_b)
    conn.executescript("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            country TEXT NOT NULL
        );
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            status TEXT NOT NULL,
            total REAL NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
    """)
    cust_data = [(f"Company {i}", "USA" if i % 2 == 0 else "UK") for i in range(1, 51)]
    conn.executemany("INSERT INTO customers (company_name, country) VALUES (?, ?)", cust_data)
    ord_data = [(i % 50 + 1, "2026-01-01", "PENDING" if i % 3 == 0 else "SHIPPED", float(i * 15)) for i in range(1, 301)]
    conn.executemany("INSERT INTO orders (customer_id, order_date, status, total) VALUES (?, ?, ?, ?)", ord_data)
    conn.commit()
    conn.close()
    paths["DB_B"] = path_b

    # 3. Database C: Academic Schema (students, courses, enrollments)
    fd, path_c = tempfile.mkstemp(suffix=".db", prefix="test_dbc_")
    os.close(fd)
    conn = sqlite3.connect(path_c)
    conn.executescript("""
        CREATE TABLE students (
            student_id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            gpa REAL NOT NULL
        );
        CREATE TABLE courses (
            course_id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_code TEXT NOT NULL,
            credits INTEGER NOT NULL
        );
        CREATE TABLE enrollments (
            student_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            grade TEXT NOT NULL,
            PRIMARY KEY (student_id, course_id)
        );
    """)
    stu_data = [(f"Student {i}", 3.0 + (i % 10) * 0.1) for i in range(1, 80)]
    conn.executemany("INSERT INTO students (full_name, gpa) VALUES (?, ?)", stu_data)
    cour_data = [(f"CS{100+i}", 3) for i in range(1, 15)]
    conn.executemany("INSERT INTO courses (course_code, credits) VALUES (?, ?)", cour_data)
    enr_data = [(i % 80 + 1, i % 14 + 1, "A" if i % 2 == 0 else "B") for i in range(1, 200)]
    conn.executemany("INSERT INTO enrollments (student_id, course_id, grade) VALUES (?, ?, ?)", enr_data)
    conn.commit()
    conn.close()
    paths["DB_C"] = path_c

    yield paths

    # Cleanup temp databases
    for p in paths.values():
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


def test_database_a_analysis(multi_databases):
    db_path = multi_databases["DB_A"]
    initial_hash = get_file_hash(db_path)

    mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(mgr)
    schema = engine.get_schema()

    assert "users" in schema.tables
    assert "posts" in schema.tables
    assert "customers" not in schema.tables

    query = "SELECT * FROM users WHERE role_id = 2;"
    res = engine.analyze_query(query, run_benchmark=True)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_01" in rule_ids  # SELECT *
    assert "RULE_02" in rule_ids  # Missing index on users(role_id)

    # Verify original database remains UNTOUCHED
    assert get_file_hash(db_path) == initial_hash, "Original Database A was modified!"


def test_database_b_analysis(multi_databases):
    db_path = multi_databases["DB_B"]
    initial_hash = get_file_hash(db_path)

    mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(mgr)
    schema = engine.get_schema()

    assert "customers" in schema.tables
    assert "orders" in schema.tables
    assert "users" not in schema.tables

    query = "SELECT customer_id, total FROM orders WHERE status = 'PENDING' AND total > 100.0;"
    res = engine.analyze_query(query, run_benchmark=True)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_03" in rule_ids  # Composite index on orders(status, total)

    # Verify original database remains UNTOUCHED
    assert get_file_hash(db_path) == initial_hash, "Original Database B was modified!"


def test_database_c_analysis(multi_databases):
    db_path = multi_databases["DB_C"]
    initial_hash = get_file_hash(db_path)

    mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(mgr)
    schema = engine.get_schema()

    assert "students" in schema.tables
    assert "enrollments" in schema.tables
    assert "orders" not in schema.tables

    query = "SELECT * FROM students WHERE gpa > 3.5;"
    res = engine.analyze_query(query, run_benchmark=True)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_01" in rule_ids  # SELECT *
    assert "RULE_02" in rule_ids  # Missing index on students(gpa)

    # Verify original database remains UNTOUCHED
    assert get_file_hash(db_path) == initial_hash, "Original Database C was modified!"



def test_database_switching_isolation(multi_databases):
    # Load A first
    mgr_a = DatabaseManager(multi_databases["DB_A"])
    engine_a = OptimizationEngine(mgr_a)
    schema_a = engine_a.get_schema()
    assert "users" in schema_a.tables

    # Switch to B
    mgr_b = DatabaseManager(multi_databases["DB_B"])
    engine_b = OptimizationEngine(mgr_b)
    schema_b = engine_b.get_schema()

    # Confirm metadata completely switches
    assert "users" not in schema_b.tables
    assert "customers" in schema_b.tables
    assert "orders" in schema_b.tables
