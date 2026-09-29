import time
import re
import statistics
from typing import List, Optional
from models.analysis_models import (
    BenchmarkResult, BenchmarkMetrics, Recommendation, PlanNode, EquivalenceResult
)
from database.database import DatabaseManager
from analyzer.query_plan import QueryPlanAnalyzer
from analyzer.result_validator import ResultValidator

class PerformanceBenchmarker:
    """
    Executes actual before-and-after query benchmarking in an isolated temporary database copy.
    Ensures safe performance comparison without modifying the primary demo database.
    Employs robust multi-run benchmarking with separate warm-up and measured runs.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def run_benchmark(
        self,
        query: str,
        recommendations: List[Recommendation],
        suggested_query: Optional[str] = None,
        warmup_runs: int = 3,
        measured_runs: int = 7,
        tolerance_percent: float = 5.0,
        iterations: Optional[int] = None
    ) -> BenchmarkResult:
        """
        Runs before & after multi-run performance benchmarking on an isolated database copy.
        - warmup_runs: Number of initial runs to warm engine/cache (excluded from statistics).
        - measured_runs: Number of measured runs to compute median, min, max.
        - tolerance_percent: Percentage variance threshold for classification (default ±5.0%).
        """
        if iterations is not None:
            measured_runs = iterations

        # 1. Verify Query Safety
        is_safe, err_msg = DatabaseManager.is_safe_read_query(query)
        if not is_safe:
            return BenchmarkResult(
                original_metrics=BenchmarkMetrics(exec_time_ms=0.0, rows_returned=0, plan_nodes=[], plan_summary="Unsafe query."),
                benchmark_applied=False,
                note=err_msg or "Query safety check failed."
            )

        # Extract executable DDLs from recommendations
        ddl_statements = [r.ddl for r in recommendations if r.ddl]

        # Extract candidate metadata from primary executable recommendation
        primary_rec = next((r for r in recommendations if r.type == "CREATE INDEX" and r.ddl), None)
        cand_name = None
        cand_strategy = None
        cand_ddl = None
        if primary_rec:
            cand_ddl = primary_rec.ddl
            cand_strategy = primary_rec.strategy or "INDEX_BASED"
            if primary_rec.table and primary_rec.columns:
                cand_name = f"{primary_rec.table}({', '.join(primary_rec.columns)})"
            else:
                m = re.search(r"ON\s+(\w+)\s*\(([^)]+)\)", primary_rec.ddl, re.IGNORECASE)
                if m:
                    cand_name = f"{m.group(1)}({m.group(2).strip()})"
                else:
                    cand_name = primary_rec.description

        # Create isolated temporary benchmark database
        temp_db_path, temp_mgr = self.db_manager.create_benchmark_copy()

        try:
            # 2. Benchmark ORIGINAL Query
            orig_plan_rows = temp_mgr.explain_query_plan(query)
            orig_nodes = QueryPlanAnalyzer.parse_explain_output(orig_plan_rows)
            orig_summary = QueryPlanAnalyzer.format_plan_summary(orig_nodes)

            # Warm-up runs (excluded from statistics)
            orig_warmup_times: List[float] = []
            orig_rows = []
            orig_cols = []
            
            w_runs = max(0, warmup_runs)
            for _ in range(w_runs):
                orig_rows, orig_cols, t_ms = temp_mgr.execute_query_with_columns(query)
                orig_warmup_times.append(t_ms)

            # If warmup_runs == 0, fetch rows and columns once
            if w_runs == 0:
                orig_rows, orig_cols, _ = temp_mgr.execute_query_with_columns(query)

            # Measured iterations
            orig_times: List[float] = []
            m_runs = max(1, measured_runs)
            for _ in range(m_runs):
                _, t_ms = temp_mgr.execute_query(query)
                orig_times.append(t_ms)

            orig_median = round(float(statistics.median(orig_times)), 2)
            orig_min = round(float(min(orig_times)), 2)
            orig_max = round(float(max(orig_times)), 2)
            row_count = len(orig_rows)

            orig_metrics = BenchmarkMetrics(
                exec_time_ms=orig_median,
                rows_returned=row_count,
                plan_nodes=orig_nodes,
                plan_summary=orig_summary,
                min_time_ms=orig_min,
                max_time_ms=orig_max,
                median_time_ms=orig_median,
                raw_times_ms=[round(t, 2) for t in orig_times],
                warmup_times_ms=[round(t, 2) for t in orig_warmup_times]
            )

            if not ddl_statements:
                return BenchmarkResult(
                    original_metrics=orig_metrics,
                    optimized_metrics=None,
                    benchmark_applied=False,
                    note="No database indexes were required to be created for this query.",
                    candidate_name=cand_name,
                    strategy=cand_strategy,
                    candidate_ddl=cand_ddl,
                    warmup_runs=warmup_runs,
                    measured_runs=measured_runs,
                    original_times_ms=[round(t, 2) for t in orig_times],
                    original_warmup_times_ms=[round(t, 2) for t in orig_warmup_times],
                    original_median_ms=orig_median,
                    original_min_ms=orig_min,
                    original_max_ms=orig_max,
                    performance_classification="NO_MEASURABLE_IMPROVEMENT"
                )

            # 3. Apply DDL Index Changes to Temp DB
            created_indexes = []
            with temp_mgr.get_connection() as conn:
                cursor = conn.cursor()
                for ddl in ddl_statements:
                    try:
                        cursor.execute(ddl)
                        created_indexes.append(ddl.split("\n")[0])
                    except Exception as e:
                        print(f"Benchmark DDL execution warning: {e}")
                    conn.commit()

            if not created_indexes:
                return BenchmarkResult(
                    original_metrics=orig_metrics,
                    optimized_metrics=None,
                    benchmark_applied=False,
                    note="No valid database indexes were applied for benchmarking.",
                    candidate_name=cand_name,
                    strategy=cand_strategy,
                    candidate_ddl=cand_ddl,
                    warmup_runs=warmup_runs,
                    measured_runs=measured_runs,
                    original_times_ms=[round(t, 2) for t in orig_times],
                    original_warmup_times_ms=[round(t, 2) for t in orig_warmup_times],
                    original_median_ms=round(orig_median, 2),
                    original_min_ms=round(orig_min, 2),
                    original_max_ms=round(orig_max, 2),
                    performance_classification="NO_MEASURABLE_IMPROVEMENT"
                )

            # Target query for post-optimization benchmark
            target_query = query
            clean_suggested = suggested_query.replace("-- Suggested query — verify semantic equivalence before production use.\n", "").strip() if suggested_query else None
            if clean_suggested and clean_suggested.upper().startswith("SELECT"):
                # Use suggested query if valid SELECT
                target_query = clean_suggested

            # 4. Benchmark OPTIMIZED Query
            opt_plan_rows = temp_mgr.explain_query_plan(target_query)
            opt_nodes = QueryPlanAnalyzer.parse_explain_output(opt_plan_rows)
            opt_summary = QueryPlanAnalyzer.format_plan_summary(opt_nodes)

            # Check if SQLite query planner actually selected the candidate index
            orig_scan_count = sum(1 for n in orig_nodes if n.step_type == "SCAN")
            opt_scan_count = sum(1 for n in opt_nodes if n.step_type == "SCAN")
            orig_search_count = sum(1 for n in orig_nodes if n.step_type == "SEARCH")
            opt_search_count = sum(1 for n in opt_nodes if n.step_type == "SEARCH")

            # Extract created index names to verify if engine selected any of them
            created_idx_names = []
            for ddl in created_indexes:
                m = re.search(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", ddl, re.IGNORECASE)
                if m:
                    created_idx_names.append(m.group(1).lower())

            index_in_opt_plan = any(
                any(idx_name in (n.detail or "").lower() or (n.index_name and idx_name == n.index_name.lower()) for idx_name in created_idx_names)
                for n in opt_nodes
            )

            planner_used_index = index_in_opt_plan or (opt_scan_count < orig_scan_count) or (opt_search_count > orig_search_count)

            # Warm-up runs (excluded from statistics)
            opt_warmup_times: List[float] = []
            opt_rows = []
            opt_cols = []
            
            for _ in range(w_runs):
                opt_rows, opt_cols, t_ms = temp_mgr.execute_query_with_columns(target_query)
                opt_warmup_times.append(t_ms)

            # If warmup_runs == 0, fetch rows and columns once
            if w_runs == 0:
                opt_rows, opt_cols, _ = temp_mgr.execute_query_with_columns(target_query)

            # Measured iterations
            opt_times: List[float] = []
            for _ in range(m_runs):
                _, t_ms = temp_mgr.execute_query(target_query)
                opt_times.append(t_ms)

            opt_median = round(float(statistics.median(opt_times)), 2)
            opt_min = round(float(min(opt_times)), 2)
            opt_max = round(float(max(opt_times)), 2)

            opt_metrics = BenchmarkMetrics(
                exec_time_ms=opt_median,
                rows_returned=len(opt_rows),
                plan_nodes=opt_nodes,
                plan_summary=opt_summary,
                min_time_ms=opt_min,
                max_time_ms=opt_max,
                median_time_ms=opt_median,
                raw_times_ms=[round(t, 2) for t in opt_times],
                warmup_times_ms=[round(t, 2) for t in opt_warmup_times]
            )

            # 5. Result-Equivalence Validation (Batch 2 prerequisite)
            has_order_by = bool(re.search(r"\bORDER\s+BY\b", query, re.IGNORECASE))
            eq_res = ResultValidator.compare_results(
                orig_columns=orig_cols,
                orig_rows=orig_rows,
                opt_columns=opt_cols,
                opt_rows=opt_rows,
                has_order_by=has_order_by
            )

            # Determine plan change status
            orig_steps = [n.step_type for n in orig_nodes if n.step_type in ("SCAN", "SEARCH")]
            opt_steps = [n.step_type for n in opt_nodes if n.step_type in ("SCAN", "SEARCH")]
            
            orig_has_btree = any(n.step_type == "TEMP B-TREE" for n in orig_nodes)
            opt_has_btree = any(n.step_type == "TEMP B-TREE" for n in opt_nodes)
            btree_eliminated = orig_has_btree and not opt_has_btree

            orig_is_covering = any("COVERING INDEX" in (n.detail or "").upper() for n in orig_nodes)
            opt_is_covering = any("COVERING INDEX" in (n.detail or "").upper() for n in opt_nodes)
            covering_added = not orig_is_covering and opt_is_covering

            has_scan_to_search = ("SCAN" in orig_steps and "SEARCH" in opt_steps)
            plan_changed = planner_used_index or has_scan_to_search or (orig_steps != opt_steps) or btree_eliminated or covering_added

            if btree_eliminated:
                plan_change_desc = "Temp B-Tree Sort Eliminated"
            elif covering_added:
                plan_change_desc = "SEARCH → COVERING INDEX"
            elif has_scan_to_search:
                plan_change_desc = "SCAN → SEARCH"
            elif plan_changed and orig_steps and opt_steps:
                plan_change_desc = f"{' / '.join(orig_steps)} → {' / '.join(opt_steps)}"
            elif plan_changed:
                plan_change_desc = "Execution plan changed."
            else:
                plan_change_desc = "Execution plan unchanged."

            # 6. Multi-Run Statistical Comparisons (Using rounded medians)
            # absolute_difference = optimized_median - original_median
            median_diff = round(opt_median - orig_median, 2)
            time_saved = max(0.0, orig_median - opt_median)
            median_speedup = round((orig_median / opt_median), 2) if opt_median > 0 else 1.0

            # Percentage difference calculation:
            # negative means faster (runtime reduced), positive means slower (runtime increased)
            if orig_median > 0:
                percentage_diff = round(((opt_median - orig_median) / orig_median) * 100.0, 2)
            else:
                percentage_diff = 0.0

            # 7. Performance Classification with Tolerance
            # Mandatory Check: Result Equivalence First
            if not eq_res.is_equivalent:
                benchmark_applied = False
                perf_classification = "NO_MEASURABLE_IMPROVEMENT"
                note_msg = f"Result Equivalence Failed: {eq_res.explanation}"
            elif not (planner_used_index or btree_eliminated or covering_added):
                benchmark_applied = False
                perf_classification = "NO_MEASURABLE_IMPROVEMENT"
                note_msg = "Index was created in isolated benchmark database, but SQLite query planner selected a different execution plan for this query."
            elif percentage_diff < -tolerance_percent:
                benchmark_applied = True
                perf_classification = "PERFORMANCE_IMPROVED"
                note_msg = f"The tested candidate improved measured execution time by approximately {abs(percentage_diff):.1f}% in this multi-run benchmark ({orig_median:.2f}ms → {opt_median:.2f}ms)."
            elif percentage_diff > tolerance_percent:
                benchmark_applied = True
                perf_classification = "PERFORMANCE_REGRESSION"
                note_msg = f"The tested candidate increased measured execution time by approximately {percentage_diff:.1f}% in this multi-run benchmark ({orig_median:.2f}ms → {opt_median:.2f}ms)."
            else:
                benchmark_applied = True
                perf_classification = "NO_MEASURABLE_IMPROVEMENT"
                note_msg = f"The measured execution-time difference was very small (median changed by {percentage_diff:+.1f}%) and falls within the ±{tolerance_percent:.1f}% normal variance threshold ({orig_median:.2f}ms → {opt_median:.2f}ms)."

            return BenchmarkResult(
                original_metrics=orig_metrics,
                optimized_metrics=opt_metrics,
                speedup_factor=round(median_speedup, 2),
                time_saved_ms=round(time_saved, 2),
                indexes_created=created_indexes,
                benchmark_applied=benchmark_applied,
                plan_changed=plan_changed,
                plan_change_desc=plan_change_desc,
                equivalence_result=eq_res,
                note=note_msg,
                candidate_name=cand_name,
                strategy=cand_strategy,
                candidate_ddl=cand_ddl,
                warmup_runs=warmup_runs,
                measured_runs=measured_runs,
                original_times_ms=[round(t, 2) for t in orig_times],
                optimized_times_ms=[round(t, 2) for t in opt_times],
                original_warmup_times_ms=[round(t, 2) for t in orig_warmup_times],
                optimized_warmup_times_ms=[round(t, 2) for t in opt_warmup_times],
                original_median_ms=round(orig_median, 2),
                optimized_median_ms=round(opt_median, 2),
                original_min_ms=round(orig_min, 2),
                optimized_min_ms=round(opt_min, 2),
                original_max_ms=round(orig_max, 2),
                optimized_max_ms=round(opt_max, 2),
                median_difference_ms=round(median_diff, 2),
                median_speedup=round(median_speedup, 2),
                percentage_difference=round(percentage_diff, 2),
                performance_classification=perf_classification
            )

        except Exception as e:
            return BenchmarkResult(
                original_metrics=BenchmarkMetrics(exec_time_ms=0.0, rows_returned=0, plan_nodes=[], plan_summary="Error during benchmark execution."),
                benchmark_applied=False,
                note=f"Benchmark error: {str(e)}",
                candidate_name=cand_name,
                strategy=cand_strategy,
                candidate_ddl=cand_ddl
            )

        finally:
            # Clean up temporary database copy
            DatabaseManager.cleanup_temp_db(temp_db_path)

