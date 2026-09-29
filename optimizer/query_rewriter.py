import re
from typing import List, Optional, Tuple
from models.analysis_models import (
    Finding, Recommendation, ParsedQuery, DatabaseSchema
)
from analyzer.schema_analyzer import SchemaAnalyzer


class QueryRewriter:
    """
    Generates DDL recommendations (CREATE INDEX) and safe query transformations.
    Applies strict schema validation on proposed index columns and classifies
    optimization strategies.

    Batch 5.1: Consolidated candidate architecture.
    Ensures that identical physical candidates and generic vs query-specific
    overlapping candidates are consolidated into one authoritative Recommendation.

    Strategy values:
      COMPOSITE_INDEX       — multi-column WHERE predicate composite index (RULE_11 / RULE_03)
      ORDER_BY_INDEX        — combined WHERE + ORDER BY index (RULE_13)
      JOIN_INDEX            — join column index (RULE_12 / RULE_05)
      COVERING_INDEX        — full covering index for filter + select (RULE_15)
      INDEX_BASED           — single-column index or unclassified DDL (RULE_02, RULE_04, RULE_14)
      QUERY_REWRITE         — SQL rewrite recommendation
      INFORMATIONAL_ADVISORY — findings with no executable DDL
      NO_OPTIMIZATION_REQUIRED — no findings
    """

    DISCLAIMER = "-- Suggested query — verify semantic equivalence before production use.\n"

    # Strategy priority — higher index = higher precedence
    _STRATEGY_PRIORITY = {
        "NO_OPTIMIZATION_REQUIRED": 0,
        "INFORMATIONAL_ADVISORY": 1,
        "QUERY_REWRITE": 2,
        "INDEX_BASED": 3,
        "JOIN_INDEX": 4,
        "COVERING_INDEX": 5,
        "ORDER_BY_INDEX": 6,
        "COMPOSITE_INDEX": 7,
    }

    @staticmethod
    def generate_recommendations(
        findings: List[Finding], parsed: ParsedQuery, schema: DatabaseSchema
    ) -> Tuple[List[Recommendation], Optional[str], str, List[Finding]]:
        advisory_recs: List[Recommendation] = []
        raw_candidates: List[dict] = []
        schema_analyzer = SchemaAnalyzer(schema)

        for f in findings:
            # ----------------------------------------------------------------
            # Existing rules (RULE_01 – RULE_10)
            # ----------------------------------------------------------------
            if f.rule_id == "RULE_01":
                advisory_recs.append(Recommendation(
                    id="REC_01",
                    type="ADVISORY",
                    description="Selecting only required columns can reduce data transfer and memory usage when the application does not need every column.",
                    expected_impact="Informational",
                    source_rules=["RULE_01"]
                ))

            elif f.rule_id in ("RULE_02", "RULE_03", "RULE_04") and f.affected_table and f.affected_column:
                cols = [c.strip() for c in f.affected_column.split(",")]
                is_valid, canonical_tname = schema_analyzer.validate_index_columns(
                    f.affected_table, cols, parsed.tables
                )

                if is_valid and canonical_tname:
                    has_idx, _, _ = schema_analyzer.has_index_on_columns(canonical_tname, cols)
                    if not has_idx:
                        cols_joined = "_".join([c.lower() for c in cols])
                        cols_formatted = ", ".join(cols)
                        idx_name = f"idx_{canonical_tname.lower()}_{cols_joined}"
                        ddl = f"CREATE INDEX IF NOT EXISTS {idx_name}\nON {canonical_tname}({cols_formatted});"

                        impact = "Medium" if f.rule_id == "RULE_02" else ("High" if f.rule_id == "RULE_03" else "Medium")
                        strategy = "COMPOSITE_INDEX" if f.rule_id == "RULE_03" else "INDEX_BASED"

                        if f.rule_id == "RULE_02":
                            desc = f"Create index on '{canonical_tname}({cols_formatted})' to improve filtering of rows by {cols_formatted}."
                        elif f.rule_id == "RULE_03":
                            desc = f"Create composite index on '{canonical_tname}({cols_formatted})' to improve multi-column filtering."
                        else:
                            desc = f"Create sorting index on '{canonical_tname}({cols_formatted})' to eliminate temporary B-tree sorting."

                        raw_candidates.append({
                            "id": f"REC_{f.rule_id.split('_')[1]}",
                            "table": canonical_tname,
                            "columns": cols,
                            "ddl": ddl,
                            "strategy": strategy,
                            "source_rules": [f.rule_id],
                            "description": desc,
                            "impact": impact,
                            "rule_id": f.rule_id,
                            "finding": f
                        })
                else:
                    # Column is computed/expression/alias — emit Advisory instead of invalid DDL
                    advisory_recs.append(Recommendation(
                        id=f"REC_{f.rule_id.split('_')[1]}",
                        type="ADVISORY",
                        description=f"Sorting/filtering occurs on a computed aggregate/expression ('{f.affected_column}'). Physical database indexes cannot be created on non-physical expressions.",
                        expected_impact="Informational",
                        source_rules=[f.rule_id]
                    ))

            elif f.rule_id == "RULE_05" and f.affected_table:
                canonical_r = schema_analyzer.resolve_table_name(f.affected_table, parsed.tables) or f.affected_table
                join_col = None
                for j in parsed.joins:
                    j_table = schema_analyzer.resolve_table_name(j.right_table, parsed.tables) or j.right_table
                    if j_table.lower() == canonical_r.lower() or (j.right_alias and j.right_alias.lower() == f.affected_table.lower()):
                        for cond in j.on_conditions:
                            m = re.search(r"(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)", cond)
                            if m:
                                t1, c1, t2, c2 = m.group(1), m.group(2), m.group(3), m.group(4)
                                t1_canon = schema_analyzer.resolve_table_name(t1, parsed.tables) or t1
                                t2_canon = schema_analyzer.resolve_table_name(t2, parsed.tables) or t2

                                if t1_canon.lower() == canonical_r.lower() or (j.right_alias and t1.lower() == j.right_alias.lower()):
                                    join_col = c1
                                elif t2_canon.lower() == canonical_r.lower() or (j.right_alias and t2.lower() == j.right_alias.lower()):
                                    join_col = c2

                if not join_col:
                    join_col = f"{canonical_r.lower()}_id"

                # Validate column exists
                if schema_analyzer.is_column_in_table(canonical_r, join_col):
                    has_idx, _, _ = schema_analyzer.has_index_on_columns(canonical_r, [join_col])
                    if not has_idx:
                        idx_name = f"idx_{canonical_r.lower()}_{join_col.lower()}"
                        ddl = f"CREATE INDEX IF NOT EXISTS {idx_name}\nON {canonical_r}({join_col});"
                        raw_candidates.append({
                            "id": "REC_05",
                            "table": canonical_r,
                            "columns": [join_col],
                            "ddl": ddl,
                            "strategy": "JOIN_INDEX",
                            "source_rules": ["RULE_05"],
                            "description": f"Create join index on '{canonical_r}({join_col})'.",
                            "impact": "High",
                            "rule_id": "RULE_05",
                            "finding": f
                        })
                else:
                    advisory_recs.append(Recommendation(
                        id="REC_05",
                        type="ADVISORY",
                        description=f"Unindexed JOIN operation on table '{canonical_r}'.",
                        expected_impact="High",
                        source_rules=["RULE_05"]
                    ))

            elif f.rule_id == "RULE_06":
                # Expression predicates — advisory only, never DDL
                advisory_recs.append(Recommendation(
                    id="REC_06",
                    type="ADVISORY",
                    description=(
                        f"Column '{f.affected_column}' is wrapped in a function expression. "
                        "A standard index on this column may not directly support the predicate. "
                        "Consider rewriting the predicate to avoid the function wrapper, or use "
                        "a SQLite expression index if supported."
                    ),
                    expected_impact="Medium",
                    source_rules=["RULE_06"]
                ))

            elif f.rule_id == "RULE_08":
                advisory_recs.append(Recommendation(
                    id="REC_08",
                    type="ADVISORY",
                    description="Consider refactoring leading wildcard LIKE to prefix search or SQLite Full-Text Search (FTS5).",
                    expected_impact="Medium",
                    source_rules=["RULE_08"]
                ))

            elif f.rule_id == "RULE_09":
                advisory_recs.append(Recommendation(
                    id="REC_09",
                    type="ADVISORY",
                    description="Consider adding pagination or a LIMIT clause depending on application requirements.",
                    expected_impact="Informational",
                    source_rules=["RULE_09"]
                ))

            # ----------------------------------------------------------------
            # Batch 5 rules (RULE_11 – RULE_15)
            # ----------------------------------------------------------------

            elif f.rule_id == "RULE_11" and f.affected_table and f.affected_column:
                # Composite index with column ordering heuristic
                cols = [c.strip() for c in f.affected_column.split(",")]
                is_valid, canonical_tname = schema_analyzer.validate_index_columns(
                    f.affected_table, cols, parsed.tables
                )
                if is_valid and canonical_tname:
                    has_idx, _, _ = schema_analyzer.has_index_on_columns(canonical_tname, cols)
                    if not has_idx:
                        cols_joined = "_".join([c.lower() for c in cols])
                        cols_formatted = ", ".join(cols)
                        idx_name = f"idx_{canonical_tname.lower()}_{cols_joined}"
                        ddl = f"CREATE INDEX IF NOT EXISTS {idx_name}\nON {canonical_tname}({cols_formatted});"
                        raw_candidates.append({
                            "id": "REC_11",
                            "table": canonical_tname,
                            "columns": cols,
                            "ddl": ddl,
                            "strategy": "COMPOSITE_INDEX",
                            "source_rules": ["RULE_11"],
                            "description": (
                                f"Potential Composite Index Opportunity: Create composite index on "
                                f"'{canonical_tname}({cols_formatted})' to optimise multi-predicate "
                                f"filtering. Equality columns are placed first for maximum B-tree prefix selectivity."
                            ),
                            "impact": "High",
                            "rule_id": "RULE_11",
                            "finding": f
                        })

            elif f.rule_id == "RULE_12" and f.affected_table and f.affected_column:
                # JOIN column index
                join_col = f.affected_column.strip()
                canonical_r = schema_analyzer.resolve_table_name(f.affected_table, parsed.tables) or f.affected_table
                if schema_analyzer.is_column_in_table(canonical_r, join_col):
                    has_idx, _, _ = schema_analyzer.has_index_on_columns(canonical_r, [join_col])
                    if not has_idx:
                        idx_name = f"idx_{canonical_r.lower()}_{join_col.lower()}"
                        ddl = f"CREATE INDEX IF NOT EXISTS {idx_name}\nON {canonical_r}({join_col});"
                        raw_candidates.append({
                            "id": "REC_12",
                            "table": canonical_r,
                            "columns": [join_col],
                            "ddl": ddl,
                            "strategy": "JOIN_INDEX",
                            "source_rules": ["RULE_12"],
                            "description": (
                                f"Potential JOIN Index Opportunity: Create index on "
                                f"'{canonical_r}({join_col})' to speed up the JOIN lookup."
                            ),
                            "impact": "High",
                            "rule_id": "RULE_12",
                            "finding": f
                        })

            elif f.rule_id == "RULE_13" and f.affected_table and f.affected_column:
                # ORDER BY combined index
                cols = [c.strip() for c in f.affected_column.split(",")]
                is_valid, canonical_tname = schema_analyzer.validate_index_columns(
                    f.affected_table, cols, parsed.tables
                )
                if is_valid and canonical_tname:
                    has_idx, _, _ = schema_analyzer.has_index_on_columns(canonical_tname, cols)
                    if not has_idx:
                        cols_joined = "_".join([c.lower() for c in cols])
                        cols_formatted = ", ".join(cols)
                        idx_name = f"idx_{canonical_tname.lower()}_{cols_joined}"
                        ddl = f"CREATE INDEX IF NOT EXISTS {idx_name}\nON {canonical_tname}({cols_formatted});"
                        raw_candidates.append({
                            "id": "REC_13",
                            "table": canonical_tname,
                            "columns": cols,
                            "ddl": ddl,
                            "strategy": "ORDER_BY_INDEX",
                            "source_rules": ["RULE_13"],
                            "description": (
                                f"Potential ORDER BY Index Opportunity: Create combined index on "
                                f"'{canonical_tname}({cols_formatted})' to potentially eliminate "
                                f"the temporary B-tree sort for ORDER BY."
                            ),
                            "impact": "Medium",
                            "rule_id": "RULE_13",
                            "finding": f
                        })

            elif f.rule_id == "RULE_14":
                if f.category == "Execution Plan":
                    # Plan observation — informational only
                    advisory_recs.append(Recommendation(
                        id="REC_14_OBS",
                        type="ADVISORY",
                        description=(
                            f"Query Plan Observation: SQLite is using a temporary B-tree for GROUP BY "
                            f"on column(s) '{f.affected_column}'. This is a plan evidence observation, "
                            f"not a guaranteed performance issue."
                        ),
                        expected_impact="Informational",
                        source_rules=["RULE_14"]
                    ))
                elif f.category == "Indexing" and f.affected_table and f.affected_column:
                    # Index candidate for GROUP BY
                    cols = [c.strip() for c in f.affected_column.split(",")]
                    is_valid, canonical_tname = schema_analyzer.validate_index_columns(
                        f.affected_table, cols, parsed.tables
                    )
                    if is_valid and canonical_tname:
                        has_idx, _, _ = schema_analyzer.has_index_on_columns(canonical_tname, cols)
                        if not has_idx:
                            cols_joined = "_".join([c.lower() for c in cols])
                            cols_formatted = ", ".join(cols)
                            idx_name = f"idx_{canonical_tname.lower()}_{cols_joined}"
                            ddl = f"CREATE INDEX IF NOT EXISTS {idx_name}\nON {canonical_tname}({cols_formatted});"
                            raw_candidates.append({
                                "id": "REC_14",
                                "table": canonical_tname,
                                "columns": cols,
                                "ddl": ddl,
                                "strategy": "INDEX_BASED",
                                "source_rules": ["RULE_14"],
                                "description": (
                                    f"Potential GROUP BY Index Opportunity: An index on "
                                    f"'{canonical_tname}({cols_formatted})' may allow SQLite to read "
                                    f"rows in pre-grouped order, potentially eliminating the temporary B-tree."
                                ),
                                "impact": "Medium",
                                "rule_id": "RULE_14",
                                "finding": f
                            })

            elif f.rule_id == "RULE_15" and f.affected_table and f.affected_column:
                # Covering index
                cols = [c.strip() for c in f.affected_column.split(",")]
                is_valid, canonical_tname = schema_analyzer.validate_index_columns(
                    f.affected_table, cols, parsed.tables
                )
                if is_valid and canonical_tname:
                    has_idx, _, _ = schema_analyzer.has_index_on_columns(canonical_tname, cols)
                    if not has_idx:
                        cols_joined = "_".join([c.lower() for c in cols])
                        cols_formatted = ", ".join(cols)
                        idx_name = f"idx_{canonical_tname.lower()}_{cols_joined}"
                        ddl = f"CREATE INDEX IF NOT EXISTS {idx_name}\nON {canonical_tname}({cols_formatted});"
                        raw_candidates.append({
                            "id": "REC_15",
                            "table": canonical_tname,
                            "columns": cols,
                            "ddl": ddl,
                            "strategy": "COVERING_INDEX",
                            "source_rules": ["RULE_15"],
                            "description": (
                                f"Potential Covering Index Opportunity: An index on "
                                f"'{canonical_tname}({cols_formatted})' includes both the filter columns "
                                f"and the selected columns, potentially allowing SQLite to satisfy the "
                                f"query entirely from the index without table row lookups."
                            ),
                            "impact": "Medium",
                            "rule_id": "RULE_15",
                            "finding": f
                        })

        # ----------------------------------------------------------------
        # Consolidation Phase (Batch 5.1)
        # ----------------------------------------------------------------
        consolidated_candidates, suppressed_finding_ids = QueryRewriter._consolidate_candidates(raw_candidates)

        # Build final recommendation list
        recommendations: List[Recommendation] = []
        for c in consolidated_candidates:
            recommendations.append(Recommendation(
                id=c["id"],
                type="CREATE INDEX",
                description=c["description"],
                ddl=c["ddl"],
                expected_impact=c["impact"],
                table=c["table"],
                columns=c["columns"],
                strategy=c["strategy"],
                source_rules=c["source_rules"]
            ))

        # Append advisories
        recommendations.extend(advisory_recs)

        # Consolidate findings (omit duplicate/suppressed finding cards)
        consolidated_findings = [f for f in findings if id(f) not in suppressed_finding_ids]

        # ----------------------------------------------------------------
        # Classify Optimization Strategy
        # ----------------------------------------------------------------
        has_ddl = any(r.ddl for r in recommendations)
        rewrite_rec = next((r for r in recommendations if r.type == "QUERY REWRITE" and r.suggested_query), None)

        if has_ddl:
            # Set strategy from primary executable candidate
            strategy = QueryRewriter._classify_consolidated_strategy(consolidated_candidates)
            suggested_query = None
        elif rewrite_rec:
            strategy = "QUERY_REWRITE"
            suggested_query = rewrite_rec.suggested_query
        elif not findings:
            strategy = "NO_OPTIMIZATION_REQUIRED"
            suggested_query = None
        else:
            strategy = "INFORMATIONAL_ADVISORY"
            suggested_query = None

        return recommendations, suggested_query, strategy, consolidated_findings

    @staticmethod
    def _consolidate_candidates(raw_candidates: List[dict]) -> Tuple[List[dict], set]:
        """
        Consolidates raw DDL candidates:
          1. Exact signature deduplication (same table and same columns in same order).
             Prefers Batch 5 rules (RULE_11 over RULE_03, RULE_12 over RULE_05, etc.).
          2. Specific vs Generic supersession (RULE_13 WHERE+ORDER BY index supersedes generic RULE_04 on same table;
             RULE_11 composite index supersedes single-column RULE_02 on same table).
        Returns (consolidated_candidates, suppressed_finding_ids).
        """
        if not raw_candidates:
            return [], set()

        # Step 1: Deduplicate exact signatures: (table.lower(), tuple(col.lower() for col in columns))
        signature_groups: dict = {}
        for c in raw_candidates:
            sig = (c["table"].lower(), tuple(col.lower() for col in c["columns"]))
            signature_groups.setdefault(sig, []).append(c)

        exact_deduped: List[dict] = []
        suppressed_finding_ids: set = set()

        for sig, group in signature_groups.items():
            if len(group) == 1:
                exact_deduped.append(group[0])
            else:
                # Prioritize rule representing the best candidate description / strategy
                rule_priority = {"RULE_11": 10, "RULE_12": 9, "RULE_13": 8, "RULE_15": 7, "RULE_14": 6, "RULE_03": 5, "RULE_05": 4, "RULE_02": 3, "RULE_04": 2}
                sorted_group = sorted(group, key=lambda x: rule_priority.get(x["rule_id"], 0), reverse=True)
                primary = dict(sorted_group[0])

                # Merge source rules
                all_sources = []
                for item in sorted_group:
                    for src in item["source_rules"]:
                        if src not in all_sources:
                            all_sources.append(src)
                primary["source_rules"] = all_sources

                # Suppress redundant findings from secondary candidates
                for secondary in sorted_group[1:]:
                    if secondary.get("finding"):
                        suppressed_finding_ids.add(id(secondary["finding"]))

                exact_deduped.append(primary)

        # Step 2: Specific vs Generic candidate supersession on the same table
        final_candidates: List[dict] = []
        candidates_by_table: dict = {}
        for c in exact_deduped:
            candidates_by_table.setdefault(c["table"].lower(), []).append(c)

        for tbl, t_cands in candidates_by_table.items():
            if len(t_cands) == 1:
                final_candidates.append(t_cands[0])
                continue

            # Check if RULE_13 (ORDER_BY_INDEX) is present alongside generic RULE_04 (INDEX_BASED sort index)
            r13_cand = next((c for c in t_cands if c["strategy"] == "ORDER_BY_INDEX"), None)
            # Check if RULE_11 (COMPOSITE_INDEX) is present alongside single-column RULE_02
            r11_cand = next((c for c in t_cands if c["strategy"] == "COMPOSITE_INDEX"), None)

            kept_for_table: List[dict] = []
            for cand in t_cands:
                # If RULE_13 exists and cand is a generic single-column RULE_04 sort index on a subset of the ORDER BY columns
                if r13_cand and cand != r13_cand and "RULE_04" in cand["source_rules"]:
                    # Generic sort index is superseded by combined WHERE+ORDER BY index
                    if "RULE_04" not in r13_cand["source_rules"]:
                        r13_cand["source_rules"].append("RULE_04")
                    if cand.get("finding"):
                        suppressed_finding_ids.add(id(cand["finding"]))
                    continue

                # If RULE_11 exists and cand is a single-column filter index on one of the composite columns
                if r11_cand and cand != r11_cand and "RULE_02" in cand["source_rules"]:
                    cand_cols_lower = [col.lower() for col in cand["columns"]]
                    r11_cols_lower = [col.lower() for col in r11_cand["columns"]]
                    if all(col in r11_cols_lower for col in cand_cols_lower):
                        if "RULE_02" not in r11_cand["source_rules"]:
                            r11_cand["source_rules"].append("RULE_02")
                        if cand.get("finding"):
                            suppressed_finding_ids.add(id(cand["finding"]))
                        continue

                kept_for_table.append(cand)

            final_candidates.extend(kept_for_table)

        return final_candidates, suppressed_finding_ids

    @staticmethod
    def _classify_consolidated_strategy(consolidated_candidates: List[dict]) -> str:
        """Determines the strategy from the active consolidated candidates."""
        if not consolidated_candidates:
            return "INDEX_BASED"
        
        strategies = [c["strategy"] for c in consolidated_candidates]
        if "COMPOSITE_INDEX" in strategies:
            return "COMPOSITE_INDEX"
        if "ORDER_BY_INDEX" in strategies:
            return "ORDER_BY_INDEX"
        if "JOIN_INDEX" in strategies:
            return "JOIN_INDEX"
        if "COVERING_INDEX" in strategies and not any(s in strategies for s in ("INDEX_BASED",)):
            return "COVERING_INDEX"
        if "INDEX_BASED" in strategies:
            return "INDEX_BASED"
        if "COVERING_INDEX" in strategies:
            return "COVERING_INDEX"
        return strategies[0] if strategies else "INDEX_BASED"

    @staticmethod
    def _rewrite_select_star(parsed: ParsedQuery, schema: DatabaseSchema) -> str:
        """Expands SELECT * into explicit column list using table schema."""
        sql = parsed.raw_sql.strip()
        if not parsed.tables:
            return sql

        tname = parsed.tables[0].name
        tschema = schema.tables.get(tname)
        if not tschema:
            return sql

        col_names = [c.name for c in tschema.columns]
        cols_str = ",\n    ".join(col_names)

        rewritten = re.sub(r"SELECT\s+\*\s+", f"SELECT\n    {cols_str}\n", sql, flags=re.IGNORECASE)
        return QueryRewriter.DISCLAIMER + rewritten

