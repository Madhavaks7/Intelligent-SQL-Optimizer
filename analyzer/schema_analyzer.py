from typing import List, Optional, Tuple, Dict
from models.analysis_models import DatabaseSchema, TableSchema, IndexSchema

class SchemaAnalyzer:
    """
    Analyzes query requirements against database schema metadata and existing index definitions.
    """

    def __init__(self, schema: DatabaseSchema):
        self.schema = schema

    def resolve_table_name(self, table_alias_or_name: str, tables_in_query: Optional[List[Any]] = None) -> Optional[str]:
        """
        Maps a table name or alias used in a query to its actual canonical table name in the schema.
        """
        if not table_alias_or_name:
            return None

        target = table_alias_or_name.lower().strip()

        # 1. Check against tables in query (aliases or table names)
        if tables_in_query:
            for t in tables_in_query:
                t_name = getattr(t, "name", str(t))
                t_alias = getattr(t, "alias", None)

                if t_alias and t_alias.lower() == target:
                    # Check if t_name exists in schema
                    for schema_tname in self.schema.tables:
                        if schema_tname.lower() == t_name.lower():
                            return schema_tname
                    return t_name

                if t_name and t_name.lower() == target:
                    for schema_tname in self.schema.tables:
                        if schema_tname.lower() == t_name.lower():
                            return schema_tname
                    return t_name

        # 2. Direct match in schema tables
        for tname in self.schema.tables:
            if tname.lower() == target:
                return tname

        return None

    def get_table_schema(self, table_name: str) -> Optional[TableSchema]:
        """Returns TableSchema for given table name (case-insensitive)."""
        for tname, tschema in self.schema.tables.items():
            if tname.lower() == table_name.lower():
                return tschema
        return None

    def has_index_on_columns(self, table_name: str, target_columns: List[str]) -> Tuple[bool, Optional[str], bool]:
        """
        Checks if an index exists on table_name starting with target_columns.
        Returns (has_index, index_name, is_exact_prefix_match).
        """
        tschema = self.get_table_schema(table_name)
        if not tschema or not target_columns:
            return False, None, False

        target_cols_lower = [c.lower() for c in target_columns]

        # 1. Check Primary Key (SQLite automatically creates unique B-tree index on PK)
        if len(target_cols_lower) == 1 and target_cols_lower[0] in [pk.lower() for pk in tschema.primary_key]:
            return True, f"PRIMARY KEY ({tschema.primary_key[0]})", True

        # 2. Check Secondary Indexes
        for idx in tschema.indexes:
            idx_cols_lower = [c.lower() for c in idx.columns]
            
            # Prefix match check
            if len(idx_cols_lower) >= len(target_cols_lower):
                if idx_cols_lower[:len(target_cols_lower)] == target_cols_lower:
                    return True, idx.name, True

            # Partial match check (target columns are contained in index, even if ordering is slightly different)
            if set(target_cols_lower).issubset(set(idx_cols_lower)):
                return True, idx.name, False

        return False, None, False

    def is_column_in_table(self, table_name: str, column_name: str) -> bool:
        """Checks if a column exists in the table schema."""
        tschema = self.get_table_schema(table_name)
        if not tschema or not column_name:
            return False
        return any(c.name.lower() == column_name.lower().strip() for c in tschema.columns)

    def validate_index_columns(self, table_alias_or_name: str, columns: List[str], tables_in_query: Optional[List[Any]] = None) -> Tuple[bool, Optional[str]]:
        """
        Validates whether table exists and ALL proposed index columns are real physical columns.
        Returns (is_valid, canonical_table_name).
        """
        canonical_tname = self.resolve_table_name(table_alias_or_name, tables_in_query)
        if not canonical_tname:
            return False, None

        for col in columns:
            if not self.is_column_in_table(canonical_tname, col):
                return False, None

        return True, canonical_tname

