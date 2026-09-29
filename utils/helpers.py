from typing import Dict, List

DEMO_QUERIES: Dict[str, Dict[str, str]] = {
    "Demo 1 — Missing Index (Full Table Scan)": {
        "title": "Missing Index on Filter Column",
        "sql": """SELECT name, salary, hire_date
FROM Employee
WHERE department_id = 5;""",
        "description": "Filters 10,000 employee records by department_id without an index, forcing a full table SCAN."
    },
    "Demo 2 — Multiple Filters (Composite Index)": {
        "title": "Multiple Filters Requiring Composite Index",
        "sql": """SELECT employee_id, name, salary
FROM Employee
WHERE department_id = 5
AND salary > 80000;""",
        "description": "Filters on department_id AND salary. Demonstrates composite index (department_id, salary) recommendation."
    },
    "Demo 3 — Unindexed Table JOIN": {
        "title": "Unindexed JOIN Operation",
        "sql": """SELECT e.name, e.salary, d.department_name, d.location
FROM Employee e
JOIN Department d ON e.department_id = d.department_id
WHERE d.department_name = 'Engineering 1';""",
        "description": "Performs a JOIN between Employee and Department on foreign key department_id without a secondary index."
    },
    "Demo 4 — ORDER BY Temporary B-Tree Sort": {
        "title": "ORDER BY Temporary B-Tree Sorting",
        "sql": """SELECT name, salary, hire_date
FROM Employee
ORDER BY salary DESC
LIMIT 20;""",
        "description": "Orders 10,000 employees by salary without a pre-sorted index, triggering an in-memory temporary B-tree sort."
    },
    "Demo 5 — SELECT * Wildcard & Status Filter": {
        "title": "SELECT * Wildcard & Large Scan",
        "sql": """SELECT *
FROM Orders
WHERE status = 'PENDING';""",
        "description": "Uses SELECT * wildcard and filters 25,000 order records by unindexed status column."
    }
}

def format_sql(sql: str) -> str:
    """Basic SQL beautifier/formatter."""
    import sqlglot
    try:
        return sqlglot.transpile(sql, read="sqlite", pretty=True)[0]
    except Exception:
        return sql.strip()

def format_ms(ms: float) -> str:
    """Formats milliseconds string cleanly."""
    if ms < 0.01:
        return "< 0.01 ms"
    return f"{ms:.2f} ms"
