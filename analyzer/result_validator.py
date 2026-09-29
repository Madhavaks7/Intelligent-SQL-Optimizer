from typing import List, Dict, Any, Tuple, Optional
from collections import Counter
from models.analysis_models import EquivalenceResult


class ResultValidator:
    """
    Generic Result-Equivalence Validator.
    Compares complete result sets across column structure, row counts, values,
    types, duplicate row multiplicities, and ORDER BY semantics.
    """

    @staticmethod
    def _to_canonical_tuple(row: Any, columns: Optional[List[str]] = None) -> Tuple:
        """Converts a row dict, tuple, or list into a canonical hashable tuple of values."""
        if isinstance(row, dict):
            if columns:
                return tuple(row.get(col) for col in columns)
            return tuple(row.values())
        elif isinstance(row, (list, tuple)):
            return tuple(row)
        else:
            return (row,)

    @staticmethod
    def compare_results(
        orig_columns: List[str],
        orig_rows: List[Any],
        opt_columns: List[str],
        opt_rows: List[Any],
        has_order_by: bool = False
    ) -> EquivalenceResult:
        """
        Performs full result set comparison between original and optimized queries.
        """
        orig_col_count = len(orig_columns)
        opt_col_count = len(opt_columns)
        orig_row_count = len(orig_rows)
        opt_row_count = len(opt_rows)

        # 1. Column Structure Validation
        column_count_match = (orig_col_count == opt_col_count)
        columns_match = (orig_columns == opt_columns)

        if not column_count_match:
            return EquivalenceResult(
                is_equivalent=False,
                columns_match=False,
                column_count_match=False,
                row_count_match=(orig_row_count == opt_row_count),
                values_match=False,
                order_match=False,
                has_order_by=has_order_by,
                orig_row_count=orig_row_count,
                opt_row_count=opt_row_count,
                orig_col_count=orig_col_count,
                opt_col_count=opt_col_count,
                orig_columns=orig_columns,
                opt_columns=opt_columns,
                explanation="Column count mismatch between original and optimized queries.",
                mismatch_reason="Different column count"
            )

        if not columns_match:
            return EquivalenceResult(
                is_equivalent=False,
                columns_match=False,
                column_count_match=True,
                row_count_match=(orig_row_count == opt_row_count),
                values_match=False,
                order_match=False,
                has_order_by=has_order_by,
                orig_row_count=orig_row_count,
                opt_row_count=opt_row_count,
                orig_col_count=orig_col_count,
                opt_col_count=opt_col_count,
                orig_columns=orig_columns,
                opt_columns=opt_columns,
                explanation="Column names or column order mismatch between original and optimized queries.",
                mismatch_reason="Different column names/order"
            )

        # 2. Row Count Validation
        row_count_match = (orig_row_count == opt_row_count)
        if not row_count_match:
            return EquivalenceResult(
                is_equivalent=False,
                columns_match=True,
                column_count_match=True,
                row_count_match=False,
                values_match=False,
                order_match=False,
                has_order_by=has_order_by,
                orig_row_count=orig_row_count,
                opt_row_count=opt_row_count,
                orig_col_count=orig_col_count,
                opt_col_count=opt_col_count,
                orig_columns=orig_columns,
                opt_columns=opt_columns,
                explanation=f"Row count mismatch: Original returned {orig_row_count} rows, Optimized returned {opt_row_count} rows.",
                mismatch_reason="Different row count"
            )

        # Convert rows to canonical tuples
        orig_tuples = [ResultValidator._to_canonical_tuple(r, orig_columns) for r in orig_rows]
        opt_tuples = [ResultValidator._to_canonical_tuple(r, opt_columns) for r in opt_rows]

        # 3. Value & Order Comparison
        if has_order_by:
            # Strict ordered comparison
            is_identical_sequence = (orig_tuples == opt_tuples)
            if is_identical_sequence:
                return EquivalenceResult(
                    is_equivalent=True,
                    columns_match=True,
                    column_count_match=True,
                    row_count_match=True,
                    values_match=True,
                    order_match=True,
                    has_order_by=True,
                    orig_row_count=orig_row_count,
                    opt_row_count=opt_row_count,
                    orig_col_count=orig_col_count,
                    opt_col_count=opt_col_count,
                    orig_columns=orig_columns,
                    opt_columns=opt_columns,
                    explanation="Results are equivalent in the requested order.",
                    mismatch_reason=None
                )
            else:
                # Check if values match as multiset but order is different
                multiset_match = (Counter(orig_tuples) == Counter(opt_tuples))
                if multiset_match:
                    explanation = "The optimized query returned the same rows but in a different order, failing requested ORDER BY sequence."
                    mismatch_reason = "Different ordered result"
                else:
                    explanation = "The optimized query returned different row values."
                    mismatch_reason = "Different row values"

                return EquivalenceResult(
                    is_equivalent=False,
                    columns_match=True,
                    column_count_match=True,
                    row_count_match=True,
                    values_match=multiset_match,
                    order_match=False,
                    has_order_by=True,
                    orig_row_count=orig_row_count,
                    opt_row_count=opt_row_count,
                    orig_col_count=orig_col_count,
                    opt_col_count=opt_col_count,
                    orig_columns=orig_columns,
                    opt_columns=opt_columns,
                    explanation=explanation,
                    mismatch_reason=mismatch_reason
                )
        else:
            # Unordered multiset comparison (bag equivalence preserving duplicate counts)
            multiset_match = (Counter(orig_tuples) == Counter(opt_tuples))
            if multiset_match:
                return EquivalenceResult(
                    is_equivalent=True,
                    columns_match=True,
                    column_count_match=True,
                    row_count_match=True,
                    values_match=True,
                    order_match=True,
                    has_order_by=False,
                    orig_row_count=orig_row_count,
                    opt_row_count=opt_row_count,
                    orig_col_count=orig_col_count,
                    opt_col_count=opt_col_count,
                    orig_columns=orig_columns,
                    opt_columns=opt_columns,
                    explanation="The optimized query returned the same result set as the original query (row order was ignored because the query does not specify ORDER BY).",
                    mismatch_reason=None
                )
            else:
                return EquivalenceResult(
                    is_equivalent=False,
                    columns_match=True,
                    column_count_match=True,
                    row_count_match=True,
                    values_match=False,
                    order_match=False,
                    has_order_by=False,
                    orig_row_count=orig_row_count,
                    opt_row_count=opt_row_count,
                    orig_col_count=orig_col_count,
                    opt_col_count=opt_col_count,
                    orig_columns=orig_columns,
                    opt_columns=opt_columns,
                    explanation="The optimized query returned different row values or duplicate multiplicities.",
                    mismatch_reason="Different row values"
                )
