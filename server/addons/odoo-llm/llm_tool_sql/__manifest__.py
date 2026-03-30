{
    "name": "LLM Tool: SQL Query",
    "version": "19.0.1.0.0",
    "category": "Productivity/LLM",
    "summary": "AI-powered safe read-only SQL query tool for Odoo database",
    "description": """
LLM Tool: SQL Query
====================

Provides an LLM tool (execute_sql_query) that allows AI assistants to
execute safe, read-only SELECT queries against the Odoo PostgreSQL database.

Features
--------
- Read-only SQL execution via AI chat
- LLM-friendly JSON response format
- Automatic row limiting (max 100 rows)
- CTE (WITH) query support

Security
--------
- Only SELECT and WITH (CTE) statements are allowed
- INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE are blocked
- SAVEPOINT-based execution for safe error rollback
- Automatic LIMIT enforcement to prevent context overflow

Response Format
---------------
- Single-row results: flat dict (e.g. {"count": 2})
- Multi-row results: {"rows": [...], "row_count": N}
- Errors: {"error": "description"}

Usage
-----
1. Install this module
2. Go to LLM > Configuration > Tools
3. Assign 'execute_sql_query' tool to your AI assistant
4. Ask questions like "How many products do we have?"
    """,
    "author": "Mojidara Vivek",
    "website": "https://github.com/anthropics/claude-code",
    "license": "LGPL-3",
    "depends": ["llm_tool"],
    "data": [
        "security/ir.model.access.csv",
    ],
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
