import re
from typing import List, Dict, Any, Optional, Tuple
from models.analysis_models import PlanNode

class QueryPlanAnalyzer:
    """
    Parses and interprets SQLite's EXPLAIN QUERY PLAN output.
    Constructs plan tree nodes and classifies operations (SCAN, SEARCH, COVERING INDEX, TEMP B-TREE).
    """

    @staticmethod
    def parse_explain_output(raw_rows: List[Dict[str, Any]]) -> List[PlanNode]:
        """
        Converts SQLite explain query plan records into structured PlanNodes.
        SQLite EXPLAIN QUERY PLAN returns rows with keys: id, parent, notused, detail
        """
        nodes = []
        for r in raw_rows:
            node_id = r.get("id", 0)
            parent_id = r.get("parent", 0)
            detail = str(r.get("detail", ""))

            step_type, table_name, index_name, scan_type = QueryPlanAnalyzer._classify_detail(detail)

            nodes.append(PlanNode(
                id=node_id,
                parent=parent_id,
                detail=detail,
                step_type=step_type,
                table_name=table_name,
                index_name=index_name,
                scan_type=scan_type,
                raw_text=detail
            ))

        return nodes

    @staticmethod
    def _classify_detail(detail: str) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
        """
        Classifies detail string into step_type, table_name, index_name, scan_type.
        Examples:
        - "SCAN TABLE Employee" -> SCAN, Employee, None, Full Scan
        - "SEARCH TABLE Employee USING INDEX idx_employee_dept (department_id=?)" -> SEARCH, Employee, idx_employee_dept, Index Search
        - "SEARCH TABLE Employee USING COVERING INDEX idx_cov" -> SEARCH, Employee, idx_cov, Covering Index Search
        - "USE TEMP B-TREE FOR ORDER BY" -> TEMP B-TREE, None, None, Temporary Sort B-Tree (ORDER BY)
        - "USE TEMP B-TREE FOR GROUP BY" -> TEMP B-TREE, None, None, Temporary Sort B-Tree (GROUP BY)
        - "USE TEMP B-TREE FOR DISTINCT" -> TEMP B-TREE, None, None, Temporary Sort B-Tree (DISTINCT)
        """
        detail_upper = detail.upper()
        step_type = "OTHER"
        table_name = None
        index_name = None
        scan_type = None

        if "USE TEMP B-TREE" in detail_upper:
            step_type = "TEMP B-TREE"
            # Batch 5: Discriminate purpose of the temp B-tree for downstream rules
            if "FOR ORDER BY" in detail_upper:
                scan_type = "Temporary Sort B-Tree (ORDER BY)"
            elif "FOR GROUP BY" in detail_upper:
                scan_type = "Temporary Sort B-Tree (GROUP BY)"
            elif "FOR DISTINCT" in detail_upper:
                scan_type = "Temporary Sort B-Tree (DISTINCT)"
            else:
                scan_type = "Temporary Sort B-Tree"

        elif "SCAN" in detail_upper:
            step_type = "SCAN"
            scan_type = "Full Table Scan"
            # Extract table name
            m_tbl = re.search(r"SCAN\s+(?:TABLE\s+)?([^\s\(\)]+)", detail, re.IGNORECASE)
            if m_tbl:
                table_name = m_tbl.group(1).strip()

        elif "SEARCH" in detail_upper:
            step_type = "SEARCH"
            scan_type = "Index Search"
            m_tbl = re.search(r"SEARCH\s+(?:TABLE\s+)?([^\s\(\)]+)", detail, re.IGNORECASE)
            if m_tbl:
                table_name = m_tbl.group(1).strip()

            if "COVERING INDEX" in detail_upper:
                scan_type = "Covering Index Search"

            m_idx = re.search(r"INDEX\s+([^\s\(\)]+)", detail, re.IGNORECASE)
            if m_idx:
                index_name = m_idx.group(1).strip()

        elif "COMPOUND QUERY" in detail_upper or "SUBQUERY" in detail_upper:
            step_type = "SUBQUERY"
            scan_type = "Subquery / Compound"

        return step_type, table_name, index_name, scan_type

    @staticmethod
    def contains_full_scan(nodes: List[PlanNode]) -> List[str]:
        """Returns list of table names performing full table SCAN."""
        scan_tables = []
        for n in nodes:
            if n.step_type == "SCAN" and n.table_name:
                scan_tables.append(n.table_name)
        return scan_tables

    @staticmethod
    def contains_temp_btree(nodes: List[PlanNode]) -> bool:
        """Returns True if plan contains temporary B-tree sorting/grouping."""
        return any(n.step_type == "TEMP B-TREE" for n in nodes)

    @staticmethod
    def temp_btree_purpose(nodes: List[PlanNode]) -> Optional[str]:
        """
        Batch 5: Returns the purpose of the first TEMP B-TREE node found, or None.
        Returns one of: 'ORDER BY', 'GROUP BY', 'DISTINCT', or 'UNKNOWN'.
        """
        for n in nodes:
            if n.step_type == "TEMP B-TREE" and n.scan_type:
                st = n.scan_type.upper()
                if "ORDER BY" in st:
                    return "ORDER BY"
                if "GROUP BY" in st:
                    return "GROUP BY"
                if "DISTINCT" in st:
                    return "DISTINCT"
                return "UNKNOWN"
        return None

    @staticmethod
    def format_plan_summary(nodes: List[PlanNode]) -> str:
        """Formats plan nodes into clean text tree."""
        if not nodes:
            return "No execution plan available."
        lines = []
        for n in nodes:
            indent = "  " * max(0, n.id)
            lines.append(f"{indent}• [{n.step_type}] {n.detail}")
        return "\n".join(lines)
