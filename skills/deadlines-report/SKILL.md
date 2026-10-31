---
name: deadlines-report
description: List upcoming deadlines (payments, deliveries, expiries, renewals) from documents, optionally as an Excel file. Use when the user asks what is due, what expires, or for a deadline report.
---

# Deadlines report

1. Call `list_deadlines` with `days` from the request (default 60).
2. If the user mentions a company or person, also call `wiki_page` for it.
3. Answer with a table: Date | What | Who | Source, sorted by date, overdue items first and marked as overdue.
4. If the user asks for a file or Excel, call `create_file` with `format: "xlsx"` and rows `["Date", "What", "Who", "Document"]` followed by one row per deadline.

Cite every row with its source number.
