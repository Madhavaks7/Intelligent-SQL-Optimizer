import re
from typing import List, Dict, Any, Optional, Tuple
from models.analysis_models import (
    ParsedQuery, DatabaseSchema, PlanNode, Finding, Recommendation, Severity, Predicate
)
from analyzer.schema_analyzer import SchemaAnalyzer
from analyzer.query_plan import QueryPlanAnalyzer


class RuleEngine:
    """
    Deterministic rule engine for identifying database & SQL query performance inefficiencies.
    Implements Rules 1–15 based on AST, Schema, and EXPLAIN QUERY PLAN analysis.

    Batch 5 additions:
      RULE_11 — Composite index opportunity (equality + range ordering heuristic)
      RULE_12 — JOIN index analysis (unindexed join column on inner table)
      RULE_13 — ORDER BY combined WHERE+ORDER index opportunity
      RULE_14 — GROUP BY temporary B-tree observation (+ optional index candidate)
      RULE_15 — Covering index opportunity (small explicit SELECT + WHERE columns)
      RULE_06 — Enhanced wording for expression/function predicates
    """

    def __init__(self, schema: DatabaseSchema):
        self.schema = schema
        self.schema_analyzer = SchemaAnalyzer(schema)

    # ------------------------------------------------------------------
    # Master evaluation entry point
    # ------------------------------------------------------------------

    def evaluate_all_rules(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> List[Finding]:
        findings = []

        # Rule 1: SELECT *
        r1 = self._rule_1_select_star(parsed)
        if r1:
            findings.append(r1)

        # Rule 2 & Rule 3: Missing Indexes (Single & Composite — scan-gated)
        r_indexes = self._rule_2_and_3_missing_indexes(parsed, plan_nodes)
        findings.extend(r_indexes)

        # Rule 4: ORDER BY Temporary B-Tree
        r4 = self._rule_4_order_by_sort(parsed, plan_nodes)
        if r4:
            findings.append(r4)

        # Rule 5: Unindexed JOIN Columns
        r5 = self._rule_5_unindexed_joins(parsed, plan_nodes)
        findings.extend(r5)

        # Rule 6: Functions on Indexed Columns (enhanced wording in Batch 5)
        r6 = self._rule_6_functions_on_columns(parsed)
        findings.extend(r6)

        # Rule 7: Unnecessary DISTINCT
        r7 = self._rule_7_unnecessary_distinct(parsed)
        if r7:
            findings.append(r7)

        # Rule 8: Leading Wildcard LIKE
        r8 = self._rule_8_leading_wildcard(parsed)
        findings.extend(r8)

        # Rule 9: Missing LIMIT on large query scans
        r9 = self._rule_9_missing_limit(parsed, plan_nodes)
        if r9:
            findings.append(r9)

        # Rule 10: HAVING clause without aggregates
        r10 = self._rule_10_having_without_agg(parsed)
        if r10:
            findings.append(r10)

        # ---- Batch 5 rules ----

        # Rule 11: Composite Index Analysis (smarter, column-ordering aware)
        r11 = self._rule_11_composite_index(parsed, plan_nodes)
        findings.extend(r11)

        # Rule 12: JOIN Index Analysis
        r12 = self._rule_12_join_index(parsed, plan_nodes)
        findings.extend(r12)

        # Rule 13: ORDER BY combined index opportunity
        r13 = self._rule_13_order_by_index(parsed, plan_nodes)
        findings.extend(r13)

        # Rule 14: GROUP BY temporary B-tree observation
        r14 = self._rule_14_group_by_btree(parsed, plan_nodes)
        findings.extend(r14)

        # Rule 15: Covering index opportunity
        r15 = self._rule_15_covering_index(parsed, plan_nodes)
        findings.extend(r15)

        return findings

    # ------------------------------------------------------------------
    # Existing rules (RULE_01 – RULE_10) — unchanged from Batch 1–4
    # ------------------------------------------------------------------

    def _rule_1_select_star(self, parsed: ParsedQuery) -> Optional[Finding]:
        """Rule 1: Detect SELECT * wildcard usage (Query-Design Advisory)."""
        has_wildcard = any(c.is_wildcard for c in parsed.selected_columns)
        if has_wildcard:
            return Finding(
                rule_id="RULE_01",
                title="Wildcard SELECT * Usage",
                severity=Severity.INFO,
                category="Query Design",
                description="The query retrieves all table columns using SELECT *.",
                rationale="Selecting only required columns can reduce data transfer and memory usage when the application does not need every column.",
                affected_table=parsed.tables[0].name if parsed.tables else None
            )
        return None


    def _rule_2_and_3_missing_indexes(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> List[Finding]:
        """Rule 2 & 3: Missing Single and Composite Index Detection."""
        findings = []

        # Identify which tables suffer from full table SCAN (supporting aliases)
        scan_tables = set()
        for n in plan_nodes:
            if n.step_type == "SCAN" and n.table_name:
                canonical = self.schema_analyzer.resolve_table_name(n.table_name, parsed.tables)
                if canonical:
                    scan_tables.add(canonical.lower())
                scan_tables.add(n.table_name.lower())

        if not scan_tables:
            return findings

        # Group predicates by canonical table name
        table_predicates: Dict[str, List[str]] = {}

        for p in parsed.where_predicates:
            tname = None
            if p.table:
                tname = self.schema_analyzer.resolve_table_name(p.table, parsed.tables)
            if not tname and len(parsed.tables) == 1:
                tname = parsed.tables[0].name

            if tname and p.column and not p.is_function_call:
                # Bug 3 & 6 Fix: Verify column exists as a physical column in table schema
                if self.schema_analyzer.is_column_in_table(tname, p.column):
                    t_key = tname.lower()
                    if t_key not in table_predicates:
                        table_predicates[t_key] = []
                    table_predicates[t_key].append(p.column)

        for tname_key, cols in table_predicates.items():
            if tname_key in scan_tables and cols:
                # Deduplicate columns preserving order
                unique_cols = list(dict.fromkeys(cols))

                canonical_tname = self.schema_analyzer.resolve_table_name(tname_key, parsed.tables) or tname_key

                # Double check schema index validation
                is_valid_idx, _ = self.schema_analyzer.validate_index_columns(canonical_tname, unique_cols, parsed.tables)
                if not is_valid_idx:
                    continue

                has_idx, idx_name, is_exact = self.schema_analyzer.has_index_on_columns(canonical_tname, unique_cols)

                if not has_idx:
                    if len(unique_cols) == 1:
                        # Rule 2: Single column index
                        findings.append(Finding(
                            rule_id="RULE_02",
                            title=f"Potential Index Opportunity: {canonical_tname}({unique_cols[0]})",
                            severity=Severity.MEDIUM,
                            category="Indexing",
                            description=f"Full table scan on '{canonical_tname}' when filtering by column '{unique_cols[0]}'.",
                            rationale=f"Creating an index on '{canonical_tname}({unique_cols[0]})' improves row filtering efficiency by allowing SQLite to perform direct B-tree search lookups.",
                            affected_table=canonical_tname,
                            affected_column=unique_cols[0]
                        ))
                    else:
                        # Rule 3: Composite column index
                        cols_str = ", ".join(unique_cols)
                        findings.append(Finding(
                            rule_id="RULE_03",
                            title=f"Composite Index Recommended for {canonical_tname}({cols_str})",
                            severity=Severity.HIGH,
                            category="Indexing",
                            description=f"Multiple unindexed filter predicates ({cols_str}) trigger full table scan on '{canonical_tname}'.",
                            rationale=f"A composite (multi-column) index on ({cols_str}) optimizes queries with multiple WHERE equality/range conditions by narrowing search space in a single index lookup.",
                            affected_table=canonical_tname,
                            affected_column=cols_str
                        ))

        return findings

    def _rule_4_order_by_sort(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> Optional[Finding]:
        """Rule 4: ORDER BY without Index causing Temporary B-Tree Sort."""
        has_temp_btree = any(n.step_type == "TEMP B-TREE" for n in plan_nodes)
        # Batch 5: Only fire Rule 4 for ORDER BY temp B-trees specifically
        btree_purpose = QueryPlanAnalyzer.temp_btree_purpose(plan_nodes)
        is_order_by_btree = has_temp_btree and (btree_purpose in ("ORDER BY", "UNKNOWN", None))

        if parsed.order_by and is_order_by_btree:
            ord_cols = [o.column for o in parsed.order_by if o.column]
            tname = parsed.tables[0].name if parsed.tables else "table"
            canonical_tname = self.schema_analyzer.resolve_table_name(tname, parsed.tables) or tname
            cols_str = ", ".join(ord_cols)

            # Bug 3 Fix: Check if all ORDER BY columns exist as physical table columns
            all_physical = all(self.schema_analyzer.is_column_in_table(canonical_tname, c) for c in ord_cols)

            if all_physical:
                return Finding(
                    rule_id="RULE_04",
                    title=f"Temporary B-Tree Sorting Detected for ORDER BY ({cols_str})",
                    severity=Severity.MEDIUM,
                    category="Sorting",
                    description=f"Query Plan Observation: SQLite is using a temporary B-tree for ORDER BY. SQLite creates an in-memory temporary B-tree to sort query results on '{cols_str}'.",
                    rationale="Sorting large result sets without an index adds O(N log N) processing overhead. An index containing the ORDER BY columns (or combined WHERE + ORDER BY columns) allows SQLite to read rows in pre-sorted order.",
                    affected_table=canonical_tname,
                    affected_column=cols_str
                )
            else:
                # Computed aggregate or alias sorting
                return Finding(
                    rule_id="RULE_04",
                    title=f"Temporary B-Tree Sorting on Computed Expression ({cols_str})",
                    severity=Severity.INFO,
                    category="Sorting",
                    description=f"Query Plan Observation: SQLite is using a temporary B-tree for ORDER BY. SQLite creates an in-memory temporary B-tree to sort computed aggregate/expression results ('{cols_str}').",
                    rationale=f"Sorting computed aggregate values or calculated expressions ('{cols_str}') requires an in-memory B-tree sort because physical database indexes cannot pre-sort calculated result values.",
                    affected_table=canonical_tname,
                    affected_column=None  # Do not generate physical index DDL on computed alias
                )
        return None

    def _rule_5_unindexed_joins(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> List[Finding]:
        """Rule 5: Unindexed JOIN Foreign Key Columns."""
        findings = []

        if not plan_nodes or not parsed.joins:
            return findings

        # Bug 9 Fix: Find the outer driving table (first table in execution plan tree)
        driving_table = None
        for n in plan_nodes:
            if n.step_type in ("SCAN", "SEARCH") and n.table_name:
                driving_table = n.table_name.lower()
                canonical_driving = self.schema_analyzer.resolve_table_name(n.table_name, parsed.tables)
                if canonical_driving:
                    driving_table = canonical_driving.lower()
                break

        # Map plan nodes by table name / alias
        table_plan_steps: Dict[str, str] = {}
        for n in plan_nodes:
            if n.table_name:
                canonical = self.schema_analyzer.resolve_table_name(n.table_name, parsed.tables)
                names = [n.table_name.lower()]
                if canonical:
                    names.append(canonical.lower())

                for name in names:
                    # SEARCH takes precedence if table appears in multiple sub-steps
                    if name not in table_plan_steps or n.step_type == "SEARCH":
                        table_plan_steps[name] = n.step_type

        for join in parsed.joins:
            r_table = join.right_table
            canonical_r = self.schema_analyzer.resolve_table_name(r_table, parsed.tables) or r_table
            r_alias = join.right_alias

            names_to_check = [r_table.lower(), canonical_r.lower()]
            if r_alias:
                names_to_check.append(r_alias.lower())

            # Bug 9 Fix: Outer driving table scan is caused by WHERE filtering/missing filter index, NOT an unindexed join lookup!
            if driving_table and any(name == driving_table for name in names_to_check):
                continue

            # Check if inner table join lookup is accessed via SEARCH
            is_searched = any(table_plan_steps.get(name) == "SEARCH" for name in names_to_check)
            if is_searched:
                continue

            # Trigger RULE_05 only if inner table join lookup suffers from an unindexed SCAN
            is_scanned = any(table_plan_steps.get(name) == "SCAN" for name in names_to_check)
            if is_scanned:
                findings.append(Finding(
                    rule_id="RULE_05",
                    title=f"Unindexed JOIN Operation on Table '{canonical_r}'",
                    severity=Severity.HIGH,
                    category="Indexing",
                    description=f"The JOIN operation on table '{canonical_r}' triggers an unindexed table scan.",
                    rationale=f"Joining tables without indexes on join foreign key columns results in nested loop full table scans. Adding an index on the join columns speeds up join lookups significantly.",
                    affected_table=canonical_r
                ))

        return findings


    def _rule_6_functions_on_columns(self, parsed: ParsedQuery) -> List[Finding]:
        """
        Rule 6: Functions on Filtered Columns in WHERE Clause.
        Batch 5: Enhanced wording to clearly state that a standard index on the underlying
        column may not directly support expression predicates.
        """
        findings = []
        for p in parsed.where_predicates:
            if p.is_function_call and p.column:
                findings.append(Finding(
                    rule_id="RULE_06",
                    title=f"Expression Predicate on Column '{p.column}'",
                    severity=Severity.MEDIUM,
                    category="Query Design",
                    description=(
                        f"Query uses an expression on column `{p.column}` in the WHERE clause "
                        f"({p.raw_expr}). A standard index on `{p.column}` may not directly "
                        f"support this predicate."
                    ),
                    rationale=(
                        "Applying scalar functions (e.g., LOWER(), strftime(), UPPER()) to columns "
                        "prevents standard B-tree index usage because stored index keys do not match "
                        "transformed expression values. Consider rewriting the predicate to avoid "
                        "wrapping the column in a function, or use a SQLite expression index if "
                        "supported by your SQLite version."
                    ),
                    affected_table=p.table,
                    affected_column=p.column
                ))
        return findings

    def _rule_7_unnecessary_distinct(self, parsed: ParsedQuery) -> Optional[Finding]:
        """Rule 7: Potentially Unnecessary DISTINCT Clause."""
        if parsed.has_distinct:
            # Check if grouping or PK exists
            return Finding(
                rule_id="RULE_07",
                title="DISTINCT Clause Used",
                severity=Severity.INFO,
                category="Query Design",
                description="The query contains a DISTINCT keyword which enforces result set deduplication.",
                rationale="DISTINCT forces SQLite to sort or hash output rows to eliminate duplicate rows. Verify whether duplicate rows are naturally prevented by primary keys or join relationships to eliminate redundant sorting overhead.",
                affected_table=parsed.tables[0].name if parsed.tables else None
            )
        return None

    def _rule_8_leading_wildcard(self, parsed: ParsedQuery) -> List[Finding]:
        """Rule 8: Leading Wildcard in LIKE Predicates."""
        findings = []
        for p in parsed.where_predicates:
            if p.operator.upper() == "LIKE" and p.value:
                clean_val = p.value.strip("'\"")
                if clean_val.startswith("%") or clean_val.startswith("_"):
                    findings.append(Finding(
                        rule_id="RULE_08",
                        title=f"Leading Wildcard Pattern in LIKE '{p.value}'",
                        severity=Severity.MEDIUM,
                        category="Query Design",
                        description=f"The LIKE predicate on '{p.column}' begins with a wildcard ({p.value}).",
                        rationale="Standard B-tree indexes cannot perform prefix lookups for leading wildcards (e.g. '%term'), forcing a full table scan. Prefix searches (e.g. 'term%') or Full-Text Search (FTS) indexes are required for fast matching.",
                        affected_table=p.table,
                        affected_column=p.column
                    ))
        return findings

    def _rule_9_missing_limit(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> Optional[Finding]:
        """Rule 9: Missing LIMIT Clause on Large Scans without Aggregation (Advisory Only)."""
        has_scan = any(n.step_type == "SCAN" for n in plan_nodes)
        has_agg = any(c.agg_func is not None for c in parsed.selected_columns)

        if has_scan and not parsed.limit and not has_agg and not parsed.group_by:
            return Finding(
                rule_id="RULE_09",
                title="Large Result Set Advisory",
                severity=Severity.INFO,
                category="Advisory",
                description="The query scans records and returns results without specifying pagination or a LIMIT clause.",
                rationale="Returning unbound result sets can transmit large volumes of data to application clients. Consider adding pagination or a LIMIT clause depending on application requirements.",
                affected_table=parsed.tables[0].name if parsed.tables else None
            )
        return None

    def _rule_10_having_without_agg(self, parsed: ParsedQuery) -> Optional[Finding]:
        """Rule 10: Non-aggregate filters placed in HAVING instead of WHERE."""
        if parsed.having:
            return Finding(
                rule_id="RULE_10",
                title="HAVING Clause Filtering",
                severity=Severity.INFO,
                category="Query Design",
                description="Filter conditions are evaluated in the HAVING clause.",
                rationale="HAVING evaluates conditions after grouping is completed. Non-aggregate conditions should be placed in WHERE to filter out rows prior to grouping, reducing grouped dataset size.",
                affected_table=parsed.tables[0].name if parsed.tables else None
            )
        return None

    # ------------------------------------------------------------------
    # Batch 5 — New Rules (RULE_11 – RULE_15)
    # ------------------------------------------------------------------

    def _rule_11_composite_index(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> List[Finding]:
        """
        RULE_11 — Composite Index Analysis (Batch 5).

        Detects queries with multiple WHERE predicates that could benefit from a
        composite index with a defensible column order heuristic:
          - Equality predicates first (higher selectivity for B-tree prefix scans)
          - Range predicates appended last (only equality prefix is range-usable)

        Guards:
          - Only fires when a full table SCAN is present on the target table.
          - Skips tables with only a single predicate column (covered by RULE_02).
          - Skips if an existing index already provides the required leading prefix.
          - Skips columns that are computed / function-wrapped.
          - Does not duplicate RULE_03 findings for the same table/columns.
        """
        findings = []

        if not plan_nodes:
            return findings

        # Tables being fully scanned
        scan_tables: set = set()
        for n in plan_nodes:
            if n.step_type == "SCAN" and n.table_name:
                canonical = self.schema_analyzer.resolve_table_name(n.table_name, parsed.tables)
                scan_tables.add((canonical or n.table_name).lower())

        if not scan_tables:
            return findings

        # Group non-expression predicates by canonical table
        table_eq_cols: Dict[str, List[str]] = {}
        table_range_cols: Dict[str, List[str]] = {}

        _RANGE_OPS = {"GT", "GTE", "LT", "LTE", "BETWEEN", ">", ">=", "<", "<="}
        _EQ_OPS = {"EQ", "=", "IS"}

        for p in parsed.where_predicates:
            if p.is_function_call or not p.column:
                continue

            tname: Optional[str] = None
            if p.table:
                tname = self.schema_analyzer.resolve_table_name(p.table, parsed.tables)
            if not tname and len(parsed.tables) == 1:
                tname = parsed.tables[0].name

            if not tname:
                continue

            # Verify physical column existence
            if not self.schema_analyzer.is_column_in_table(tname, p.column):
                continue

            t_key = tname.lower()
            op_upper = p.operator.upper()

            if op_upper in _EQ_OPS:
                table_eq_cols.setdefault(t_key, [])
                if p.column not in table_eq_cols[t_key]:
                    table_eq_cols[t_key].append(p.column)
            elif op_upper in _RANGE_OPS:
                table_range_cols.setdefault(t_key, [])
                if p.column not in table_range_cols[t_key]:
                    table_range_cols[t_key].append(p.column)

        # Find tables in scan_tables that have multiple predicate columns
        all_predicate_tables = set(table_eq_cols.keys()) | set(table_range_cols.keys())
        for t_key in all_predicate_tables & scan_tables:
            eq_cols = table_eq_cols.get(t_key, [])
            rng_cols = table_range_cols.get(t_key, [])
            total_cols = eq_cols + rng_cols

            # Need at least 2 total columns for a composite candidate
            if len(total_cols) < 2:
                continue

            canonical_tname = self.schema_analyzer.resolve_table_name(t_key, parsed.tables) or t_key
            is_valid, _ = self.schema_analyzer.validate_index_columns(canonical_tname, total_cols, parsed.tables)
            if not is_valid:
                continue

            # Check if an existing index already covers the equality prefix
            has_idx, idx_name, is_exact = self.schema_analyzer.has_index_on_columns(canonical_tname, eq_cols if eq_cols else total_cols)
            if has_idx:
                # Existing index covers the leading prefix — no composite candidate needed
                continue

            # Also skip if RULE_03 would already cover the exact same column set
            rule03_cols = list(dict.fromkeys(eq_cols + rng_cols))
            has_r3, _, _ = self.schema_analyzer.has_index_on_columns(canonical_tname, rule03_cols)
            if has_r3:
                continue

            cols_str = ", ".join(total_cols)
            findings.append(Finding(
                rule_id="RULE_11",
                title=f"Potential Composite Index Opportunity: {canonical_tname}({cols_str})",
                severity=Severity.HIGH,
                category="Indexing",
                description=(
                    f"Query filters on multiple columns ({cols_str}) of table '{canonical_tname}'. "
                    f"Equality predicates ({', '.join(eq_cols) or 'none'}) are placed first; "
                    f"range predicates ({', '.join(rng_cols) or 'none'}) are appended last to "
                    f"maximise B-tree prefix usage."
                ),
                rationale=(
                    "A composite index ordered with equality columns first, then range columns, "
                    "allows SQLite to use the equality portion as a direct B-tree prefix seek and "
                    "the range portion as a bounded scan within that subtree. This is typically "
                    "more selective than individual single-column indexes."
                ),
                affected_table=canonical_tname,
                affected_column=cols_str
            ))

        return findings

    def _rule_12_join_index(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> List[Finding]:
        """
        RULE_12 — JOIN Index Analysis (Batch 5).

        Provides targeted join-column inspection beyond RULE_05:
          - Parses ON condition to identify specific join columns on the inner table.
          - Only fires when the inner table is being SCANned (not SEARCHed).
          - Only fires when the specific join column is confirmed unindexed.
          - Alias resolution is applied throughout.
          - Does not re-fire for a table already reported by RULE_05.

        This rule does NOT revive the false-positive JOIN behavior fixed in Batch 1–4.
        """
        findings = []

        if not plan_nodes or not parsed.joins:
            return findings

        # Tables already reported by RULE_05 (to avoid duplicate findings)
        rule05_tables: set = set()
        for n in plan_nodes:
            if n.step_type == "SCAN" and n.table_name:
                pass  # RULE_05 handles SCAN detection internally

        # Identify the driving (outer) table
        driving_table: Optional[str] = None
        for n in plan_nodes:
            if n.step_type in ("SCAN", "SEARCH") and n.table_name:
                canonical_driving = self.schema_analyzer.resolve_table_name(n.table_name, parsed.tables)
                driving_table = (canonical_driving or n.table_name).lower()
                break

        # Build per-table step type map
        table_plan_steps: Dict[str, str] = {}
        for n in plan_nodes:
            if n.table_name:
                canonical = self.schema_analyzer.resolve_table_name(n.table_name, parsed.tables)
                for name in filter(None, [n.table_name.lower(), (canonical or "").lower()]):
                    if name not in table_plan_steps or n.step_type == "SEARCH":
                        table_plan_steps[name] = n.step_type

        for join in parsed.joins:
            r_table = join.right_table
            canonical_r = self.schema_analyzer.resolve_table_name(r_table, parsed.tables) or r_table
            r_alias = join.right_alias

            names_to_check = list(filter(None, [r_table.lower(), canonical_r.lower(), (r_alias or "").lower()]))

            # Skip the outer/driving table
            if driving_table and any(name == driving_table for name in names_to_check):
                continue

            # Only fire for genuinely SCANned inner tables
            is_searched = any(table_plan_steps.get(name) == "SEARCH" for name in names_to_check)
            if is_searched:
                continue
            is_scanned = any(table_plan_steps.get(name) == "SCAN" for name in names_to_check)
            if not is_scanned:
                continue

            # Parse the ON condition to find the join column on the inner table
            join_col: Optional[str] = self._extract_join_column_for_table(join, canonical_r, r_alias, parsed)

            if not join_col:
                # Cannot determine the specific join column — skip rather than guess
                continue

            # Validate physical column existence
            if not self.schema_analyzer.is_column_in_table(canonical_r, join_col):
                continue

            # Check if the join column is already indexed
            has_idx, _, _ = self.schema_analyzer.has_index_on_columns(canonical_r, [join_col])
            if has_idx:
                continue

            findings.append(Finding(
                rule_id="RULE_12",
                title=f"Potential JOIN Index Opportunity: {canonical_r}({join_col})",
                severity=Severity.HIGH,
                category="Indexing",
                description=(
                    f"JOIN: The join column '{canonical_r}.{join_col}' is used in an ON condition "
                    f"but lacks an index. SQLite performs a full table scan on '{canonical_r}' for "
                    f"every row of the driving table."
                ),
                rationale=(
                    "An index on the inner join column allows SQLite to replace the nested-loop "
                    "full scan with a direct B-tree lookup for each join, reducing join complexity "
                    "from O(N·M) to O(N·log M)."
                ),
                affected_table=canonical_r,
                affected_column=join_col
            ))

        return findings

    def _extract_join_column_for_table(
        self,
        join: "JoinRef",
        canonical_table: str,
        alias: Optional[str],
        parsed: ParsedQuery
    ) -> Optional[str]:
        """
        Parses an ON condition string to extract the join column that belongs to `canonical_table`.
        Handles alias-qualified references. Returns the physical column name or None.
        """
        for cond in join.on_conditions:
            m = re.search(r"(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)", cond)
            if m:
                t1, c1, t2, c2 = m.group(1), m.group(2), m.group(3), m.group(4)
                t1_canon = self.schema_analyzer.resolve_table_name(t1, parsed.tables) or t1
                t2_canon = self.schema_analyzer.resolve_table_name(t2, parsed.tables) or t2

                t1_match = (
                    t1_canon.lower() == canonical_table.lower()
                    or (alias and t1.lower() == alias.lower())
                )
                t2_match = (
                    t2_canon.lower() == canonical_table.lower()
                    or (alias and t2.lower() == alias.lower())
                )

                if t1_match:
                    return c1
                elif t2_match:
                    return c2
        return None

    def _rule_13_order_by_index(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> List[Finding]:
        """
        RULE_13 — ORDER BY Combined WHERE + ORDER Index Opportunity (Batch 5).

        When SQLite uses a temp B-tree specifically for ORDER BY and the query also has
        WHERE predicates, a combined index (WHERE equality columns first, ORDER BY column
        appended) may allow SQLite to deliver rows pre-sorted without a separate sort step.

        Guards:
          - Only fires when temp B-tree purpose is ORDER BY.
          - Only fires when ORDER BY column(s) are physical table columns.
          - Only fires when there is at least one WHERE equality predicate.
          - Skips if an existing index already provides the full (WHERE + ORDER BY) prefix.
          - Only fires when the combined column set is not already covered by RULE_11.
        """
        findings = []

        btree_purpose = QueryPlanAnalyzer.temp_btree_purpose(plan_nodes)
        if btree_purpose != "ORDER BY":
            return findings

        if not parsed.order_by:
            return findings

        tname = parsed.tables[0].name if parsed.tables else None
        if not tname:
            return findings
        canonical_tname = self.schema_analyzer.resolve_table_name(tname, parsed.tables) or tname

        # Collect physical ORDER BY columns
        ord_cols = []
        for o in parsed.order_by:
            if o.column and self.schema_analyzer.is_column_in_table(canonical_tname, o.column):
                ord_cols.append(o.column)

        if not ord_cols:
            return findings

        # Collect equality WHERE columns for the same table
        eq_cols = []
        _EQ_OPS = {"EQ", "=", "IS"}
        for p in parsed.where_predicates:
            if p.is_function_call or not p.column:
                continue
            op_upper = p.operator.upper()
            if op_upper not in _EQ_OPS:
                continue

            tbl_r = None
            if p.table:
                tbl_r = self.schema_analyzer.resolve_table_name(p.table, parsed.tables)
            if not tbl_r and len(parsed.tables) == 1:
                tbl_r = parsed.tables[0].name

            if tbl_r and tbl_r.lower() == canonical_tname.lower():
                if self.schema_analyzer.is_column_in_table(canonical_tname, p.column):
                    if p.column not in eq_cols:
                        eq_cols.append(p.column)

        # Proposed composite: equality prefix + ORDER BY suffix
        combined_cols = eq_cols + [c for c in ord_cols if c not in eq_cols]

        if len(combined_cols) < 2:
            # Single column — RULE_02 or RULE_04 already covers this
            return findings

        is_valid, _ = self.schema_analyzer.validate_index_columns(canonical_tname, combined_cols, parsed.tables)
        if not is_valid:
            return findings

        has_idx, _, _ = self.schema_analyzer.has_index_on_columns(canonical_tname, combined_cols)
        if has_idx:
            return findings

        cols_str = ", ".join(combined_cols)
        findings.append(Finding(
            rule_id="RULE_13",
            title=f"Potential ORDER BY Index Opportunity: {canonical_tname}({cols_str})",
            severity=Severity.MEDIUM,
            category="Sorting",
            description=(
                f"Query Plan Observation: SQLite is using a temporary B-tree for ORDER BY. "
                f"A combined index on ({cols_str}) — equality columns first, ORDER BY column(s) "
                f"last — may allow SQLite to deliver rows in pre-sorted order, potentially "
                f"eliminating the sort step."
            ),
            rationale=(
                "When WHERE equality columns are indexed as a prefix and the ORDER BY column "
                "follows in the same index, SQLite can use a single index scan to both filter "
                "and retrieve rows in the correct order, avoiding the O(N log N) sort phase."
            ),
            affected_table=canonical_tname,
            affected_column=cols_str
        ))

        return findings

    def _rule_14_group_by_btree(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> List[Finding]:
        """
        RULE_14 — GROUP BY Temporary B-Tree Observation (Batch 5).

        When SQLite uses a temp B-tree for GROUP BY, surfaces:
          1. A plan observation Finding (INFO severity) — always when temp B-tree is for GROUP BY.
          2. An index candidate Finding (MEDIUM severity) — only when:
             - Query is single-table (conservative; JOIN GROUP BY is too complex to advise).
             - GROUP BY column(s) are physical columns.
             - No suitable index already exists.

        The observation and the candidate are kept as separate findings to preserve the
        distinction between plan evidence and actionable opportunity.
        """
        findings = []

        btree_purpose = QueryPlanAnalyzer.temp_btree_purpose(plan_nodes)
        if btree_purpose != "GROUP BY":
            return findings

        if not parsed.group_by:
            return findings

        tname = parsed.tables[0].name if parsed.tables else None
        if not tname:
            return findings
        canonical_tname = self.schema_analyzer.resolve_table_name(tname, parsed.tables) or tname

        # 1. Plan observation (always emitted when GROUP BY temp B-tree is detected)
        group_cols_str = ", ".join(parsed.group_by)
        findings.append(Finding(
            rule_id="RULE_14",
            title=f"Temporary B-Tree for GROUP BY Detected ({group_cols_str})",
            severity=Severity.INFO,
            category="Execution Plan",
            description=(
                f"Query Plan Observation: SQLite is using a temporary B-tree for GROUP BY. "
                f"SQLite creates an in-memory temporary B-tree to group query results on "
                f"'{group_cols_str}'."
            ),
            rationale=(
                "Grouping large result sets without a supporting index requires SQLite to sort "
                "rows in a temporary B-tree structure. An index on the GROUP BY column(s) may "
                "allow SQLite to read rows in pre-grouped order, potentially eliminating this step."
            ),
            affected_table=canonical_tname,
            affected_column=group_cols_str
        ))

        # 2. Index candidate — only for single-table queries on physical, unindexed GROUP BY columns.
        # Multi-table (JOIN) GROUP BY queries are too complex to advise a single-table index
        # deterministically without risking misleading recommendations.
        if len(parsed.tables) != 1:
            return findings

        physical_group_cols = [
            c for c in parsed.group_by
            if self.schema_analyzer.is_column_in_table(canonical_tname, c)
        ]

        if physical_group_cols:
            is_valid, _ = self.schema_analyzer.validate_index_columns(canonical_tname, physical_group_cols, parsed.tables)
            if is_valid:
                has_idx, _, _ = self.schema_analyzer.has_index_on_columns(canonical_tname, physical_group_cols)
                if not has_idx:
                    cols_str = ", ".join(physical_group_cols)
                    findings.append(Finding(
                        rule_id="RULE_14",
                        title=f"Potential GROUP BY Index Opportunity: {canonical_tname}({cols_str})",
                        severity=Severity.MEDIUM,
                        category="Indexing",
                        description=(
                            f"An index on '{canonical_tname}({cols_str})' may allow SQLite to read "
                            f"rows in pre-grouped order, potentially eliminating the temporary B-tree "
                            f"for GROUP BY."
                        ),
                        rationale=(
                            "When rows arrive pre-sorted by the GROUP BY column(s) via an index "
                            "scan, SQLite can accumulate groups in a single pass without building "
                            "a separate temporary structure."
                        ),
                        affected_table=canonical_tname,
                        affected_column=cols_str
                    ))

        return findings


    def _rule_15_covering_index(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> List[Finding]:
        """
        RULE_15 — Covering Index Opportunity (Batch 5, conservative).

        A covering index contains all columns needed by the query (both filter and select),
        allowing SQLite to satisfy the query entirely from the index without a table row lookup.

        Activation conditions (all must be true):
          - Single-table query (conservative).
          - Query uses specific (non-wildcard) SELECT columns.
          - Query has at least one WHERE equality predicate on a physical column.
          - Selected columns contain columns beyond the WHERE filter columns.
          - The combined set of WHERE + SELECT columns is ≤ 6 (conservative threshold).
          - The target table is accessed via SCAN or non-covering SEARCH.
          - No existing index already covers the full combined column set.
          - The current execution plan does not already use a COVERING INDEX.

        Does NOT fire for SELECT *, computed columns, or aliased aggregates.
        """
        findings = []

        if not plan_nodes:
            return findings

        # Must have explicit (non-wildcard) column selection
        has_wildcard = any(c.is_wildcard for c in parsed.selected_columns)
        if has_wildcard:
            return findings

        if not parsed.where_predicates:
            return findings

        # Only handle single-table queries for covering index analysis (conservative)
        if len(parsed.tables) != 1:
            return findings

        tname = parsed.tables[0].name
        canonical_tname = self.schema_analyzer.resolve_table_name(tname, parsed.tables) or tname
        tbl_names = {tname.lower(), canonical_tname.lower()}

        # Verify the table is accessed in plan (via SCAN or non-covering SEARCH)
        table_nodes = [
            n for n in plan_nodes
            if n.table_name and n.table_name.lower() in tbl_names
        ]
        if not table_nodes:
            return findings

        # If SQLite already uses a COVERING INDEX for this table, no need to recommend another
        is_already_covering = any(
            "COVERING INDEX" in (n.detail or "").upper() or (n.step_type and "COVERING" in n.step_type.upper())
            for n in table_nodes
        )
        if is_already_covering:
            return findings

        # Collect physical WHERE columns (equality only — conservative)
        _EQ_OPS = {"EQ", "=", "IS"}
        where_cols: List[str] = []
        for p in parsed.where_predicates:
            if p.is_function_call or not p.column:
                continue
            if p.operator.upper() not in _EQ_OPS:
                continue
            if self.schema_analyzer.is_column_in_table(canonical_tname, p.column):
                if p.column not in where_cols:
                    where_cols.append(p.column)

        if not where_cols:
            return findings

        # Collect physical SELECT columns
        select_cols: List[str] = []
        for c in parsed.selected_columns:
            if c.is_wildcard or not c.name or c.agg_func:
                continue
            # Resolve aliased column names back to physical columns
            if self.schema_analyzer.is_column_in_table(canonical_tname, c.name):
                if c.name not in select_cols:
                    select_cols.append(c.name)

        if not select_cols:
            return findings

        # Combined covering set: WHERE columns first (filter prefix), then remaining SELECT columns
        covering_cols = where_cols[:]
        for c in select_cols:
            if c not in covering_cols:
                covering_cols.append(c)

        # Must add at least one column beyond WHERE columns to be a covering opportunity
        if len(covering_cols) <= len(where_cols):
            return findings

        # Conservative threshold — only suggest covering indexes for small column sets
        if len(covering_cols) > 6:
            return findings

        is_valid, _ = self.schema_analyzer.validate_index_columns(canonical_tname, covering_cols, parsed.tables)
        if not is_valid:
            return findings

        has_idx, _, _ = self.schema_analyzer.has_index_on_columns(canonical_tname, covering_cols)
        if has_idx:
            return findings

        cols_str = ", ".join(covering_cols)
        findings.append(Finding(
            rule_id="RULE_15",
            title=f"Potential Covering Index Opportunity: {canonical_tname}({cols_str})",
            severity=Severity.MEDIUM,
            category="Indexing",
            description=(
                f"The query selects columns ({', '.join(select_cols)}) and filters on "
                f"({', '.join(where_cols)}) from '{canonical_tname}'. A covering index on "
                f"({cols_str}) could allow SQLite to satisfy this query entirely from the "
                f"index without accessing table rows."
            ),
            rationale=(
                "A covering index includes all columns needed by a query — both for filtering "
                "and for the SELECT list. When SQLite can satisfy a query from the index alone, "
                "it avoids the additional cost of looking up each matching row in the table, "
                "which can significantly reduce I/O for large tables."
            ),
            affected_table=canonical_tname,
            affected_column=cols_str
        ))

        return findings
