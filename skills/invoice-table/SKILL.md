---
name: invoice-table
description: Extract invoice data (number, supplier, client, date, total, due date) from documents into a table or an Excel file. Use when the user asks to collect, list or export invoices.
---

# Invoice table

1. Find invoices: `list_documents` with `text: "invoice"`, then `text: "fattura"`; if the user names a supplier, use `search_documents` with the supplier name and "invoice".
2. Read each invoice with `read_document` (at most 15).
3. For each invoice collect: number, supplier, client, issue date, total with currency, due date. Copy values exactly as written; leave a cell empty when the value is not in the document.
4. Answer with a Markdown table and cite each row's source number.
5. If the user asks for Excel or a file, call `create_file` with `format: "xlsx"` and rows: the header `["Number", "Supplier", "Client", "Date", "Total", "Currency", "Due date", "Document"]` followed by one row per invoice (Total as a number).
