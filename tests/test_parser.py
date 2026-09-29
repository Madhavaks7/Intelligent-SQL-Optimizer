import pytest
from parser.sql_parser import SQLParser

def test_parse_simple_select():
    sql = "SELECT name, salary FROM Employee WHERE department_id = 5;"
    parsed = SQLParser.parse_query(sql)

    assert parsed.is_select is True
    assert parsed.parse_error is None
    assert len(parsed.tables) == 1
    assert parsed.tables[0].name.lower() == "employee"
    assert len(parsed.selected_columns) == 2
    assert len(parsed.where_predicates) == 1
    assert parsed.where_predicates[0].column.lower() == "department_id"
    assert parsed.where_predicates[0].value == "5"

def test_parse_select_star():
    sql = "SELECT * FROM Employee;"
    parsed = SQLParser.parse_query(sql)

    assert parsed.is_select is True
    assert len(parsed.selected_columns) == 1
    assert parsed.selected_columns[0].is_wildcard is True

def test_parse_join_query():
    sql = """
    SELECT e.name, d.department_name
    FROM Employee e
    JOIN Department d ON e.department_id = d.department_id
    WHERE d.location = 'New York';
    """
    parsed = SQLParser.parse_query(sql)

    assert parsed.is_select is True
    assert len(parsed.tables) >= 1
    assert len(parsed.joins) == 1
    assert parsed.joins[0].right_table.lower() == "department"
    assert len(parsed.where_predicates) == 1

def test_parse_order_by_limit():
    sql = "SELECT name, salary FROM Employee ORDER BY salary DESC LIMIT 10;"
    parsed = SQLParser.parse_query(sql)

    assert len(parsed.order_by) == 1
    assert parsed.order_by[0].column.lower() == "salary"
    assert parsed.order_by[0].direction == "DESC"
    assert parsed.limit == 10

def test_invalid_sql():
    sql = "SELEEEEECT name FROM Employee WHERE;"
    parsed = SQLParser.parse_query(sql)

    assert parsed.parse_error is not None
