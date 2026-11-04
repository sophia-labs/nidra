# SPDX-License-Identifier: Apache-2.0
"""The write seam: op builders, the ``NIDRA_WRITES`` switch, the SPARQL rendering
and the ledger transport, import compensation, the no-ledger refusal.

The pure tests need nothing. The live tests run against a real gardend whose
commit seam is wired (``GARDEND_URL`` / ``GARDEND_TOKEN``; each test makes its
own graph) and, for the import, ``CATALOG_READ_BACKEND=rdf``:

    set -a; . ./.env; set +a
    export CONNECTION_STRINGS="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:$POSTGRES_PORT/pagila,sqlite:///$PWD/dev_tools/fixtures/sql/chinook.sqlite"
    CATALOG_READ_BACKEND=rdf AO_RDF_STRICT=1 POSTGRES_PORT=1 \\
    GARDEND_URL=http://127.0.0.1:7693 GARDEND_TOKEN=... \\
        uv run --no-sync pytest auto_ontology/rdf/tests/test_commit_seam.py -q

(``CONNECTION_STRINGS`` as ``nidra/closure.sh`` sets it: the import resolves dialects
through the catalogued sources.)

Without ``GARDEND_URL`` the live tests fail (they are never skipped: a skipped
test is not a pass).
"""
from __future__ import annotations

import http.server
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from auto_ontology.rdf import writes as W
from auto_ontology.rdf.client import RDF_JSON, GardendClient, lit, use_client
from auto_ontology.rdf.vocab import DCT_DESCRIPTION, SKOS_PREF_LABEL, XSD, prop_iri, ref_iri, row_iri_single

FIXTURE_NT = Path(__file__).resolve().parents[3] / "dev_tools" / "fixtures" / "sql" / "catalog_fixture.nt"
MODES = ("sparql", "commit")


# -- helpers -------------------------------------------------------------------


def _live() -> None:
    if not os.environ.get("GARDEND_URL") or not os.environ.get("GARDEND_TOKEN"):
        raise AssertionError("live test: set GARDEND_URL and GARDEND_TOKEN (a gardend with the commit seam)")


def _fresh(tag: str) -> GardendClient:
    _live()
    c = GardendClient(graph_id=f"seam-{tag}-{uuid.uuid4().hex[:8]}")
    c.ensure_graph(f"seam test {tag}")
    return c


@pytest.fixture
def mode_graph(request):
    """``(mode, client)``: the write mode pinned, ``client()`` routed to a fresh graph."""
    mode = request.param
    c = _fresh(mode)
    W.set_write_mode(mode)
    try:
        with use_client(c):
            yield mode, c
    finally:
        W.set_write_mode(None)


def _triples(c: GardendClient) -> set[tuple[str, str, str]]:
    g = W.data_graph_iri(c.graph_id)
    rows = c.select_raw(f"SELECT ?s ?p ?o WHERE {{ GRAPH <{g}> {{ ?s ?p ?o }} }}")
    return {(r["s"], r["p"], r["o"]) for r in rows}


def _values(c: GardendClient, subject: str, predicate: str) -> list[str]:
    g = W.data_graph_iri(c.graph_id)
    rows = c.select_raw(f"SELECT ?o WHERE {{ GRAPH <{g}> {{ <{subject}> <{predicate}> ?o }} }}")
    return sorted(r["o"] for r in rows)


def _oracle(c: GardendClient) -> dict:
    """The ledger's own oracle: rebuild the owned subjects in memory, compare with the sink."""
    response = c._http.post(f"/emporium/fold/{c.graph_id}/ao/replay", json={"into": "oracle"})
    assert response.status_code == 200, response.text[:300]
    return response.json()


# -- pure: terms and op builders --------------------------------------------------

_VALUES = [
    "plain", "", 'quote " and \\ backslash\nnewline\ttab', "ünïcødé ✓", True, False, 0, -7, 12345678901234,
    1.5, 0.1, 1e20, datetime(2026, 11, 4, 9, 52, 1, 123456, tzinfo=timezone.utc), datetime(2026, 1, 2, 3, 4, 5),
    ["a", "b"], [], {"k": [1, 2], "s": "x"}, [["a", "b"], ["c"]],
]


@pytest.mark.parametrize("value", _VALUES, ids=lambda v: type(v).__name__ + ":" + repr(v)[:20])
def test_term_is_the_literal_lit_writes(value):
    assert W.term_nt(W.term(value)) == lit(value)
    assert W.term_nt(W.nt_term(lit(value))) == lit(value)


def test_row_ops_datatypes_mirrors_and_list_columns():
    row = {
        "id": "t1", "name": "Film", "description": "A film", "synonyms": ["movie", "picture"],
        "name_certified": False, "created": datetime(2026, 11, 4, tzinfo=timezone.utc), "owner_id": None,
    }
    ops = W.row_ops("term", row)
    by_pred = {op["predicate"]: op for op in ops}
    subject = row_iri_single("term", "t1")
    assert all(op["op"] == "set" and op["subject"] == subject and op["class"] == "urn:ao:term" for op in ops)
    assert by_pred[prop_iri("term", "name")]["values"] == ["Film"]
    assert by_pred[SKOS_PREF_LABEL]["values"] == ["Film"]
    assert by_pred[DCT_DESCRIPTION]["values"] == ["A film"]
    assert by_pred[prop_iri("term", "name_certified")]["values"] == [{"@value": "false", "@type": f"{XSD}boolean"}]
    assert by_pred[prop_iri("term", "created")]["values"] == [{"@value": "2026-11-04T00:00:00+00:00", "@type": f"{XSD}dateTime"}]
    # a list column is ONE set: the rdf:JSON whole plus one value per member
    syn = [op for op in ops if op["predicate"] == prop_iri("term", "synonyms")]
    assert len(syn) == 1
    assert syn[0]["values"] == [{"@value": '["movie", "picture"]', "@type": RDF_JSON}, "movie", "picture"]
    assert prop_iri("term", "owner_id") not in by_pred  # NULL on insert: nothing


def test_fk_column_sets_the_ref_link_and_update_none_clears_everything():
    ops = W.row_ops("catalog_schema", {"id": "s1", "database_id": "d1", "name": "public"})
    link = [op for op in ops if op["predicate"] == ref_iri("catalog_schema", "database_id")]
    assert link == [W.set_op(row_iri_single("catalog_schema", "s1"), "catalog_schema", ref_iri("catalog_schema", "database_id"),
                             [{"@id": row_iri_single("catalog_database", "d1")}])]
    cleared = W.update_ops("catalog_schema", row_iri_single("catalog_schema", "s1"), {"database_id": None, "name": None})
    assert {op["predicate"]: op["values"] for op in cleared} == {
        prop_iri("catalog_schema", "database_id"): [],
        ref_iri("catalog_schema", "database_id"): [],
        prop_iri("catalog_schema", "name"): [],
        SKOS_PREF_LABEL: [],
    }


def test_ntriples_ops_one_set_per_predicate():
    lines = [
        '<urn:ao:term/id=1> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:ao:term> .',
        '<urn:ao:term/id=1> <urn:ao:term#synonyms> "[\\"a\\"]"^^<http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON> .',
        '<urn:ao:term/id=1> <urn:ao:term#synonyms> "a" .',
        '<urn:ao:term/id=1> <urn:ao:term#name_certified> "true"^^<http://www.w3.org/2001/XMLSchema#boolean> .',
    ]
    ops = W.ntriples_ops(lines)
    assert ops == [
        {"op": "set", "subject": "urn:ao:term/id=1", "class": "urn:ao:term", "predicate": "urn:ao:term#synonyms",
         "values": [{"@value": '["a"]', "@type": RDF_JSON}, "a"]},
        {"op": "set", "subject": "urn:ao:term/id=1", "class": "urn:ao:term", "predicate": "urn:ao:term#name_certified",
         "values": [{"@value": "true", "@type": f"{XSD}boolean"}]},
    ]


# -- pure: the switch --------------------------------------------------------------


def test_switch_reads_nidra_writes_once(monkeypatch):
    try:
        W.set_write_mode(None)
        monkeypatch.delenv("NIDRA_WRITES", raising=False)
        assert W.write_mode() == "sparql"  # the default
        monkeypatch.setenv("NIDRA_WRITES", "commit")
        assert W.write_mode() == "sparql"  # read once per process
        W.set_write_mode(None)
        assert W.write_mode() == "commit"
        monkeypatch.setenv("NIDRA_WRITES", "postgres")
        W.set_write_mode(None)
        with pytest.raises(ValueError):
            W.write_mode()
    finally:
        W.set_write_mode(None)


def test_txn_named_commit_dash_is_refused():
    with pytest.raises(ValueError):
        W.commit([W.retract_op("term", "urn:ao:term/id=x")], txn="commit-1")


def test_identities(monkeypatch):
    monkeypatch.setenv("NIDRA_ORIGIN", "engine:tenant-a")
    monkeypatch.delenv("NIDRA_WITNESS", raising=False)
    assert W.writer_id() == "engine:tenant-a"
    assert W.current_witness() == "engine:tenant-a"
    with W.witness_scope("user-42"):
        assert W.current_witness() == "user-42"
    assert W.current_witness() == "engine:tenant-a"


# -- the no-ledger refusal ------------------------------------------------------------


class _NotFound(http.server.BaseHTTPRequestHandler):
    """A gardend without the ledger, as far as the route goes: every path is a bare 404."""

    def _404(self):
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()

    do_GET = do_POST = _404

    def log_message(self, *args):  # quiet
        pass


def test_no_ledger_fails_loudly_at_startup_and_at_first_write():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _NotFound)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = GardendClient(url=f"http://127.0.0.1:{server.server_address[1]}", token="x", graph_id="g")
        W.set_write_mode("commit")
        with use_client(c):
            with pytest.raises(W.LedgerUnavailable, match="no Emporium commit route"):
                W.startup_check()
            with pytest.raises(W.LedgerUnavailable):
                W.insert_row("tag", {"name": "pii"})
        W.set_write_mode("sparql")
        with use_client(c):
            W.startup_check()  # sparql mode never asks
    finally:
        W.set_write_mode(None)
        server.shutdown()


def test_missing_graph_is_not_a_missing_ledger():
    _live()
    c = GardendClient(graph_id=f"seam-absent-{uuid.uuid4().hex[:8]}")
    assert W.check_ledger(c).get("missing") is True


# -- live: both modes leave the same graph ------------------------------------------------


@pytest.mark.parametrize("mode_graph", MODES, indirect=True)
def test_list_column_rewrites_leave_one_json_whole(mode_graph):
    mode, c = mode_graph
    term = W.insert_row("term", {"name": "Film", "synonyms": ["movie", "picture"]})
    subject = row_iri_single("term", term["id"])
    for synonyms in (["a", "b", "c"], ["c"], ["x", "y"], []):
        W.update_row("term", term["id"], {"synonyms": synonyms})
        found = _values(c, subject, prop_iri("term", "synonyms"))
        wholes = [v for v in found if v.endswith(f"^^<{RDF_JSON}>")]
        assert wholes == [lit(synonyms)], (mode, synonyms, found)
        assert sorted(v for v in found if v not in wholes) == sorted(lit(s) for s in set(synonyms))
    if mode == "commit":
        assert _oracle(c)["equal"] is True


@pytest.mark.parametrize("mode_graph", MODES, indirect=True)
def test_fk_links_cascade_and_foreign_links(mode_graph):
    mode, c = mode_graph
    db = W.insert_row("catalog_database", {"name": "db1", "dialect": "postgres"})
    schema = W.insert_row("catalog_schema", {"name": "public", "database_id": db["id"]})
    table = W.insert_row("catalog_table", {"name": "film", "schema_id": schema["id"], "pk": ["film_id"]})
    column = W.insert_row("catalog_column", {"name": "title", "table_id": table["id"]})
    tag = W.insert_row("tag", {"name": "pii"})
    W.insert_row("tag_target", {"tag_id": tag["id"], "column_id": column["id"]})
    s_schema = row_iri_single("catalog_schema", schema["id"])
    assert _values(c, s_schema, ref_iri("catalog_schema", "database_id")) == [f"<{row_iri_single('catalog_database', db['id'])}>"]
    # a foreign subject (the Nidra service's question record) links to the column
    s_column = row_iri_single("catalog_column", column["id"])
    c.update(f"INSERT DATA {{ GRAPH <{W.data_graph_iri(c.graph_id)}> {{ <urn:nidra:question/q1> <urn:nidra:column> <{s_column}> . "
             f"<urn:nidra:question/q1> <urn:nidra:question> \"why\" }} }}")
    W.delete_rows("catalog_database", [db["id"]])
    left = _triples(c)
    gone = {s_schema, row_iri_single("catalog_table", table["id"]), s_column, row_iri_single("catalog_database", db["id"])}
    assert not [t for t in left if t[0][1:-1] in gone or t[2][1:-1] in gone], mode
    # the tag survives, its target (CASCADE from the column) does not
    assert ("<" + row_iri_single("tag", tag["id"]) + ">", "<urn:ao:tag#name>", '"pii"') in left
    assert not [t for t in left if t[0].startswith("<urn:ao:tag_target/")]
    assert ("<urn:nidra:question/q1>", "<urn:nidra:question>", '"why"') in left
    if mode == "commit":
        assert _oracle(c)["equal"] is True


def _same_ops_both_modes(build) -> tuple[set, set]:
    out = []
    for mode in MODES:
        c = _fresh(f"parity-{mode}")
        W.set_write_mode(mode)
        try:
            with use_client(c):
                for ops in build():
                    W.commit(ops)
                out.append(_triples(c))
        finally:
            W.set_write_mode(None)
    return out[0], out[1]


def test_the_rendering_and_the_ledger_agree_op_for_op():
    s1, s2, s3 = (row_iri_single("term", i) for i in ("p1", "p2", "p3"))

    def build():
        yield W.row_ops("term", {"id": "p1", "name": "One", "synonyms": ["a"], "description": "d"})
        yield W.row_ops("term", {"id": "p2", "name": "Two"}) + W.row_ops("term", {"id": "p3", "name": "Three"})
        yield W.update_ops("term", s1, {"name": "Uno", "description": None, "synonyms": ["b", "c"]})
        yield [W.retract_op("term", s2)] + W.row_ops("term", {"id": "p2", "name": "Two again"})  # revive
        yield [W.retract_op("term", s3)]
        yield W.update_ops("term", s1, {"synonyms": []})

    sparql, commit = _same_ops_both_modes(build)
    assert sparql == commit
    assert ("<" + s1 + ">", "<urn:ao:term#name>", '"Uno"') in commit
    assert not [t for t in commit if t[0] == "<" + s3 + ">"]


@pytest.mark.parametrize("mode_graph", MODES, indirect=True)
def test_write_transaction_is_one_request_and_discards_on_failure(mode_graph):
    from auto_ontology.dal_rdf.session__writes import write_transaction

    mode, c = mode_graph
    tag = W.insert_row("tag", {"name": "a"})
    before = W.watermark(c)["version"] if mode == "commit" else None
    with write_transaction():
        W.update_row("tag", tag["id"], {"name": "b"})  # a read (the existence check) precedes it
        W.commit(W.update_ops("tag", row_iri_single("tag", tag["id"]), {"modified_by": "u1"}))
        W.commit(W.row_ops("tag", {"id": "t-new", "name": "c"}))
    if mode == "commit":
        after = W.watermark(c)["version"]
        assert sum(after.values()) - sum(before.values()) == 1  # ONE frame for the block
    with pytest.raises(RuntimeError):
        with write_transaction():
            W.commit(W.update_ops("tag", row_iri_single("tag", tag["id"]), {"name": "never"}))
            raise RuntimeError("the block fails")
    names = _values(c, row_iri_single("tag", tag["id"]), "urn:ao:tag#name")
    if mode == "commit":
        assert names == ['"b"']  # the pending op was discarded
    else:
        assert names == ['"never"']  # sparql mode: no rollback (documented)
    assert _values(c, row_iri_single("tag", "t-new"), "urn:ao:tag#name") == ['"c"']


def test_seeding_the_fixture_loaded_or_committed_is_the_same_graph():
    """``export.load``: ``/api/rdf/load`` under sparql, ``set`` ops under commit
    (every subject ledger-owned). Oxigraph canonicalizes some lexical forms
    (dateTime), so the two graphs are compared with each other."""
    from auto_ontology.rdf.export import load

    lines = FIXTURE_NT.read_text().splitlines()
    graphs = {}
    for mode in MODES:
        c = _fresh(f"seed-{mode}")
        W.set_write_mode(mode)
        try:
            with use_client(c):
                load(lines)
            graphs[mode] = (c, _triples(c))
        finally:
            W.set_write_mode(None)
    assert graphs["sparql"][1] == graphs["commit"][1]
    assert len(graphs["commit"][1]) == len({line for line in lines if line.strip()})
    report = _oracle(graphs["commit"][0])
    assert report["equal"] is True and report["triples"] == len(graphs["commit"][1])


# -- live: the import, compensated ------------------------------------------------------


@pytest.mark.parametrize("mode_graph", MODES, indirect=True)
def test_failed_import_is_compensated_to_the_pre_import_graph(mode_graph, caplog):
    """Replace-import the fixture's own export with one Term dropped (a delete stage),
    a new Term and SQL attribute (create stages) and a custom analysis whose SQL does
    not parse (the LAST stage fails). Everything written before is undone."""
    import yaml

    from auto_ontology.dal import model_interchange
    from auto_ontology.rdf.export import load
    from auto_ontology.rdf.patch import enabled
    from auto_ontology.server.model_interchange.schemas import AutoOntologyModelDocument, ExportRequest
    from auto_ontology.server.model_interchange.service import export_model

    assert enabled(), "run with CATALOG_READ_BACKEND=rdf"
    mode, c = mode_graph
    load(FIXTURE_NT.read_text().splitlines())
    document = yaml.safe_load(export_model(ExportRequest()))
    layer = document["semantic_layer"]
    dropped = {t["id"] for t in layer["terms"] if t["name"] == "Rental"}
    assert dropped
    gone_attrs = {a["id"] for t in layer["terms"] if t["id"] in dropped for a in t.get("columns_attributes", [])}
    layer["terms"] = [t for t in layer["terms"] if t["id"] not in dropped]
    layer["semantic_fks"] = [f for f in layer["semantic_fks"] if f["column_attribute_id"] not in gone_attrs]
    for key, items in layer["sql_attributes"].items():
        layer["sql_attributes"][key] = [a for a in items if a["term_id"] not in dropped]
    film = next(t for t in layer["terms"] if t["name"] == "Film")
    layer["terms"].append({"id": "comp-term", "name": "Screening", "description": "shown", "represents": film.get("represents", [])})
    layer["sql_attributes"].setdefault("manual", []).append(
        {"id": "comp-sqlattr", "name": "title length", "description": "chars", "sql": "SELECT film_id, length(title) AS n FROM film", "term_id": "comp-term"}
    )
    layer["custom_analyses"].append({"id": "comp-broken", "name": "broken", "description": "does not parse", "sql": "SELEC FROM WHERE ((("})

    before = _triples(c)
    frames_before = sum(W.watermark(c)["version"].values()) if mode == "commit" else None
    caplog.set_level("WARNING", logger="auto_ontology.rdf.writes")
    with pytest.raises(Exception) as raised:
        model_interchange.apply_import_model(AutoOntologyModelDocument.model_validate(document), replace=True)
    # the LAST stage (custom analyses) failed on its SQL, nothing else
    assert type(raised.value).__name__ == "SqlParseError", repr(raised.value)
    undo = [r.getMessage() for r in caplog.records if "compensating" in r.getMessage()]
    assert len(undo) == 1, undo
    # the catalog, delete, term, attribute and SQL-attribute stages had written
    assert int(undo[0].split()[2]) >= 6, (undo, repr(raised.value))
    after = _triples(c)
    assert after == before, (len(before - after), sorted(before - after)[:5], len(after - before), sorted(after - before)[:5])
    if mode == "commit":
        # the import did write (and the undo is lineage): the ledger grew, the sink is back
        assert sum(W.watermark(c)["version"].values()) > frames_before + 1
        assert _oracle(c)["equal"] is True
