from abc import ABC, abstractmethod


class DatabaseAdapter(ABC):
    sql_dialect: str  # Injected into the SQL generation prompt

    @abstractmethod
    def execute_query(self, sql: str) -> dict:
        """Execute SQL. Returns {"columns": [...], "rows": [...]}."""

    @abstractmethod
    def fetch_distinct_values(self, table: str, column: str, limit: int = 200) -> list:
        """Return distinct non-null values for a column (used by vector builder)."""
