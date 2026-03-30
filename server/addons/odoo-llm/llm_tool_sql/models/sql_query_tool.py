"""LLM Tool: Safe SQL Query Executor

Allows the AI assistant to run read-only SELECT queries against the database.
All non-SELECT statements are strictly blocked for security.

Returns LLM-friendly JSON: single-row results are flattened to a dict,
multi-row results are returned as a list of dicts with column names as keys.
"""

import logging
import re
from datetime import date, datetime
from decimal import Decimal

from odoo import models

from odoo.addons.llm_tool.decorators import llm_tool

_logger = logging.getLogger(__name__)

# Statements that are absolutely forbidden
_BLOCKED_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|EXECUTE|EXEC|CALL"
    r"|COPY|LOAD|INTO\s+OUTFILE|INTO\s+DUMPFILE)\b",
    re.IGNORECASE,
)

# Maximum rows returned to the LLM to avoid context overflow
_MAX_ROWS = 100


def _make_serializable(value):
    """Convert non-JSON-serializable types to safe primitives."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, memoryview):
        return bytes(value).decode("utf-8", errors="replace")
    return value


class LLMSqlQueryTool(models.TransientModel):
    """Transient model providing a safe SQL query tool for LLM assistants."""

    _name = "llm.sql.query.tool"
    _description = "LLM SQL Query Tool"

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def execute_sql_query(self, query: str) -> dict:
        """Execute a read-only SQL SELECT query against the Odoo database.

        Use this tool when you need to look up data, count records, aggregate
        numbers, or inspect the database schema. Only SELECT queries are
        permitted; any attempt to modify data will be rejected.

        Args:
            query: A SQL SELECT statement, e.g.
                   "SELECT COUNT(*) as count FROM product_template"

        Returns:
            For single-row results: a flat dict like {"count": 2}
            For multi-row results: {"rows": [{"id": 1, "name": "Chair"}, ...], "row_count": 2}
            On error: {"error": "description"}

        Examples:
            execute_sql_query("SELECT COUNT(*) as count FROM product_template")
            → {"count": 2}

            execute_sql_query("SELECT id, name FROM res_partner LIMIT 3")
            → {"rows": [{"id": 1, "name": "My Company"}, {"id": 2, "name": "John"}], "row_count": 2}
        """
        return self._safe_execute(query)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _safe_execute(self, query: str) -> dict:
        """Validate and execute a SQL query, returning structured results."""
        # --- 1. Basic sanitation ---
        clean = query.strip().rstrip(";")
        if not clean:
            return {"error": "Empty query."}

        # --- 2. Must start with SELECT or WITH (CTE) ---
        first_word = clean.split()[0].upper()
        if first_word not in ("SELECT", "WITH"):
            return {
                "error": (
                    f"Only SELECT queries are allowed. "
                    f"Received a query starting with '{first_word}'."
                )
            }

        # --- 3. Block dangerous keywords anywhere in the query ---
        match = _BLOCKED_KEYWORDS.search(clean)
        if match:
            return {
                "error": (
                    f"Blocked: query contains forbidden keyword "
                    f"'{match.group().upper()}'. Only read-only queries are allowed."
                )
            }

        # --- 4. Enforce row limit ---
        if not re.search(r"\bLIMIT\b", clean, re.IGNORECASE):
            clean = f"{clean} LIMIT {_MAX_ROWS}"

        # --- 5. Execute inside a SAVEPOINT so any error is safely rolled back ---
        cr = self.env.cr
        try:
            cr.execute("SAVEPOINT llm_sql_tool")
            cr.execute(clean)
            columns = [desc[0] for desc in cr.description] if cr.description else []
            rows = cr.fetchall()
            cr.execute("RELEASE SAVEPOINT llm_sql_tool")
        except Exception as e:
            try:
                cr.execute("ROLLBACK TO SAVEPOINT llm_sql_tool")
            except Exception:
                pass  # savepoint may already be gone
            _logger.warning("LLM SQL tool query failed: %s – %s", clean, e)
            return {"error": str(e)}

        # --- 6. Convert to LLM-friendly JSON ---
        return self._format_result(columns, rows)

    def _format_result(self, columns: list, rows: list) -> dict:
        """Convert raw query results into LLM-friendly JSON.

        Single-row results are flattened to a dict (e.g. {"count": 2}).
        Multi-row results are returned as a list of named dicts.
        """
        if not rows:
            return {"rows": [], "row_count": 0}

        # Build list of dicts with column names as keys
        named_rows = [
            {col: _make_serializable(val) for col, val in zip(columns, row)}
            for row in rows
        ]

        # Single row → flatten to top-level dict for easy LLM consumption
        if len(named_rows) == 1:
            return named_rows[0]

        return {"rows": named_rows, "row_count": len(named_rows)}
