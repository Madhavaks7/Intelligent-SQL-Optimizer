from typing import List, Dict, Any, Optional
from models.analysis_models import (
    ParsedQuery, DatabaseSchema, PlanNode, TableRef, ColumnRef, Predicate, JoinRef
)
from analyzer.schema_analyzer import SchemaAnalyzer
from analyzer.query_plan import QueryPlanAnalyzer

class QueryAnalyzer:
    """
    Coordinates AST analysis, schema validation, and execution plan facts.
    Produces structured query metrics and flags basic syntax/structural facts.
    """

    def __init__(self, schema: DatabaseSchema):
        self.schema = schema
        self.schema_analyzer = SchemaAnalyzer(schema)

    def analyze_facts(self, parsed: ParsedQuery, plan_nodes: List[PlanNode]) -> Dict[str, Any]:
        """
        Extracts verified facts from query AST, database schema, and SQLite query plan.
        Batch 5: Extended with predicate classification, temp B-tree purpose, and
        expression-predicate tracking.
        """
        table_names = [t.name for t in parsed.tables]
        has_wildcard = any(c.is_wildcard for c in parsed.selected_columns)

        # Map filters to tables
        table_filters: Dict[str, List[Predicate]] = {}
        for tname in table_names:
            table_filters[tname] = []

        # Batch 5: Classify predicates into equality, range, expression categories
        equality_predicates: List[Predicate] = []
        range_predicates: List[Predicate] = []
        expression_predicates: List[Predicate] = []

        _RANGE_OPS = {"GT", "GTE", "LT", "LTE", "BETWEEN", ">", ">=", "<", "<="}
        _EQ_OPS = {"EQ", "=", "IS"}

        for p in parsed.where_predicates:
            matched_tbl = None
            if p.table:
                # Resolve alias or table
                for t in parsed.tables:
                    if (t.alias and t.alias.lower() == p.table.lower()) or (t.name.lower() == p.table.lower()):
                        matched_tbl = t.name
                        break

            if not matched_tbl and len(table_names) == 1:
                matched_tbl = table_names[0]

            if matched_tbl:
                if matched_tbl not in table_filters:
                    table_filters[matched_tbl] = []
                table_filters[matched_tbl].append(p)

            # Classify by operator / function usage
            if p.is_function_call:
                expression_predicates.append(p)
            elif p.operator.upper() in _EQ_OPS:
                equality_predicates.append(p)
            elif p.operator.upper() in _RANGE_OPS:
                range_predicates.append(p)

        # Identify full scans
        scan_tables = []
        for n in plan_nodes:
            if n.step_type == "SCAN" and n.table_name:
                scan_tables.append(n.table_name)

        has_temp_btree = any(n.step_type == "TEMP B-TREE" for n in plan_nodes)
        # Batch 5: identify what the temp B-tree is sorting for
        temp_btree_for = QueryPlanAnalyzer.temp_btree_purpose(plan_nodes)

        return {
            "table_names": table_names,
            "has_wildcard": has_wildcard,
            "filter_count": len(parsed.where_predicates),
            "table_filters": table_filters,
            "join_count": len(parsed.joins),
            "has_distinct": parsed.has_distinct,
            "has_group_by": len(parsed.group_by) > 0,
            "has_order_by": len(parsed.order_by) > 0,
            "has_limit": parsed.limit is not None,
            "scan_tables": list(set(scan_tables)),
            "has_temp_btree": has_temp_btree,
            "temp_btree_for": temp_btree_for,          # Batch 5: 'ORDER BY' | 'GROUP BY' | 'DISTINCT' | None
            "plan_node_count": len(plan_nodes),
            # Batch 5: structured predicate breakdown
            "equality_predicates": equality_predicates,
            "range_predicates": range_predicates,
            "expression_predicates": expression_predicates,
        }
