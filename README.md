# Intelligent SQL Query Optimization and Performance Analysis System

A college 3rd-year Computer Science mini-project prototype for deterministic SQL AST analysis, database schema inspection, execution plan parsing (`EXPLAIN QUERY PLAN`), index recommendation, and safe before/after performance benchmarking.

> [!IMPORTANT]
> **Deterministic Core Engine**: This system is **NOT an LLM wrapper**. All core SQL AST parsing, schema introspection, SQLite `EXPLAIN QUERY PLAN` parsing, rule matching, DDL index recommendations, and benchmark execution operate 100% locally and deterministically without requiring an LLM or external API.

---

## 1. Problem Statement & Motivation

Relational databases are the backbone of modern software systems, yet un-optimized SQL queries frequently cause server performance bottlenecks, high CPU utilization, and slow response times. Developers often write queries without understanding how the database query engine executes them (e.g. performing full table scans instead of indexed lookups).

Existing commercial APM tools (such as Datadog or SolarWinds) are expensive enterprise solutions. This project provides an accessible, educational prototype that inspects user SQL queries, explains SQLite query execution plans, identifies performance anti-patterns using deterministic rules, recommends specific single/composite indexes or rewrites, and measures exact performance speedups in an isolated benchmark environment.

---

## 2. Key Objectives

1. **Deterministic AST Parsing**: Extract structured query components (tables, columns, predicates, joins, order by, limit) using `SQLGlot`.
2. **Database Metadata Introspection**: Inspect SQLite system catalog tables (`PRAGMA table_info`, `PRAGMA index_list`, `PRAGMA index_info`) to check existing index coverage.
3. **Execution Plan Analysis**: Execute and parse SQLite's `EXPLAIN QUERY PLAN` to distinguish between `SCAN TABLE` (Full scan) and `SEARCH TABLE ... USING INDEX` (Index lookup).
4. **Deterministic Rule Engine**: Apply 10 optimization rules to detect missing indexes, composite index opportunities, temporary B-tree sorting, unindexed joins, wildcard `SELECT *` usages, and function transformations on columns.
5. **Safe Performance Benchmarking**: Run before-and-after execution comparisons in an isolated temporary database copy over multiple warm-up and measured iterations.
6. **Polished Dashboard & Optional AI Layer**: Present findings via a Streamlit interface, with an optional AI explanation layer for supplemental natural language summaries.

---

## 3. High-Level Architecture

```text
                    USER
                      |
                      v
              Streamlit Frontend (app.py)
                      |
                      v
            Query Manager / Orchestrator
                      |
          +-----------+-----------+
          |                       |
          v                       v
     SQL Parser             Database Manager
      (SQLGlot)                   |
          |                       |
          v                       v
     AST Analyzer          Schema / Index Analyzer
          |                       |
          +-----------+-----------+
                      |
                      v
            Query Plan Analyzer (EXPLAIN QUERY PLAN)
                      |
                      v
            Optimization Engine (Deterministic Rules)
                      |
             +--------+--------+
             |                 |
             v                 v
       Recommendations    Query Rewriter
             |                 |
             +--------+--------+
                      |
                      v
             Performance Analyzer (Safe Benchmarking)
                      |
                      v
             Before vs After Comparison
                      |
                      v
             Streamlit UI & Optional AI Explainer
```

---

## 4. Optimization Methodology & Rules

The system evaluates queries against 10 deterministic rules categorized by severity:

| Rule ID | Rule Name | Category | Severity | Description |
| :--- | :--- | :--- | :--- | :--- |
| **RULE_01** | Wildcard `SELECT *` | Query Design | Medium | Flags fetching all table columns; recommends specifying explicit columns. |
| **RULE_02** | Missing Single Index | Indexing | High | Detects full table `SCAN` on unindexed filter column; recommends single-column `CREATE INDEX`. |
| **RULE_03** | Composite Index | Indexing | High | Recommends composite index `(col1, col2)` for queries with multiple unindexed predicates. |
| **RULE_04** | Temporary B-Tree Sort | Sorting | Medium | Detects `USE TEMP B-TREE FOR ORDER BY`; recommends sorting index to eliminate O(N log N) sorting. |
| **RULE_05** | Unindexed JOIN | Indexing | High | Detects full table `SCAN` on joined tables; recommends index on foreign key columns. |
| **RULE_06** | Function on Column | Query Design | Medium | Flags expressions like `LOWER(email)` that suppress standard B-tree index lookup. |
| **RULE_07** | Unnecessary `DISTINCT` | Query Design | Info | Warns when `DISTINCT` forces sorting/hashing deduplication. |
| **RULE_08** | Leading Wildcard `LIKE` | Query Design | Medium | Flags `LIKE '%term'` which cannot utilize standard B-tree index. |
| **RULE_09** | Missing `LIMIT` | Query Design | Info | Advises restricting result set size on large table scans. |
| **RULE_10** | `HAVING` vs `WHERE` | Query Design | Info | Advises moving non-aggregate filters from `HAVING` to `WHERE` to reduce grouped dataset size. |

---

## 5. Technology Stack

- **Core & Logic**: Python 3.11+, SQLite 3
- **SQL AST Parsing**: `sqlglot`
- **Frontend Dashboard**: `streamlit`
- **Data & Metrics**: `pandas`
- **Testing**: `pytest`
- **Optional AI Layer**: `requests` (OpenAI / Anthropic / Gemini API compatible)

---

## 6. Sample Database Design

The system includes a seeding script (`database/seed_database.py`) that generates **~52,750 synthetic records** across 7 normalized tables:

1. **`Employee`** (10,000 rows): `employee_id` (PK), `name`, `department_id`, `salary`, `email`, `hire_date`, `job_title`
2. **`Department`** (50 rows): `department_id` (PK), `department_name`, `location`
3. **`Project`** (200 rows): `project_id` (PK), `project_name`, `budget`, `department_id`
4. **`EmployeeProject`** (15,000 rows): `employee_id`, `project_id`, `hours_allocated`, `role`
5. **`Customer`** (2,000 rows): `customer_id` (PK), `name`, `email`, `city`, `created_at`
6. **`Products`** (500 rows): `product_id` (PK), `product_name`, `category`, `price`
7. **`Orders`** (25,000 rows): `order_id` (PK), `customer_id`, `product_id`, `order_date`, `quantity`, `amount`, `status`

*Note*: Secondary foreign key columns are intentionally left unindexed initially so full table scans are triggered and measured.

---

## 7. How `EXPLAIN QUERY PLAN` Works

SQLite's `EXPLAIN QUERY PLAN <query>` command exposes the query planner's chosen execution strategy:
- `SCAN TABLE Employee`: SQLite reads every page of the 10,000-row table sequentially $O(N)$.
- `SEARCH TABLE Employee USING INDEX idx_employee_dept`: SQLite performs a direct B-tree search $O(\log N)$.
- `USE TEMP B-TREE FOR ORDER BY`: SQLite allocates an in-memory B-tree to sort unindexed output rows.

---

## 8. Role of Optional LLM Explanation Layer

The optional LLM layer (OpenAI / Gemini / Anthropic compatible) is used strictly as an **educational explanation layer**.

- **Strict Constraint**: The LLM is **NOT** the source of truth for query plans, execution timings, schema structure, or index recommendations.
- **Graceful Fallback**: If no API key is configured or network requests fail, the application hides the AI tab and remains 100% functional.

---

## 9. Academic Integrity & Terminology Standard

To maintain academic rigor and avoid overhyped claims:
- **Observed Fact**: "SQLite performed a full table scan on table Employee."
- **Recommendation**: "Creating an index on Employee(department_id) may allow logarithmic B-tree lookups."
- **Measured Result**: "On the supplied test dataset of 10,000 records, execution time changed from 4.82 ms to 0.12 ms (40.1x speedup)."

---

## 10. Installation & Running Instructions

### Prerequisites
- Python 3.11 or higher
- `pip` package manager

### Setup Steps

1. **Clone or Navigate to Project Directory**:
   ```bash
   cd Intelligent_SQL_Optimizer
   ```

2. **Install Dependencies**:
   ```bash
   python -m pip install -r requirements.txt
   ```

3. **Seed Database (~50,000 records)**:
   ```bash
   python database/seed_database.py
   ```

4. **Run Unit Tests**:
   ```bash
   python -m pytest
   ```

5. **Launch Streamlit Web Dashboard**:
   ```bash
   python -m streamlit run app.py
   ```

---

## 11. Pre-loaded Demo Queries

The dashboard includes 5 pre-configured demo queries accessible from the sidebar:

- **Demo 1 — Missing Index**:
  ```sql
  SELECT name, salary, hire_date FROM Employee WHERE department_id = 5;
  ```
- **Demo 2 — Composite Index**:
  ```sql
  SELECT employee_id, name, salary FROM Employee WHERE department_id = 5 AND salary > 80000;
  ```
- **Demo 3 — Unindexed JOIN**:
  ```sql
  SELECT e.name, e.salary, d.department_name, d.location
  FROM Employee e
  JOIN Department d ON e.department_id = d.department_id
  WHERE d.department_name = 'Engineering 1';
  ```
- **Demo 4 — ORDER BY Sort**:
  ```sql
  SELECT name, salary, hire_date FROM Employee ORDER BY salary DESC LIMIT 20;
  ```
- **Demo 5 — SELECT * & Status Filter**:
  ```sql
  SELECT * FROM Orders WHERE status = 'PENDING';
  ```

---

## 12. Future Work & Scope Boundaries

**Out of Scope for Mini-Project**:
- PostgreSQL / MySQL wire protocol optimizers
- Reinforcement learning cost estimators
- Multi-node distributed query optimization

**Future Extensions**:
- Support for PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)`
- Expression index recommendations for custom scalar functions
- Workload-level index tuning across query logs

---

## 13. License

Developed for academic demonstration purposes.
