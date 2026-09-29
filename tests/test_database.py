import pytest
import os
import tempfile
from database.database import DatabaseManager
from database.seed_database import seed_sample_database
from database.schema import SchemaInspector

@pytest.fixture
def temp_db():
    temp_fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_sqldb_")
    os.close(temp_fd)
    seed_sample_database(db_path, scale=0.1)
    db_mgr = DatabaseManager(db_path)
    yield db_mgr
    DatabaseManager.cleanup_temp_db(db_path)

def test_safe_read_query():
    safe_sql = "SELECT name, salary FROM Employee;"
    is_safe, err = DatabaseManager.is_safe_read_query(safe_sql)
    assert is_safe is True
    assert err is None

    drop_sql = "DROP TABLE Employee;"
    is_safe, err = DatabaseManager.is_safe_read_query(drop_sql)
    assert is_safe is False
    assert "Modifying query detected" in err

    delete_sql = "DELETE FROM Employee WHERE department_id = 5;"
    is_safe, err = DatabaseManager.is_safe_read_query(delete_sql)
    assert is_safe is False

def test_schema_inspection(temp_db):
    inspector = SchemaInspector(temp_db)
    schema = inspector.inspect_database()

    assert "Employee" in schema.tables
    assert "Department" in schema.tables
    assert "Orders" in schema.tables

    emp_table = schema.tables["Employee"]
    col_names = [c.name for c in emp_table.columns]
    assert "employee_id" in col_names
    assert "department_id" in col_names

def test_explain_query_plan(temp_db):
    plan_rows = temp_db.explain_query_plan("SELECT name FROM Employee WHERE department_id = 5;")
    assert len(plan_rows) > 0
    assert "detail" in plan_rows[0]


def test_validate_sqlite_db(temp_db):
    from database.database import validate_sqlite_db

    # 1. Valid SQLite DB
    is_valid, err = validate_sqlite_db(temp_db.db_path)
    assert is_valid is True
    assert err is None

    # 2. Nonexistent path
    is_valid, err = validate_sqlite_db("nonexistent_file_path_123.db")
    assert is_valid is False
    assert "does not exist" in err.lower()

    # 3. Empty file
    temp_fd, empty_path = tempfile.mkstemp(suffix=".db", prefix="test_empty_")
    os.close(temp_fd)
    try:
        is_valid, err = validate_sqlite_db(empty_path)
        assert is_valid is False
        assert "empty" in err.lower()
    finally:
        os.remove(empty_path)

    # 4. Invalid header / fake DB
    temp_fd, fake_path = tempfile.mkstemp(suffix=".db", prefix="test_fake_")
    os.write(temp_fd, b"This is not a sqlite file!")
    os.close(temp_fd)
    try:
        is_valid, err = validate_sqlite_db(fake_path)
        assert is_valid is False
        assert "not appear to be a valid sqlite database" in err.lower()
    finally:
        os.remove(fake_path)

