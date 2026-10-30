from datetime import date, timedelta

import pytest

from app import db as dbmod
from app import wiki, wiki_views

TEXT = """FATTURA N. 2026/0417
Data: 15/10/2026
Fornitore: Officine Rossi S.r.l. - P.IVA 01234567890
Cliente: Studio Bianchi S.r.l., Trieste
Pagamento entro il 30/11/2026."""


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    dbmod.init()
    with dbmod.db() as c:
        c.execute("INSERT INTO documents(id, name, ext, size, status) VALUES ('d1','fattura.pdf','.pdf',1,'ready')")
        c.execute("INSERT INTO documents(id, name, ext, size, status) VALUES ('d2','ordine.pdf','.pdf',1,'ready')")
        yield c


def ent(name, etype="organization", role=None, facts=()):
    return {"type": etype, "name": name, "role": role, "facts": [dict(key=k, value=v, page=1) for k, v in facts]}


def extraction(**kw):
    return {"entities": [], "relations": [], "events": [], "deadlines": [], **kw}


def test_name_norm_ignores_legal_form_and_punctuation():
    assert wiki.name_norm("Officine Rossi S.r.l.") == wiki.name_norm("OFFICINE ROSSI SRL") == "officine rossi"
    assert wiki.name_norm("Città Nuova S.p.A.") == "citta nuova"


@pytest.mark.parametrize("iso,ok", [("2026-10-15", True), ("2026-11-30", True), ("2026-01-01", False), ("2026-13-01", False)])
def test_date_must_appear_in_text(iso, ok):
    assert wiki.date_in_text(iso, TEXT) is ok


def test_date_formats_and_optional_year():
    assert wiki.date_in_text("2026-05-31", "richieste entro il 31 maggio", year_optional=True)
    assert not wiki.date_in_text("2026-05-31", "richieste entro il 31 maggio")
    assert wiki.date_in_text("2026-05-31", "scade il 31.05.26")


def test_merge_drops_invented_entities_ids_and_dates(conn):
    res = extraction(
        entities=[ent("Officine Rossi S.r.l.", role="supplier", facts=[("vat_id", "IT 01234567890"), ("iban", "IT60X0542811101000000123456")]),
                  ent("Mario Bianchi", "person")],                                     # not in the text
        events=[{"date": "2026-10-15", "what": "Fattura 2026/0417", "entities": ["Officine Rossi"], "page": 1}],
        deadlines=[{"kind": "payment", "date": "2026-11-30", "what": "Pagamento", "entities": ["Officine Rossi S.r.l."], "page": 9},
                   {"kind": "payment", "date": "2026-12-31", "what": "Inventata", "entities": [], "page": 1}])
    counts = wiki.merge(conn, "d1", 1, TEXT, [res])
    assert [e["name"] for e in conn.execute("SELECT name FROM entities")] == ["Officine Rossi S.r.l."]
    facts = {(f["kind"], f["key"], f["norm"] if f["kind"] == "attr" else f["date"]) for f in conn.execute("SELECT * FROM facts")}
    assert ("attr", "vat_id", "01234567890") in facts                   # 'IT' prefix and spaces normalized
    assert not any(k == "iban" for _, k, _ in facts)                    # IBAN not in the text -> dropped
    assert ("event", "event", "2026-10-15") in facts and ("deadline", "deadline", "2026-11-30") in facts
    assert not any(d == "2026-12-31" for _, _, d in facts)              # deadline date not in the text
    assert conn.execute("SELECT MAX(page) FROM facts").fetchone()[0] == 1   # page clamped to the document
    assert counts["created"] == ["Officine Rossi S.r.l."]


def test_same_vat_resolves_to_same_entity_and_conflicts_are_flagged(conn):
    wiki.merge(conn, "d1", 1, TEXT, [extraction(entities=[ent("Officine Rossi S.r.l.", facts=[("vat_id", "01234567890")])])])
    other = "Rossi Officine Meccaniche - P.IVA 01234567890 - Officine Rossi S.r.l. P.IVA 09999999990"
    wiki.merge(conn, "d2", 1, other, [extraction(entities=[
        ent("Rossi Officine Meccaniche", facts=[("vat_id", "01234567890")]),
        ent("Officine Rossi S.r.l.", facts=[("vat_id", "09999999990")])])])
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1   # matched by VAT, then by alias
    [c] = wiki_views.conflicts_for(conn)
    assert c["key"] == "vat_id" and {o["value"] for o in c["options"]} == {"01234567890", "09999999990"}


def test_similar_names_are_suggested_as_duplicates(conn):
    wiki.merge(conn, "d1", 1, "Rossi srl e Officine Rossi", [extraction(entities=[ent("Rossi srl"), ent("Officine Rossi")])])
    [d] = wiki_views.duplicates(conn)
    assert {d["a"]["name"], d["b"]["name"]} == {"Rossi srl", "Officine Rossi"}


def test_rejected_facts_and_ignored_names_stay_out(conn):
    res = [extraction(entities=[ent("Officine Rossi S.r.l.", facts=[("vat_id", "01234567890")]), ent("Studio Bianchi S.r.l.")])]
    wiki.merge(conn, "d1", 1, TEXT, res)
    eid = conn.execute("SELECT id FROM entities WHERE name LIKE 'Officine%'").fetchone()[0]
    conn.execute("INSERT INTO rejected_facts VALUES (?, 'vat_id', '01234567890')", (eid,))
    conn.execute("INSERT INTO ignored_names VALUES ('studio bianchi')")
    conn.execute("DELETE FROM facts")
    wiki.merge(conn, "d1", 1, TEXT, res)
    assert conn.execute("SELECT COUNT(*) FROM facts WHERE key='vat_id'").fetchone()[0] == 0
    assert not conn.execute("SELECT 1 FROM facts f JOIN entities e ON e.id=f.entity_id WHERE e.name LIKE 'Studio Bianchi%'").fetchone()


def test_deadlines_list_entities_with_commas_in_their_names(conn):
    due = (date.today() + timedelta(days=5)).isoformat()
    conn.execute("INSERT INTO entities(id, type, name, slug) VALUES (1, 'person', 'ROSSI, MARIO MR', 'rossi')")
    for entity_id in (1, None):   # one fact about the person, one about the document itself
        conn.execute("INSERT INTO facts(entity_id, doc_id, page, kind, key, value, norm, date) "
                     "VALUES (?, 'd1', 1, 'deadline', 'deadline', 'Gate closes', 'gate closes', ?)", (entity_id, due))
    [d] = wiki_views.deadlines(conn)
    assert d["entities"] == [{"id": 1, "name": "ROSSI, MARIO MR"}]


TICKET = """BOARDING PASS - ROSSI, MARIO MR
Flight KDL 1234 from Venice Marco Polo (VCE) to Basel-Mulhouse-Freiburg (BSL), 26/10/2026, departure 12:40
Gate closes 12:10. Booking reference ABC123."""


def test_places_and_ticket_times_stay_out_and_person_names_are_cleaned(conn):
    res = extraction(
        entities=[ent("ROSSI, MARIO MR", "person", role="passenger"), ent("KDL", role="airline"),
                  ent("Venice Marco Polo", "place"), ent("Basel-Mulhouse-Freiburg", "place")],
        events=[{"date": "2026-10-26", "what": "Flight KDL 1234", "entities": ["ROSSI, MARIO MR", "KDL"], "page": 1}] * 2,
        deadlines=[{"kind": "schedule", "date": "2026-10-26", "what": "Gate closes 12:10",
                    "entities": ["ROSSI, MARIO MR"], "page": 1}])
    wiki.merge(conn, "d1", 1, TICKET, [res])
    assert sorted(r[0] for r in conn.execute("SELECT name FROM entities")) == ["KDL", "Mario Rossi"]
    assert conn.execute("SELECT COUNT(*) FROM facts WHERE kind='deadline'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM facts WHERE kind='event'").fetchone()[0] == 2   # one per entity, not repeated
    # the same person written normally in another document lands on the same page
    wiki.merge(conn, "d2", 1, "Expense report by Mario Rossi", [extraction(entities=[ent("Mario Rossi", "person")])])
    assert conn.execute("SELECT COUNT(*) FROM entities WHERE type='person'").fetchone()[0] == 1


@pytest.mark.parametrize("raw,clean", [("ROSSI, MARIO MR", "Mario Rossi"), ("Dott.ssa Giulia Ferri", "Giulia Ferri"),
                                       ("Sarah Collins", "Sarah Collins"), ("ROSSI MARIO", "Rossi Mario")])
def test_person_name(raw, clean):
    assert wiki.person_name(raw) == clean


def test_existing_raw_person_names_are_cleaned_on_next_ingest(conn):
    conn.execute("INSERT INTO entities(id, type, name, slug) VALUES (1, 'person', 'ROSSI, MARIO MR', 'rossi')")
    conn.execute("INSERT INTO aliases(norm, entity_id) VALUES ('rossi mario mr', 1)")
    wiki.merge(conn, "d1", 1, TICKET, [extraction(entities=[ent("ROSSI, MARIO MR", "person")])])
    assert conn.execute("SELECT name FROM entities").fetchone()[0] == "Mario Rossi"


def test_departments_and_stale_pages_are_not_linked(conn):
    # an old page (e.g. an airport from before places were filtered) must not be revived by an event naming it
    conn.execute("INSERT INTO entities(id, type, name, slug) VALUES (9, 'organization', 'Venice Marco Polo', 'vce')")
    conn.execute("INSERT INTO aliases(norm, entity_id) VALUES ('venice marco polo', 9)")
    res = extraction(entities=[ent("ROSSI, MARIO MR", "person"), ent("HR", role="department")],
                     events=[{"date": "2026-10-26", "what": "Flight", "entities": ["Venice Marco Polo", "Mario Rossi"], "page": 1}])
    wiki.merge(conn, "d1", 1, TICKET + " Questions to HR.", [res])
    wiki.cleanup(conn)
    assert [r[0] for r in conn.execute("SELECT name FROM entities")] == ["Mario Rossi"]
    assert conn.execute("SELECT COUNT(*) FROM facts WHERE kind='event'").fetchone()[0] == 1
