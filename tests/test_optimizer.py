import pytest
import os
import tempfile
from database.database import DatabaseManager
from database.seed_database import seed_sample_database
from optimizer.optimization_engine import OptimizationEngine
from models.analysis_models import Severity

@pytest.fixture
def temp_db():
    temp_fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_sqlo_")
    os.close(temp_fd)
    seed_sample_database(db_path, scale=0.1)  # small scale for fast tests
    db_mgr = DatabaseManager(db_path)
    engine = OptimizationEngine(db_mgr)
    yield engine
    DatabaseManager.cleanup_temp_db(db_path)

def test_rule_1_select_star(temp_db):
    query = "SELECT * FROM Employee WHERE department_id = 5;"
    res = temp_db.analyze_query(query, run_benchmark=False)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_01" in rule_ids

def test_rule_2_missing_index(temp_db):
    query = "SELECT name, salary FROM Employee WHERE department_id = 5;"
    res = temp_db.analyze_query(query, run_benchmark=False)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_02" in rule_ids

def test_rule_3_composite_index(temp_db):
    query = "SELECT name, salary FROM Employee WHERE department_id = 5 AND salary > 80000;"
    res = temp_db.analyze_query(query, run_benchmark=False)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_03" in rule_ids

def test_rule_4_order_by(temp_db):
    query = "SELECT name, salary FROM Employee ORDER BY salary DESC LIMIT 10;"
    res = temp_db.analyze_query(query, run_benchmark=False)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_04" in rule_ids

def test_rule_6_function_on_column(temp_db):
    query = "SELECT name, email FROM Employee WHERE LOWER(email) = 'test@example.com';"
    res = temp_db.analyze_query(query, run_benchmark=False)

    rule_ids = [f.rule_id for f in res.findings]
    assert "RULE_06" in rule_ids
