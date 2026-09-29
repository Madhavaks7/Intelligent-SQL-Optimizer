import sqlglot
from sqlglot import exp
from typing import List, Optional, Tuple, Dict, Any
from models.analysis_models import (
    ParsedQuery, TableRef, ColumnRef, Predicate, JoinRef, OrderByRef
)

class SQLParser:
    """
    Robust SQL Parser powered by SQLGlot for AST extraction.
    Parses SQL queries into structured Python dataclasses.
    """

    @staticmethod
    def parse_query(sql: str) -> ParsedQuery:
        clean_sql = sql.strip()

        # Handle empty query
        if not clean_sql:
            return ParsedQuery(
                raw_sql=sql,
                parse_error="Empty query provided."
            )

        try:
            # Parse SQL using SQLGlot (SQLite dialect)
            parsed_expressions = sqlglot.parse(clean_sql, read="sqlite")
            if not parsed_expressions or parsed_expressions[0] is None:
                return ParsedQuery(
                    raw_sql=sql,
                    parse_error="Unable to parse SQL query structure."
                )

            ast = parsed_expressions[0]

            # Verify statement type
            statement_type = ast.key.upper() if hasattr(ast, "key") else "UNKNOWN"
            is_select = isinstance(ast, exp.Select)

            if not is_select:
                return ParsedQuery(
                    raw_sql=sql,
                    is_select=False,
                    statement_type=statement_type,
                    parse_error=f"Statement is a {statement_type}, not a SELECT query."
                )

            # 1. Extract Tables
            tables = []
            for table_expr in ast.find_all(exp.Table):
                # Avoid picking up CTEs or aliases as separate physical tables
                t_name = table_expr.name
                t_alias = table_expr.alias if table_expr.alias else None
                if t_name and not any(t.name.lower() == t_name.lower() for t in tables):
                    tables.append(TableRef(name=t_name, alias=t_alias))

            # 2. Extract Selected Columns
            selected_columns = []
            if hasattr(ast, "selects"):
                for sel in ast.selects:
                    if isinstance(sel, exp.Star):
                        selected_columns.append(ColumnRef(name="*", is_wildcard=True))
                    elif isinstance(sel, exp.Column):
                        col_name = sel.name
                        tbl_alias = sel.table if sel.table else None
                        selected_columns.append(ColumnRef(name=col_name, table=tbl_alias))
                    elif isinstance(sel, exp.Alias):
                        # E.g. expr AS alias
                        alias_name = sel.alias
                        inner = sel.this
                        if isinstance(inner, exp.Column):
                            selected_columns.append(ColumnRef(
                                name=inner.name,
                                table=inner.table if inner.table else None,
                                alias=alias_name
                            ))
                        elif isinstance(inner, exp.Star):
                            selected_columns.append(ColumnRef(name="*", is_wildcard=True, alias=alias_name))
                        elif isinstance(inner, exp.Func):
                            func_name = inner.key.upper()
                            first_arg = str(inner.this) if inner.this else "*"
                            selected_columns.append(ColumnRef(
                                name=first_arg,
                                alias=alias_name,
                                agg_func=func_name
                            ))
                        else:
                            selected_columns.append(ColumnRef(name=str(inner), alias=alias_name))
                    elif isinstance(sel, exp.Func):
                        func_name = sel.key.upper()
                        first_arg = str(sel.this) if sel.this else "*"
                        selected_columns.append(ColumnRef(name=first_arg, agg_func=func_name))
                    else:
                        selected_columns.append(ColumnRef(name=str(sel)))

            # 3. Extract WHERE Predicates
            where_predicates = []
            where_clause = ast.find(exp.Where)
            if where_clause:
                where_predicates = SQLParser._extract_predicates(where_clause.this)

            # 4. Extract JOINs
            joins = []
            for join_expr in ast.find_all(exp.Join):
                join_type = join_expr.side or "INNER"
                if join_expr.kind:
                    join_type = f"{join_type} {join_expr.kind}".strip()
                
                # Right table
                r_table = ""
                r_alias = None
                if isinstance(join_expr.this, exp.Table):
                    r_table = join_expr.this.name
                    r_alias = join_expr.this.alias if join_expr.this.alias else None
                elif isinstance(join_expr.this, exp.Alias):
                    r_alias = join_expr.this.alias
                    if isinstance(join_expr.this.this, exp.Table):
                        r_table = join_expr.this.this.name

                on_conds = []
                on_clause = join_expr.args.get("on")
                if on_clause:
                    on_conds.append(str(on_clause))

                if r_table:
                    joins.append(JoinRef(
                        join_type=join_type.upper(),
                        right_table=r_table,
                        right_alias=r_alias,
                        on_conditions=on_conds
                    ))

            # 5. Extract GROUP BY
            group_by = []
            group_clause = ast.find(exp.Group)
            if group_clause:
                for grp in group_clause.expressions:
                    if isinstance(grp, exp.Column):
                        group_by.append(grp.name)
                    else:
                        group_by.append(str(grp))

            # 6. Extract ORDER BY
            order_by = []
            order_clause = ast.find(exp.Order)
            if order_clause:
                for ord_expr in order_clause.expressions:
                    col_name = str(ord_expr.this)
                    tbl_name = None
                    if isinstance(ord_expr.this, exp.Column):
                        col_name = ord_expr.this.name
                        tbl_name = ord_expr.this.table if ord_expr.this.table else None
                    
                    direction = "DESC" if ord_expr.args.get("desc") else "ASC"
                    order_by.append(OrderByRef(
                        column=col_name,
                        table=tbl_name,
                        direction=direction
                    ))

            # 7. Extract HAVING
            having = []
            having_clause = ast.find(exp.Having)
            if having_clause:
                having.append(str(having_clause.this))

            # 8. Extract LIMIT
            limit_val = None
            limit_clause = ast.find(exp.Limit)
            if limit_clause and limit_clause.expression:
                try:
                    limit_val = int(str(limit_clause.expression))
                except ValueError:
                    pass

            # 9. Extract DISTINCT
            has_distinct = ast.args.get("distinct") is not None

            # 10. Subqueries count
            subquery_count = len(list(ast.find_all(exp.Subquery)))

            return ParsedQuery(
                raw_sql=clean_sql,
                is_select=is_select,
                statement_type=statement_type,
                tables=tables,
                selected_columns=selected_columns,
                where_predicates=where_predicates,
                joins=joins,
                group_by=group_by,
                order_by=order_by,
                having=having,
                limit=limit_val,
                has_distinct=has_distinct,
                subquery_count=subquery_count,
                parse_error=None
            )

        except Exception as e:
            return ParsedQuery(
                raw_sql=sql,
                parse_error=f"SQLGlot Syntax Error: {str(e)}"
            )

    @staticmethod
    def _extract_predicates(node) -> List[Predicate]:
        """
        Recursively decomposes binary WHERE expressions (AND/OR/EQ/GT/LT/LIKE/in, etc.)
        """
        predicates = []
        if node is None:
            return predicates

        if isinstance(node, exp.And) or isinstance(node, exp.Or):
            predicates.extend(SQLParser._extract_predicates(node.left))
            predicates.extend(SQLParser._extract_predicates(node.right))
        elif isinstance(node, exp.Binary):
            op_symbol = node.key.upper()
            left = node.left
            right = node.right

            col_name = str(left)
            tbl_name = None
            is_func = False

            if isinstance(left, exp.Column):
                col_name = left.name
                tbl_name = left.table if left.table else None
            elif isinstance(left, exp.Func):
                is_func = True
                if left.this and isinstance(left.this, exp.Column):
                    col_name = left.this.name
                    tbl_name = left.this.table if left.this.table else None
                else:
                    col_name = str(left)

            val_str = str(right)
            if isinstance(right, exp.Literal):
                val_str = right.name

            predicates.append(Predicate(
                column=col_name,
                table=tbl_name,
                operator=op_symbol,
                value=val_str,
                raw_expr=str(node),
                is_function_call=is_func
            ))
        elif isinstance(node, exp.In):
            left = node.this
            col_name = left.name if isinstance(left, exp.Column) else str(left)
            tbl_name = left.table if isinstance(left, exp.Column) and left.table else None
            predicates.append(Predicate(
                column=col_name,
                table=tbl_name,
                operator="IN",
                value=str(node.expressions),
                raw_expr=str(node)
            ))
        else:
            # Fallback for unary or custom expressions
            predicates.append(Predicate(
                column=str(node),
                raw_expr=str(node)
            ))

        return predicates
