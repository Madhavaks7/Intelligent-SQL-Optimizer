# Phase 2 — Batch 4: Query Playground & Multi-Query Workflow

Implement **only Batch 4** of the Intelligent SQL Query Optimization and Performance Analysis System.

Do not implement Batch 5 or any unrelated features.

---

# Existing functionality

The application already has:

- Arbitrary user-uploaded SQLite databases.
- Dynamic schema/table/column/index inspection.
- SQLGlot-based SQL analysis.
- SQLite `EXPLAIN QUERY PLAN`.
- Potential index candidate detection.
- Test Optimization workflow.
- Isolated database-copy optimization testing.
- Before/after execution plans.
- Benchmark comparison.
- Complete result-equivalence validation.
- Optimization & Analysis History.
- Session-state management.
- Database switching/state reset.

Batch 4 must **build on these systems**.

Do not rewrite or replace them.

---

# 1. Batch 4 goal

Transform the current single-query workflow into a **Query Playground** where the user can work with multiple SQL queries during the current database session.

The user should be able to:

1. Create/select a query.
2. Enter SQL.
3. Analyze it.
4. Test an optimization if available.
5. View its results.
6. Switch to another query.
7. Return to the previous query without mixing its state with another query.

The existing Optimization & Analysis History should remain the historical record.

The Query Playground is the user's **active working area**.

---

# 2. Important distinction

Do not confuse:

**Query Playground**

with:

**Optimization & Analysis History**

The playground represents the user's current working queries.

History represents completed analyses/tests from the session.

A query can exist in the playground without ever being optimized.

A completed optimization test should also appear in History according to the existing Batch 3 behavior.

---

# 3. Query management

Add a clear Query Playground section.

The user should be able to:

- Create a new query.
- Select an existing query.
- Rename a query.
- Edit its SQL.
- Delete a query.

Provide a sensible default first query, for example:

`Query 1`

Do not hardcode a specific SQL query as the application default.

---

# 4. Query naming

Each query should have:

- Internal unique identifier.
- User-visible name.
- SQL text.
- Creation/order information.

Example:

```text
Query 1 — Customer Lookup
Query 2 — Attendance Analysis
Query 3 — Order Lookup
```

The user-visible name should be editable.

Query names must not be used as database/schema logic.

---

# 5. Independent query state

This is the most important requirement of Batch 4.

Every query must maintain its own analysis/test state.

Example:

```text
Query A
SQL: SELECT * FROM customers WHERE city = 'Bengaluru'
Candidate: customers(city)
Test: completed
Result: Equivalent
Performance: 4.52 → 3.24 ms
```

Then:

```text
Query B
SQL: SELECT * FROM attendance WHERE status = 'Absent'
Candidate: attendance(status)
Test: completed
Result: Equivalent
Performance: 45.67 → 47.33 ms
```

Switching from Query A to Query B must never display Query A's:

- SQL
- candidate
- execution plan
- benchmark
- result-equivalence result
- final validation outcome

And vice versa.

---

# 6. Query editing behavior

If the user edits the SQL of an existing query:

Treat the edited SQL as a new analysis state for that query.

Clear/invalidate stale current analysis/test state associated with the previous SQL.

Do not show the previous query's:

- candidate
- execution plan
- benchmark
- optimized plan
- result-equivalence status
- final outcome

as though they belong to the new SQL.

However, **do not delete historical records** created by previous completed analyses/tests.

Example:

```text
Query: Customer Lookup

Previous SQL:
SELECT * FROM customers WHERE city = 'Bengaluru';

Tested:
4.52 → 3.24 ms
```

User changes it to:

```sql
SELECT * FROM customers WHERE city = 'Mumbai';
```

The current query should require fresh analysis.

The previous Bengaluru optimization remains in Optimization History.

---

# 7. Query switching

Switching between queries must be instantaneous from the user's perspective and must restore that query's current state.

Example:

```text
Query A
↓
Analyze
↓
Query B
↓
Analyze
↓
Query A
```

When returning to Query A, its existing analysis state should be restored.

Query B must not inherit Query A's state.

---

# 8. New query behavior

When the user creates a new query:

It should start with:

- Empty or clearly editable SQL input.
- No analysis result.
- No candidate.
- No benchmark.
- No result-equivalence result.
- No optimization outcome.

It must not inherit the previous query's state.

---

# 9. Query deletion

Allow the user to delete a playground query.

Deleting a playground query must:

- Remove it from the active Query Playground.
- Remove its active/current analysis state.

But it must **not delete Optimization & Analysis History** that was already generated from that query.

History is an audit-like session record and should remain intact.

Example:

```text
Query A
  ↓
Optimization tested
  ↓
History entry created
  ↓
Delete Query A
  ↓
History entry remains
```

---

# 10. Running SQL

Preserve the existing query execution behavior.

The playground should use the currently selected query's SQL.

Do not introduce unrestricted database modification execution.

The existing safety rules for allowed SQL statements must remain intact.

Do not weaken protections against:

- `INSERT`
- `UPDATE`
- `DELETE`
- `DROP`
- `ALTER`
- Other modifying/destructive statements

unless the existing application already explicitly supports them.

Batch 4 is not a database editor.

---

# 11. Analyze workflow

The existing Analyze action should operate on the **currently selected query only**.

Example:

```text
Selected Query:
Customer Lookup

SQL:
SELECT * FROM customers WHERE city = 'Bengaluru';

[Analyze]
```

Only Customer Lookup's state should change.

Other queries must remain untouched.

---

# 12. Test Optimization workflow

The existing `[🧪 Test Optimization]` workflow should operate only on the currently selected query and its current candidate.

Do not duplicate or rewrite the Batch 1/Batch 2 implementation.

After testing:

- The current query receives the test result.
- The existing Optimization & Analysis History receives the completed run.

---

# 13. History integration

Continue using the existing Batch 3 history system.

History entries should contain enough information to identify the originating query.

Ideally preserve:

- Query identifier, where appropriate.
- Query name.
- SQL.
- Database context.
- Timestamp.

Do not create a second history mechanism.

Do not duplicate history entries simply because the user switches between queries.

Only actual analysis/test events should create history records according to the existing Batch 3 rules.

---

# 14. Database switching

Query Playground state must be scoped to the uploaded database/session according to the application's existing database-switching behavior.

When the user switches from:

```text
university.sqlite
```

to:

```text
ecommerce.sqlite
```

do not leave active university queries available as though they belong to ecommerce.

Clear/reset the appropriate Query Playground state when changing databases.

Do not leak:

- SQL
- candidates
- execution plans
- benchmarks
- query names
- active query state

between unrelated databases.

Existing Optimization History behavior from Batch 3 must remain intact.

---

# 15. UI design

Add the Query Playground without overwhelming the existing interface.

A reasonable layout could be:

```text
┌─────────────────────────────────────┐
│ Query Playground                    │
│                                     │
│ [Query 1 ▼] [＋ New Query]          │
│                                     │
│ Name: Customer Lookup               │
│                                     │
│ SQL Editor                          │
│ ┌─────────────────────────────────┐ │
│ │ SELECT ...                      │ │
│ └─────────────────────────────────┘ │
│                                     │
│ [Analyze]                           │
└─────────────────────────────────────┘
```

The existing findings, execution plan, benchmark, and optimization sections can continue to display results for the selected query.

Do not redesign the entire application.

---

# 16. Query list

Provide a simple query selector/list.

It should make it obvious:

- Which query is selected.
- Which queries exist.
- Which queries have been analyzed.
- Which queries have completed optimization tests.

Keep this concise.

Do not add complex filtering/search in this batch.

---

# 17. Query status indicators

You may show lightweight status indicators such as:

```text
Query 1 — Customer Lookup
✓ Tested

Query 2 — Attendance Analysis
✓ Analyzed

Query 3 — New Query
○ Not Analyzed
```

These indicators must reflect actual state.

Do not show `✓ Tested` unless a Test Optimization run actually completed.

Do not show `✓ Optimized` merely because an index candidate exists.

---

# 18. Session scope

The Query Playground is session-only for this batch.

Do not implement:

- Persistent query storage.
- User accounts.
- Cloud storage.
- External database storage.
- Query sharing.

Those are outside scope.

---

# 19. Automated tests

Add focused automated tests for Batch 4.

At minimum test:

1. A new query starts with clean state.
2. Multiple queries can coexist.
3. Each query receives a unique internal identifier.
4. Switching Query A → Query B does not mix state.
5. Returning to Query A restores Query A's state.
6. Editing SQL invalidates stale analysis/test state.
7. Editing SQL does not delete previous History entries.
8. Deleting a playground query removes active query state.
9. Deleting a query does not delete existing History entries.
10. Only the selected query is analyzed.
11. Only the selected query is tested.
12. New queries do not inherit previous query state.
13. Switching databases does not leak queries between databases.
14. Existing safety restrictions remain intact.

---

# 20. Regression tests

After implementing Batch 4, run:

- All Batch 4 tests.
- Existing Batch 3 tests.
- Existing Batch 2 result-equivalence tests.
- Existing optimization tests.

Confirm that the existing:

- Analyze workflow
- Test Optimization workflow
- Result-equivalence validation
- Optimization History
- Database safety

still work.

Do not modify previous tests simply to make them pass.

---

# 21. Fixture isolation

`ecommerce.sqlite` and `university.sqlite` remain **external test fixtures only**.

Never hardcode them into production/application logic.

Do not:

- Auto-load them.
- Default to them.
- Fall back to them.
- Special-case their schemas.
- Special-case their tables.
- Special-case their columns.
- Special-case their indexes.

They may only be referenced by development/regression tests.

Production must remain completely database-agnostic.

---

# 22. Explicitly out of scope

Do NOT implement:

- SQL file upload.
- Multiple-query file parsing.
- Query workload import.
- Slow-query logs.
- PostgreSQL.
- MySQL.
- AI explanations.
- Advanced composite-index optimization.
- Historical performance charts.
- Persistent query storage.
- Query sharing.
- Monitoring.
- Automatic optimization.

Those belong to later batches.

---

# Completion requirements

Batch 4 is complete only when:

- Query Playground exists.
- Multiple queries can be created.
- Queries can be selected.
- Queries can be renamed.
- Queries can be edited.
- Queries can be deleted.
- Each query maintains independent current state.
- Query switching cannot mix results.
- Editing SQL invalidates stale current results.
- Previous completed History remains intact.
- Test Optimization works for the selected query.
- History integration works.
- Database switching does not leak query state.
- Safety restrictions remain intact.
- Automated Batch 4 tests pass.
- Batch 2 and Batch 3 regression tests still pass.

At the end, report:

1. Files changed.
2. Query Playground architecture/state structure.
3. UI changes.
4. Tests added.
5. Exact test commands executed.
6. Exact test results.
7. Any issues or limitations.

**Do not implement anything outside Batch 4.**
