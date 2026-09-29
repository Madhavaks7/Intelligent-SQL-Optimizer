import datetime
import uuid
import re
from typing import List, Optional
from models.analysis_models import HistoryEntry, BenchmarkResult, Recommendation


class HistoryManager:
    """
    Manages session-only in-memory optimization and analysis history records.
    Ensures complete isolation from database storage and preserves individual test runs.
    """

    def __init__(self):
        self.entries: List[HistoryEntry] = []

    @staticmethod
    def extract_candidate_name(recommendations: List[Recommendation], default: str = "No Index Candidate") -> str:
        """Extracts a concise human-readable candidate name (e.g. table(column)) from recommendations."""
        for r in recommendations:
            if r.type == "CREATE INDEX" and r.ddl:
                if r.table and r.columns:
                    return f"{r.table}({', '.join(r.columns)})"
                m = re.search(r"ON\s+(\w+)\s*\(([^)]+)\)", r.ddl, re.IGNORECASE)
                if m:
                    return f"{m.group(1)}({m.group(2).strip()})"
                return r.description
        return default

    def record_analysis(
        self,
        db_name: str,
        query: str,
        optimization_name: str,
        strategy: str,
        candidate_ddl: Optional[str] = None,
        query_name: Optional[str] = None
    ) -> HistoryEntry:
        """
        Records an analysis action. If an untested entry for the exact same query already exists
        at the head of history, updates it; otherwise appends a new 'Analyzed — Not Tested' entry.

        ``query_name`` is an optional snapshot of the PlaygroundQuery's user-visible name at the
        moment of recording. It is stored verbatim and never updated retroactively.
        """
        # Check if the latest entry is an untested entry for the same query
        if self.entries and not self.entries[-1].is_tested and self.entries[-1].query.strip() == query.strip():
            entry = self.entries[-1]
            entry.timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry.db_name = db_name
            entry.optimization_name = optimization_name
            entry.strategy = strategy
            entry.candidate_ddl = candidate_ddl
            if query_name is not None:
                entry.query_name = query_name
            return entry

        entry_id = uuid.uuid4().hex[:8]
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        entry = HistoryEntry(
            id=entry_id,
            timestamp=now_str,
            db_name=db_name,
            query=query,
            optimization_name=optimization_name,
            strategy=strategy,
            query_name=query_name,
            candidate_ddl=candidate_ddl,
            is_tested=False,
            validation_status="Analyzed — Not Tested"
        )
        self.entries.append(entry)
        return entry

    def record_test_result(
        self,
        db_name: str,
        query: str,
        optimization_name: str,
        strategy: str,
        candidate_ddl: Optional[str],
        benchmark_result: BenchmarkResult,
        query_name: Optional[str] = None
    ) -> HistoryEntry:
        """
        Records a completed optimization test result.
        Updates an existing untested entry or appends a new record if re-testing.
        Transfers robust multi-run benchmark metrics to the HistoryEntry.
        """
        orig = benchmark_result.original_metrics
        opt = benchmark_result.optimized_metrics
        eq = benchmark_result.equivalence_result

        orig_t = benchmark_result.original_median_ms if benchmark_result.original_median_ms is not None else (orig.exec_time_ms if orig else 0.0)
        opt_t = benchmark_result.optimized_median_ms if benchmark_result.optimized_median_ms is not None else (opt.exec_time_ms if opt else orig_t)
        time_diff = benchmark_result.median_difference_ms if benchmark_result.median_difference_ms is not None else (orig_t - opt_t)

        is_equiv = eq.is_equivalent if eq else True

        # Determine validation status based on multi-run performance classification & equivalence
        if not is_equiv:
            status = "⚠ Validation Failed"
        elif benchmark_result.original_median_ms is not None:
            if benchmark_result.performance_classification == "PERFORMANCE_IMPROVED":
                status = "✓ Validated Performance Improvement"
            elif benchmark_result.performance_classification == "PERFORMANCE_REGRESSION":
                status = "✗ Performance Regression"
            else:
                status = "≈ No Measurable Performance Improvement"
        else:
            # Fallback for manually mocked legacy BenchmarkResult instances
            rel_diff_pct = ((abs(orig_t - opt_t) / orig_t) * 100.0) if orig_t > 0 else 0.0
            if rel_diff_pct < 3.0:
                status = "≈ No Measurable Performance Improvement"
            elif opt_t < orig_t:
                status = "✓ Validated Performance Improvement"
            else:
                status = "✗ Performance Regression"

        orig_plan_summary = orig.plan_summary if orig else "N/A"
        opt_plan_summary = opt.plan_summary if opt else "N/A"

        # Check if there is a pending untested entry for this query
        pending_idx = None
        for idx in range(len(self.entries) - 1, -1, -1):
            if self.entries[idx].query.strip() == query.strip() and not self.entries[idx].is_tested:
                pending_idx = idx
                break

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if pending_idx is not None:
            # Update the pending entry
            entry = self.entries[pending_idx]
            entry.timestamp = now_str
            entry.is_tested = True
            entry.orig_time_ms = orig_t
            entry.opt_time_ms = opt_t
            entry.time_diff_ms = round(time_diff, 2)
            entry.speedup_factor = benchmark_result.speedup_factor
            entry.orig_plan = orig_plan_summary
            entry.opt_plan = opt_plan_summary
            entry.is_equivalent = is_equiv
            entry.equivalence_details = eq.explanation if eq else ""
            entry.validation_status = status
            # Multi-run benchmark fields
            entry.warmup_runs = benchmark_result.warmup_runs
            entry.measured_runs = benchmark_result.measured_runs
            entry.original_median_ms = benchmark_result.original_median_ms
            entry.optimized_median_ms = benchmark_result.optimized_median_ms
            entry.original_min_ms = benchmark_result.original_min_ms
            entry.optimized_min_ms = benchmark_result.optimized_min_ms
            entry.original_max_ms = benchmark_result.original_max_ms
            entry.optimized_max_ms = benchmark_result.optimized_max_ms
            entry.percentage_difference = benchmark_result.percentage_difference
            entry.performance_classification = benchmark_result.performance_classification
            entry.original_times_ms = list(benchmark_result.original_times_ms)
            entry.optimized_times_ms = list(benchmark_result.optimized_times_ms)
            # Snapshot query_name if provided and not yet set
            if query_name is not None and entry.query_name is None:
                entry.query_name = query_name
            return entry
        else:
            # Repeated test run or standalone test -> Create separate history record
            entry_id = uuid.uuid4().hex[:8]
            entry = HistoryEntry(
                id=entry_id,
                timestamp=now_str,
                db_name=db_name,
                query=query,
                optimization_name=optimization_name,
                strategy=strategy,
                query_name=query_name,
                candidate_ddl=candidate_ddl,
                is_tested=True,
                orig_time_ms=orig_t,
                opt_time_ms=opt_t,
                time_diff_ms=round(time_diff, 2),
                speedup_factor=benchmark_result.speedup_factor,
                orig_plan=orig_plan_summary,
                opt_plan=opt_plan_summary,
                is_equivalent=is_equiv,
                equivalence_details=eq.explanation if eq else "",
                validation_status=status,
                warmup_runs=benchmark_result.warmup_runs,
                measured_runs=benchmark_result.measured_runs,
                original_median_ms=benchmark_result.original_median_ms,
                optimized_median_ms=benchmark_result.optimized_median_ms,
                original_min_ms=benchmark_result.original_min_ms,
                optimized_min_ms=benchmark_result.optimized_min_ms,
                original_max_ms=benchmark_result.original_max_ms,
                optimized_max_ms=benchmark_result.optimized_max_ms,
                percentage_difference=benchmark_result.percentage_difference,
                performance_classification=benchmark_result.performance_classification,
                original_times_ms=list(benchmark_result.original_times_ms),
                optimized_times_ms=list(benchmark_result.optimized_times_ms)
            )
            self.entries.append(entry)
            return entry

    def update_ai_explanation(self, query: str, ai_explanation: str, entry_id: Optional[str] = None) -> Optional[HistoryEntry]:
        """
        Updates the AI explanation for a history entry.
        If entry_id is provided, updates that specific entry; otherwise updates the latest matching query entry.
        """
        if entry_id:
            for entry in reversed(self.entries):
                if entry.id == entry_id:
                    entry.ai_explanation = ai_explanation
                    return entry
        for entry in reversed(self.entries):
            if entry.query.strip() == query.strip():
                entry.ai_explanation = ai_explanation
                return entry
        return None

    def get_entries(self) -> List[HistoryEntry]:
        """Returns all history entries."""
        return list(self.entries)

    def clear(self) -> None:
        """Clears all session history entries."""
        self.entries.clear()
