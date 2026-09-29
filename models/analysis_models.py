from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any

class Severity(Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    INFO = "Informational"

    def __str__(self):
        return self.value

@dataclass
class TableRef:
    name: str
    alias: Optional[str] = None

@dataclass
class ColumnRef:
    name: str
    table: Optional[str] = None
    alias: Optional[str] = None
    is_wildcard: bool = False
    agg_func: Optional[str] = None

@dataclass
class Predicate:
    column: str
    table: Optional[str] = None
    operator: str = "="
    value: Optional[str] = None
    raw_expr: str = ""
    is_function_call: bool = False

@dataclass
class JoinRef:
    join_type: str
    right_table: str
    right_alias: Optional[str] = None
    on_conditions: List[str] = field(default_factory=list)

@dataclass
class OrderByRef:
    column: str
    table: Optional[str] = None
    direction: str = "ASC"

@dataclass
class ParsedQuery:
    raw_sql: str
    is_select: bool = True
    statement_type: str = "SELECT"
    tables: List[TableRef] = field(default_factory=list)
    selected_columns: List[ColumnRef] = field(default_factory=list)
    where_predicates: List[Predicate] = field(default_factory=list)
    joins: List[JoinRef] = field(default_factory=list)
    group_by: List[str] = field(default_factory=list)
    order_by: List[OrderByRef] = field(default_factory=list)
    having: List[str] = field(default_factory=list)
    limit: Optional[int] = None
    has_distinct: bool = False
    subquery_count: int = 0
    parse_error: Optional[str] = None

@dataclass
class ColumnSchema:
    name: str
    data_type: str
    not_null: bool = False
    pk: bool = False

@dataclass
class IndexSchema:
    name: str
    table_name: str
    is_unique: bool
    columns: List[str]

@dataclass
class TableSchema:
    name: str
    columns: List[ColumnSchema]
    primary_key: List[str]
    row_count: int = 0
    indexes: List[IndexSchema] = field(default_factory=list)

@dataclass
class DatabaseSchema:
    db_path: str
    tables: Dict[str, TableSchema] = field(default_factory=dict)
    indexes: List[IndexSchema] = field(default_factory=list)

@dataclass
class PlanNode:
    id: int
    parent: int
    detail: str
    step_type: str  # SCAN, SEARCH, TEMP B-TREE, COVERING INDEX, etc.
    table_name: Optional[str] = None
    index_name: Optional[str] = None
    scan_type: Optional[str] = None
    raw_text: str = ""

@dataclass
class Finding:
    rule_id: str
    title: str
    severity: Severity
    category: str  # Indexing, Query Design, Execution Plan, Sorting
    description: str
    rationale: str
    affected_table: Optional[str] = None
    affected_column: Optional[str] = None

@dataclass
class Recommendation:
    id: str
    type: str  # CREATE INDEX, QUERY REWRITE, ADVISORY
    description: str
    ddl: Optional[str] = None
    suggested_query: Optional[str] = None
    expected_impact: str = "Medium"
    table: Optional[str] = None
    columns: List[str] = field(default_factory=list)
    strategy: Optional[str] = None
    source_rules: List[str] = field(default_factory=list)

@dataclass
class BenchmarkMetrics:
    exec_time_ms: float
    rows_returned: int
    plan_nodes: List[PlanNode]
    plan_summary: str
    min_time_ms: Optional[float] = None
    max_time_ms: Optional[float] = None
    median_time_ms: Optional[float] = None
    raw_times_ms: List[float] = field(default_factory=list)
    warmup_times_ms: List[float] = field(default_factory=list)

@dataclass
class EquivalenceResult:
    is_equivalent: bool
    columns_match: bool
    column_count_match: bool
    row_count_match: bool
    values_match: bool
    order_match: bool
    has_order_by: bool
    orig_row_count: int
    opt_row_count: int
    orig_col_count: int
    opt_col_count: int
    orig_columns: List[str] = field(default_factory=list)
    opt_columns: List[str] = field(default_factory=list)
    explanation: str = ""
    mismatch_reason: Optional[str] = None

@dataclass
class BenchmarkResult:
    original_metrics: BenchmarkMetrics
    optimized_metrics: Optional[BenchmarkMetrics] = None
    speedup_factor: float = 1.0
    time_saved_ms: float = 0.0
    indexes_created: List[str] = field(default_factory=list)
    benchmark_applied: bool = False
    plan_changed: bool = False
    plan_change_desc: str = "Execution plan unchanged."
    equivalence_result: Optional[EquivalenceResult] = None
    note: str = ""
    candidate_name: Optional[str] = None
    strategy: Optional[str] = None
    candidate_ddl: Optional[str] = None
    # Batch 6 Multi-Run Benchmark Fields
    warmup_runs: int = 3
    measured_runs: int = 7
    original_times_ms: List[float] = field(default_factory=list)
    optimized_times_ms: List[float] = field(default_factory=list)
    original_warmup_times_ms: List[float] = field(default_factory=list)
    optimized_warmup_times_ms: List[float] = field(default_factory=list)
    original_median_ms: Optional[float] = None
    optimized_median_ms: Optional[float] = None
    original_min_ms: Optional[float] = None
    optimized_min_ms: Optional[float] = None
    original_max_ms: Optional[float] = None
    optimized_max_ms: Optional[float] = None
    median_difference_ms: Optional[float] = None
    median_speedup: float = 1.0
    percentage_difference: float = 0.0
    performance_classification: str = "NO_MEASURABLE_IMPROVEMENT"



TESTABLE_OPTIMIZATION_STRATEGIES = {
    "INDEX_BASED",
    "COMPOSITE_INDEX",
    "JOIN_INDEX",
    "ORDER_BY_INDEX",
    "COVERING_INDEX",
}


@dataclass
class AnalysisResult:
    query: str
    parsed_query: ParsedQuery
    schema: DatabaseSchema
    plan_nodes: List[PlanNode]
    findings: List[Finding]
    recommendations: List[Recommendation]
    suggested_query: Optional[str] = None
    optimization_strategy: str = "NO_OPTIMIZATION_REQUIRED"  # INDEX_BASED, COMPOSITE_INDEX, JOIN_INDEX, ORDER_BY_INDEX, COVERING_INDEX, QUERY_REWRITE, NO_OPTIMIZATION_REQUIRED, INFORMATIONAL_ADVISORY
    benchmark_result: Optional[BenchmarkResult] = None
    ai_explanation: Optional[str] = None

    @property
    def is_test_optimization_eligible(self) -> bool:
        """Returns True if the analysis result contains a valid executable index candidate and testable strategy."""
        executable_recs = [r for r in self.recommendations if r.type == "CREATE INDEX" and r.ddl]
        return self.optimization_strategy in TESTABLE_OPTIMIZATION_STRATEGIES and len(executable_recs) > 0


@dataclass
class HistoryEntry:
    id: str
    timestamp: str
    db_name: str
    query: str
    optimization_name: str
    strategy: str
    query_name: Optional[str] = None  # Snapshot of the PlaygroundQuery's user-visible name at creation time
    candidate_ddl: Optional[str] = None
    is_tested: bool = False
    orig_time_ms: Optional[float] = None
    opt_time_ms: Optional[float] = None
    time_diff_ms: Optional[float] = None
    speedup_factor: Optional[float] = None
    orig_plan: Optional[str] = None
    opt_plan: Optional[str] = None
    is_equivalent: Optional[bool] = None
    equivalence_details: Optional[str] = None
    validation_status: str = "Analyzed — Not Tested"
    # Batch 6 Multi-Run Benchmark Fields
    warmup_runs: Optional[int] = None
    measured_runs: Optional[int] = None
    original_median_ms: Optional[float] = None
    optimized_median_ms: Optional[float] = None
    original_min_ms: Optional[float] = None
    optimized_min_ms: Optional[float] = None
    original_max_ms: Optional[float] = None
    optimized_max_ms: Optional[float] = None
    percentage_difference: Optional[float] = None
    performance_classification: Optional[str] = None
    original_times_ms: List[float] = field(default_factory=list)
    optimized_times_ms: List[float] = field(default_factory=list)
    # Batch 7 AI Explanation
    ai_explanation: Optional[str] = None



@dataclass
class PlaygroundQuery:
    """Represents a single query slot in the Query Playground with its own isolated state."""
    id: str
    name: str
    sql: str = ""
    # Current analysis/test state — cleared whenever SQL is edited
    analysis_result: Optional["AnalysisResult"] = None
    test_result: Optional[BenchmarkResult] = None
    # "not_analyzed" | "analyzed" | "tested"
    status: str = "not_analyzed"
    created_at: str = ""

