from app.ingest import CHUNK_CHARS, chunk, html_tables_to_markdown
from app.rag import _fts_query


def test_html_table_becomes_pipe_table():
    html = "Intro\n<table><thead><tr><th>Desc</th><th>Tot</th></tr></thead>" \
           "<tbody><tr><td>Licenza</td><td>1.200,00</td></tr></tbody></table>\nEnd"
    assert html_tables_to_markdown(html) == "Intro\n| Desc | Tot |\n| --- | --- |\n| Licenza | 1.200,00 |\nEnd"


def test_ragged_rows_are_padded_and_pipes_escaped():
    out = html_tables_to_markdown("<table><tr><td>a|b</td><td>c</td></tr><tr><td>d</td></tr></table>")
    assert out.splitlines() == ["| a\\|b | c |", "| --- | --- |", "| d |  |"]


def test_chunks_respect_size_and_keep_all_text():
    text = "\n\n".join(f"Paragrafo {i} " + "parola " * 40 for i in range(30))
    chunks = chunk(text)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_CHARS * 2 for c in chunks)
    assert all(f"Paragrafo {i} " in "".join(chunks) for i in range(30))


def test_fts_query_quotes_terms_and_keeps_codes():
    assert _fts_query('fattura 2026/0417 "Rossi"?') == '"fattura" OR "2026/0417" OR "Rossi"'
    assert _fts_query("?!") is None
