import sqlite3
import shutil
import tempfile
import time
import os
from typing import List, Dict, Any, Tuple, Optional

class DatabaseManager:
    """
    Manages connections, execution, safety checks, and temporary benchmarking databases.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_connection(self) -> sqlite3.Connection:
        """Returns a connection to the database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def execute_query(self, query: str, params: Tuple = ()) -> Tuple[List[Dict[str, Any]], float]:
        """
        Executes a SELECT query and returns (rows as dicts, execution_time_ms).
        """
        rows, _, elapsed_ms = self.execute_query_with_columns(query, params)
        return rows, elapsed_ms

    def execute_query_with_columns(self, query: str, params: Tuple = ()) -> Tuple[List[Dict[str, Any]], List[str], float]:
        """
        Executes a SELECT query and returns (rows as dicts, column_names, execution_time_ms).
        """
        start = time.perf_counter()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            dict_rows = [dict(row) for row in rows]
            return dict_rows, columns, elapsed_ms


    def explain_query_plan(self, query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        """
        Executes EXPLAIN QUERY PLAN <query> and returns raw plan records.
        """
        explain_sql = f"EXPLAIN QUERY PLAN {query}"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(explain_sql, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def create_benchmark_copy(self) -> Tuple[str, 'DatabaseManager']:
        """
        Creates an isolated temporary copy of the SQLite database for benchmark testing.
        Returns (temp_db_path, temp_db_manager).
        """
        temp_fd, temp_db_path = tempfile.mkstemp(suffix=".db", prefix="sqlo_bench_")
        os.close(temp_fd)
        shutil.copy2(self.db_path, temp_db_path)
        temp_mgr = DatabaseManager(temp_db_path)
        return temp_db_path, temp_mgr

    @staticmethod
    def cleanup_temp_db(temp_db_path: str):
        """Removes the temporary benchmark database."""
        if temp_db_path and os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass

    @staticmethod
    def is_safe_read_query(query: str) -> Tuple[bool, Optional[str]]:
        """
        Checks if the SQL statement is a safe read-only query (SELECT).
        Blocks destructive or database-altering DDL/DML statements.
        """
        clean = query.strip().upper()
        # Disallow multi-statement execution if semicolon is in middle
        statements = [s.strip() for s in clean.split(";") if s.strip()]
        if len(statements) > 1:
            return False, "Multiple SQL statements are not permitted."

        first_word = clean.split()[0] if clean else ""
        dangerous_keywords = {"DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "ATTACH", "DETACH", "PRAGMA"}
        
        if first_word in dangerous_keywords:
            return False, f"Modifying query detected ({first_word}). Only SELECT queries are permitted for safety."
        
        if not clean.startswith("SELECT") and not clean.startswith("WITH") and not clean.startswith("EXPLAIN"):
            return False, f"Statement type '{first_word}' is not permitted. Only SELECT queries are allowed."

        return True, None


def validate_sqlite_db(file_path: str) -> Tuple[bool, Optional[str]]:
    """
    Validates whether the specified file is a valid, readable SQLite database.
    Returns (is_valid, error_message).
    """
    if not file_path or not os.path.exists(file_path):
        return False, "File does not exist."

    if os.path.getsize(file_path) == 0:
        return False, "Uploaded file is empty (0 bytes)."

    # Verify SQLite header magic string
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
            if header != b"SQLite format 3\x00":
                return False, "The uploaded file does not appear to be a valid SQLite database."
    except Exception as e:
        return False, f"Could not read file header: {str(e)}"

    # Attempt SQLite connection and schema check
    try:
        conn = sqlite3.connect(f"file:{file_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM sqlite_master;")
        cursor.fetchone()
        conn.close()
    except Exception as e:
        return False, f"SQLite validation failed: {str(e)}"

    return True, None

