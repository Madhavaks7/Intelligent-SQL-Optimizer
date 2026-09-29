import datetime
import uuid
from typing import List, Optional

from models.analysis_models import PlaygroundQuery, AnalysisResult, BenchmarkResult


class PlaygroundManager:
    """
    Manages the collection of PlaygroundQuery objects for the current database session.

    Key invariants:
    - Every query has a unique internal ``id``.
    - Each query maintains its own isolated analysis_result / test_result / status.
    - Updating a query's SQL *always* clears its stale analysis/test state.
    - Deleting a query removes it from the playground but never touches HistoryManager.
    - ``reset()`` clears all queries (called on database switch).
    """

    def __init__(self):
        self._queries: List[PlaygroundQuery] = []
        self._active_id: Optional[str] = None
        self._counter: int = 0  # monotonic counter for default names

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_name(self) -> str:
        self._counter += 1
        return f"Query {self._counter}"

    def _now(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _find(self, query_id: str) -> Optional[PlaygroundQuery]:
        for q in self._queries:
            if q.id == query_id:
                return q
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_query(self, name: Optional[str] = None) -> PlaygroundQuery:
        """Creates a new query with completely clean state and selects it as active."""
        qid = uuid.uuid4().hex[:12]
        qname = name if name else self._next_name()
        pq = PlaygroundQuery(
            id=qid,
            name=qname,
            sql="",
            analysis_result=None,
            test_result=None,
            status="not_analyzed",
            created_at=self._now(),
        )
        self._queries.append(pq)
        self._active_id = qid
        return pq

    def ensure_default(self) -> PlaygroundQuery:
        """Ensures at least one query exists; creates 'Query 1' if the playground is empty."""
        if not self._queries:
            return self.create_query()
        if self._active_id is None:
            self._active_id = self._queries[0].id
        return self.get_active()

    def get_query(self, query_id: str) -> Optional[PlaygroundQuery]:
        """Returns the query with the given id, or None if not found."""
        return self._find(query_id)

    def get_all(self) -> List[PlaygroundQuery]:
        """Returns all queries in creation order."""
        return list(self._queries)

    def get_active(self) -> Optional[PlaygroundQuery]:
        """Returns the currently selected/active query."""
        if self._active_id is None:
            return None
        return self._find(self._active_id)

    def get_active_id(self) -> Optional[str]:
        """Returns the id of the active query."""
        return self._active_id

    def set_active(self, query_id: str) -> bool:
        """Selects a query as the active one. Returns False if the id does not exist."""
        if self._find(query_id) is None:
            return False
        self._active_id = query_id
        return True

    def rename_query(self, query_id: str, new_name: str) -> bool:
        """Renames a query's user-visible name. Returns False if not found."""
        q = self._find(query_id)
        if q is None:
            return False
        q.name = new_name.strip() or q.name
        return True

    def update_sql(self, query_id: str, new_sql: str) -> bool:
        """
        Updates a query's SQL text.
        IMPORTANT: Always clears stale analysis_result, test_result, and status
        when the SQL text actually changes.
        Returns False if not found.
        """
        q = self._find(query_id)
        if q is None:
            return False
        if q.sql.strip() != new_sql.strip():
            q.sql = new_sql
            q.analysis_result = None
            q.test_result = None
            q.status = "not_analyzed"
        return True

    def set_analysis(self, query_id: str, result: AnalysisResult) -> bool:
        """Stores an analysis result for a query and marks it as 'analyzed'. Returns False if not found."""
        q = self._find(query_id)
        if q is None:
            return False
        q.analysis_result = result
        q.test_result = None  # Clear any old test result since SQL was re-analyzed
        q.status = "analyzed"
        return True

    def set_test_result(self, query_id: str, result: BenchmarkResult) -> bool:
        """Stores a test result for a query and marks it as 'tested'. Returns False if not found."""
        q = self._find(query_id)
        if q is None:
            return False
        q.test_result = result
        q.status = "tested"
        # Also update the analysis_result's benchmark so tabs render correctly
        if q.analysis_result is not None:
            q.analysis_result.benchmark_result = result
        return True

    def delete_query(self, query_id: str) -> bool:
        """
        Removes a query from the playground.
        Does NOT touch HistoryManager — history is an independent audit log.
        Returns False if not found.
        """
        q = self._find(query_id)
        if q is None:
            return False
        self._queries = [x for x in self._queries if x.id != query_id]

        # Re-select active query if the deleted one was active
        if self._active_id == query_id:
            if self._queries:
                self._active_id = self._queries[-1].id
            else:
                self._active_id = None
        return True

    def reset(self) -> None:
        """
        Clears the entire playground (called on database switch).
        Does NOT interact with HistoryManager.
        """
        self._queries.clear()
        self._active_id = None
        self._counter = 0

    def status_label(self, query: PlaygroundQuery) -> str:
        """Returns a compact status indicator string for display in the query selector."""
        if query.status == "tested":
            return "✓ Tested"
        elif query.status == "analyzed":
            return "✓ Analyzed"
        else:
            return "○ Not Analyzed"
