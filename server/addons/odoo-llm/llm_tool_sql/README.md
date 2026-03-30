# LLM Tool: SQL Query

AI-powered safe read-only SQL query tool for Odoo database.

## Overview

This module provides an `execute_sql_query` tool that allows AI assistants
(via the Odoo LLM framework) to run safe, read-only SELECT queries against
the Odoo PostgreSQL database and return structured JSON results.

## Installation

1. Install the `llm_tool` base module first
2. Install this module (`llm_tool_sql`) from the Odoo Apps menu
3. Go to **LLM > Configuration > Tools** and verify `execute_sql_query` is listed
4. Assign the tool to your AI assistant

## Usage

Once installed and assigned to an assistant, users can ask database questions
in natural language:

- "How many products do we have?"
- "Show me the top 5 customers by name"
- "What are the sales order states and their counts?"

The AI will automatically generate and execute the appropriate SQL query,
then respond with a human-friendly answer.

## Security

- **SELECT only** — only SELECT and WITH (CTE) statements are allowed
- **Keyword blocking** — INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE,
  CREATE, GRANT, REVOKE, EXECUTE, COPY, LOAD are all blocked
- **Row limiting** — maximum 100 rows per query (auto-enforced)
- **SAVEPOINT isolation** — query errors roll back safely without
  affecting the Odoo transaction
- **Access control** — managers get full access, regular users get read-only

## Response Format

Single-row results are flattened for easy LLM consumption:

```json
{"count": 42}
```

Multi-row results include column names as keys:

```json
{
  "rows": [
    {"id": 1, "name": "Desk", "list_price": 150.0},
    {"id": 2, "name": "Chair", "list_price": 75.0}
  ],
  "row_count": 2
}
```

Errors return a clear message:

```json
{"error": "Only SELECT queries are allowed."}
```

## Dependencies

- `llm_tool` (LLM Tool base module)

## Author

Mojidara Vivek

## License

LGPL-3
