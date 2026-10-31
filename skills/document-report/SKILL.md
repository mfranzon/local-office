---
name: document-report
description: Write a Word report that summarizes the documents about a topic, client or project, with sources. Use when the user asks for a report, a brief, a dossier or a summary document to download.
---

# Document report

1. Gather material: `search_documents` with two or three different queries about the topic, and `wiki_page` for the companies or people involved.
2. Read the most relevant documents in full with `read_document` (at most 6).
3. Write the report in Markdown, in the user's language:
   - `# Title`, then a short summary paragraph.
   - `## Key facts` as a bullet list (amounts, dates, parties).
   - `## Open points and deadlines`.
   - `## Sources`: one line per document used.
4. Call `create_file` with `format: "docx"`, a short filename and the report as `content`.
5. In the chat, give a three-line summary and cite sources with their numbers.
