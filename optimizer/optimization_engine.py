from typing import List, Optional
from models.analysis_models import (
    AnalysisResult, DatabaseSchema, ParsedQuery, PlanNode, Finding, Recommendation, BenchmarkResult
)
from database.database import DatabaseManager
from database.schema import SchemaInspector
from parser.sql_parser import SQLParser
from analyzer.query_plan import QueryPlanAnalyzer
from analyzer.query_analyzer import QueryAnalyzer
from optimizer.rules import RuleEngine
from optimizer.query_rewriter import QueryRewriter
from performance.benchmark import PerformanceBenchmarker

class OptimizationEngine:
    """
    Master Orchestrator for the Intelligent SQL Query Optimization System.
    Connects SQL parsing, schema inspection, query plan analysis, deterministic rules,
    recommendations, and benchmark execution.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
        self.schema_inspector = SchemaInspector(db_manager)
        self._cached_schema: Optional[DatabaseSchema] = None

    def get_schema(self, force_refresh: bool = False) -> DatabaseSchema:
        """Returns introspected database schema."""
        if self._cached_schema is None or force_refresh:
            self._cached_schema = self.schema_inspector.inspect_database()
        return self._cached_schema

    def analyze_query(self, query: str, run_benchmark: bool = False) -> AnalysisResult:
        """
        Executes end-to-end query optimization analysis pipeline.
        """

        schema = self.get_schema()

        # 1. Parse SQL AST
        parsed = SQLParser.parse_query(query)
        if parsed.parse_error or not parsed.is_select:
            return AnalysisResult(
                query=query,
                parsed_query=parsed,
                schema=schema,
                plan_nodes=[],
                findings=[],
                recommendations=[]
            )

        # 2. Extract SQLite EXPLAIN QUERY PLAN
        try:
            explain_rows = self.db_manager.explain_query_plan(query)
            plan_nodes = QueryPlanAnalyzer.parse_explain_output(explain_rows)
        except Exception as e:
            parsed.parse_error = f"Database Execution Error: {str(e)}"
            return AnalysisResult(
                query=query,
                parsed_query=parsed,
                schema=schema,
                plan_nodes=[],
                findings=[],
                recommendations=[]
            )

        # 3. Evaluate Deterministic Optimization Rules
        rule_engine = RuleEngine(schema)
        findings = rule_engine.evaluate_all_rules(parsed, plan_nodes)

        # 4. Generate Recommendations, Query Transformations & Consolidate Findings
        recommendations, suggested_query, strategy, consolidated_findings = QueryRewriter.generate_recommendations(findings, parsed, schema)

        # 5. Run Benchmarking (if requested and safe)
        benchmark_res = None
        if run_benchmark and parsed.is_select:
            benchmarker = PerformanceBenchmarker(self.db_manager)
            benchmark_res = benchmarker.run_benchmark(
                query=query,
                recommendations=recommendations,
                suggested_query=suggested_query
            )

        return AnalysisResult(
            query=query,
            parsed_query=parsed,
            schema=schema,
            plan_nodes=plan_nodes,
            findings=findings,
            recommendations=recommendations,
            suggested_query=suggested_query,
            optimization_strategy=strategy,
            benchmark_result=benchmark_res
        )

