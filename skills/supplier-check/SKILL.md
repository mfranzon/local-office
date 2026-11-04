---
name: supplier-check
description: Check a supplier before a renewal, a payment or a new order - contract terms, invoices, open deadlines, data that does not match, plus public news when a web search service is connected. Use when the user asks to check, review or vet a supplier or vendor.
---

# Supplier check

1. Call `wiki_page` with the supplier's name. Note its VAT numbers, people and any conflicting values.
2. Call `search_documents` with "<supplier> contract renewal cancellation", then "<supplier> invoice total due", then "<supplier> order". Read the contract and each invoice or order found with `read_document` (at most 6).
3. Call `list_deadlines` with `days: 90` and keep the deadlines that involve the supplier.
4. Compare the documents with each other. Flag every mismatch: a VAT number, name or address written differently, an amount or due date that breaks the contract's payment terms, an order after the contract's end.
5. Only if a tool marked "external service" can search the web: call it once with the supplier's full legal name and the words "news" and the current year. Send nothing else from the documents (no amounts, no people). Keep only results about this exact company. If there is no such tool, skip this step and say at the end that connecting Web search in Settings adds public news.
6. Answer in the user's language, headings included. Start directly with the summary heading and write nothing after the last section:
   - `## Summary`: two sentences, ending with a recommendation (renew, renegotiate, check first) and why.
   - `## Contract`: parties, start, end, renewal and cancellation terms, payment terms.
   - `## Money`: a table Invoice/Order | Date | Amount | Due | Source, then the total of the invoices only (open orders are not invoiced yet).
   - `## Deadlines`: one line per supplier deadline from `list_deadlines`, with its date copied exactly, the nearest first; add **urgent** to each line due within 30 days of today.
   - `## Issues to check`: only real mismatches from step 4, each with both values and both sources. Leave out checks that passed. Write "None found" if there are none.
   - `## Public news`: what the web search found, or "No public news found". Never mix web results with document facts.

Cite every fact with its source number. Copy numbers, dates and VAT numbers exactly as written.
