import sqlite3
from typing import Dict, List, Optional
from models.analysis_models import DatabaseSchema, TableSchema, ColumnSchema, IndexSchema
from database.database import DatabaseManager

class SchemaInspector:
    """
    Introspects SQLite database schema metadata using system PRAGMAs.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

    def inspect_database(self) -> DatabaseSchema:
        """
        Retrieves complete database schema info (tables, columns, indexes, row counts).
        """
        schema = DatabaseSchema(db_path=self.db_manager.db_path)

        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Fetch user tables (exclude sqlite system tables)
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%';
            """)
            table_names = [row[0] for row in cursor.fetchall()]

            for tname in table_names:
                # 2. Fetch row count
                try:
                    cursor.execute(f'SELECT COUNT(*) FROM "{tname}"')
                    row_count = cursor.fetchone()[0]
                except Exception:
                    row_count = 0

                # 3. Fetch columns info
                cursor.execute(f'PRAGMA table_info("{tname}")')
                col_rows = cursor.fetchall()

                columns = []
                primary_keys = []
                for c in col_rows:
                    # c: (cid, name, type, notnull, dflt_value, pk)
                    c_name = c[1]
                    c_type = c[2]
                    c_notnull = bool(c[3])
                    c_pk = bool(c[5])
                    columns.append(ColumnSchema(
                        name=c_name,
                        data_type=c_type if c_type else "TEXT",
                        not_null=c_notnull,
                        pk=c_pk
                    ))
                    if c_pk:
                        primary_keys.append(c_name)

                # 4. Fetch indexes info
                cursor.execute(f'PRAGMA index_list("{tname}")')
                idx_rows = cursor.fetchall()

                indexes = []
                for idx in idx_rows:
                    # idx: (seq, name, unique, origin, partial)
                    idx_name = idx[1]
                    is_unique = bool(idx[2])

                    # Fetch index columns
                    cursor.execute(f'PRAGMA index_info("{idx_name}")')
                    idx_cols_rows = cursor.fetchall()
                    # sort by seqno
                    idx_cols = [c[2] for c in sorted(idx_cols_rows, key=lambda x: x[0])]

                    idx_schema = IndexSchema(
                        name=idx_name,
                        table_name=tname,
                        is_unique=is_unique,
                        columns=idx_cols
                    )
                    indexes.append(idx_schema)
                    schema.indexes.append(idx_schema)

                table_schema = TableSchema(
                    name=tname,
                    columns=columns,
                    primary_key=primary_keys,
                    row_count=row_count,
                    indexes=indexes
                )
                schema.tables[tname] = table_schema

        return schema
