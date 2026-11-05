# SPDX-License-Identifier: Apache-2.0
"""The ledger adopts a catalog written by SPARQL (``auto_ontology.rdf.adopt``).

The pure tests need nothing. The live tests need a gardend with the ledger
(``GARDEND_URL`` / ``GARDEND_TOKEN``, as ``test_commit_seam.py``); each makes its
own graph, seeds it under ``NIDRA_WRITES=sparql`` and adopts it under ``commit``.
Without ``GARDEND_URL`` they fail (never skipped: a skipped test is not a pass).
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest

from auto_ontology.rdf import adopt as A
from auto_ontology.rdf import writes as W
from auto_ontology.rdf.client import GardendClient, use_client
from auto_ontology.rdf.vocab import DCT_DESCRIPTION, RDF_TYPE, SKOS_PREF_LABEL, prop_iri, row_iri_single

FIXTURE_NT = Path(__file__).resolve().parents[3] / "dev_tools" / "fixtures" / "sql" / "catalog_fixture.nt"
XSD_INT = "http://www.w3.org/2001/XMLSchema#integer"


# -- pure ---------------------------------------------------------------------------


def test_adoption_ops_carry_every_stored_value_and_count_what_they_cannot():
    term, vdb, ghost = "urn:ao:term/id=t1", "urn:ao:vdb/semantic_layer/x1", "urn:x:not-ao"
    triples = [
        (term, RDF_TYPE, "<urn:ao:term>"),
        (term, RDF_TYPE, "<urn:emporium:Thing>"),  # a second type: no op carries it
        (term, prop_iri("term", "name"), '"Film"'),
        (term, SKOS_PREF_LABEL, '"Film"'),
        (term, DCT_DESCRIPTION, '"A film"'),
        (term, DCT_DESCRIPTION, '"film: a motion picture"'),  # the index text beside the mirror
        (term, DCT_DESCRIPTION, '"A film"'),  # a duplicate row of the same value
        (term, prop_iri("term", "weight"), f'"7"^^<{XSD_INT}>'),
        (term, "urn:x:note", "_:b0"),  # a blank node: no op carries it
        (term, "urn:sophia:wire#relates", "<urn:ao:term/id=t2>"),  # a foreign predicate on the row: kept, owned
        (vdb, "urn:ao:vdb#collection", '"semantic_layer"'),  # an index record with no row, untyped
        (ghost, "urn:x:p", '"never asked for"'),  # neither typed ao nor an index record
    ]
    ops, tally = A.adoption_ops(triples)
    by = {(op["subject"], op["predicate"]): op for op in ops}
    assert all(op["op"] == "set" for op in ops)
    assert by[(term, DCT_DESCRIPTION)]["values"] == ["A film", "film: a motion picture"]
    assert by[(term, prop_iri("term", "weight"))]["values"] == [{"@value": "7", "@type": XSD_INT}]
    assert by[(term, "urn:sophia:wire#relates")]["values"] == [{"@id": "urn:ao:term/id=t2"}]
    assert {op["class"] for op in ops if op["subject"] == term} == {"urn:ao:term"}
    assert by[(vdb, "urn:ao:vdb#collection")]["class"] == A.VDB_CLASS
    assert not [op for op in ops if op["subject"] == ghost or op["predicate"] in (RDF_TYPE, "urn:x:note")]
    assert (tally.rows, tally.index_records, tally.kept_types, tally.kept_blank, tally.skipped) == (1, 1, 1, 1, 1)
    assert tally.values == sum(len(op["values"]) for op in ops)


def test_the_class_is_the_row_iri_table_when_a_subject_has_two_ao_types():
    s = "urn:ao:tag/id=1"
    ops, tally = A.adoption_ops([(s, RDF_TYPE, "<urn:ao:term>"), (s, RDF_TYPE, "<urn:ao:tag>"), (s, "urn:ao:tag#name", '"pii"')])
    assert ops[0]["class"] == "urn:ao:tag" and tally.kept_types == 1


def test_adoption_is_needed_when_the_ledger_owns_nothing_or_fewer_subjects():
    assert A._needed(0, 0) is False
    assert A._needed(10, 0) is True
    assert A._needed(10, 3) is True
    assert A._needed(10, 10) is False
    assert A._needed(10, 14) is False  # retracted subjects stay owned


# -- live helpers --------------------------------------------------------------------


def _live() -> None:
    import os

    if not os.environ.get("GARDEND_URL") or not os.environ.get("GARDEND_TOKEN"):
        raise AssertionError("live test: set GARDEND_URL and GARDEND_TOKEN (a gardend with the ledger)")


def _fresh(tag: str) -> GardendClient:
    _live()
    c = GardendClient(graph_id=f"adopt-{tag}-{uuid.uuid4().hex[:8]}")
    c.ensure_graph(f"adoption test {tag}")
    return c


def _triples(c: GardendClient) -> set[tuple[str, str, str]]:
    g = W.data_graph_iri(c.graph_id)
    rows = c.select_raw(f"SELECT ?s ?p ?o WHERE {{ GRAPH <{g}> {{ ?s ?p ?o }} }}")
    return {(r["s"], r["p"], r["o"]) for r in rows}


def _values(c: GardendClient, subject: str, predicate: str) -> list[str]:
    g = W.data_graph_iri(c.graph_id)
    rows = c.select_raw(f"SELECT ?o WHERE {{ GRAPH <{g}> {{ <{subject}> <{predicate}> ?o }} }}")
    return sorted(r["o"] for r in rows)


def _replay(c: GardendClient, into: str) -> dict:
    response = c._http.post(f"/emporium/fold/{c.graph_id}/ao/replay", json={"into": into})
    assert response.status_code == 200, response.text[:300]
    return response.json()


def _frames(c: GardendClient) -> int:
    return sum((W.watermark(c).get("version") or {}).values())


FOREIGN = [
    # the Nidra service's question record, linking to a row (a foreign subject)
    ("<urn:nidra:question/q1>", "<urn:nidra:column>", "<urn:ao:catalog_column/id=adopt-col>"),
    ("<urn:nidra:question/q1>", "<urn:nidra:question>", '"why"'),
    # Emporium-style testimony about a row
    ("<urn:emporium:testimony/t1>", "<urn:emporium:about>", "<urn:ao:catalog_column/id=adopt-col>"),
]


def _seed_by_sparql(c: GardendClient) -> None:
    """The fixture loaded by SPARQL, one more row with its vector-index triples, an
    index record with no row, a foreign predicate on a row and foreign subjects."""
    from auto_ontology.rdf.export import load
    from auto_ontology.vdb.gardend import GardendVDB

    W.set_write_mode("sparql")
    load(FIXTURE_NT.read_text().splitlines())
    W.insert_rows("catalog_column", [{"id": "adopt-col", "table_id": "adopt-tbl", "name": "title", "description": "The film's title"}])
    vdb = GardendVDB(collection_name="data_objects_layer")
    assert vdb.write_to_index([
        {"text": "title: the film's title", "metadata": {"id": "adopt-col", "label": "Column", "database_name": "pagila"}},
        {"text": "a note with no catalog row", "metadata": {"id": "n1", "label": "Note"}},
    ]) == 2
    g = W.data_graph_iri(c.graph_id)
    lines = " ".join(f"{s} {p} {o} ." for s, p, o in FOREIGN)
    c.update(f"INSERT DATA {{ GRAPH <{g}> {{ {lines} <urn:ao:catalog_column/id=adopt-col> <urn:sophia:wire#seeAlso> <urn:nidra:question/q1> }} }}")


# -- live ------------------------------------------------------------------------------


def test_a_sparql_seeded_graph_is_adopted_in_place_once():
    c = _fresh("upgrade")
    col = row_iri_single("catalog_column", "adopt-col")
    try:
        with use_client(c):
            _seed_by_sparql(c)
            before = _triples(c)
            W.set_write_mode("commit")
            assert A.plan(c).how == "all"
            out = A.adopt(c)
            after = _triples(c)
            # the graph reads the same: nothing was deleted; only the index record with
            # no row gained the type commit mode gives it
            assert before <= after
            assert after - before == {("<urn:ao:vdb/data_objects_layer/n1>", f"<{RDF_TYPE}>", f"<{A.VDB_CLASS}>")}
            for t in FOREIGN:
                assert t in after
            # every triple of every adopted subject is the ledger's
            oracle = _replay(c, "oracle")
            assert oracle["equal"] is True, oracle
            on_ao = [t for t in after if t[0].startswith("<urn:ao:")]
            assert oracle["triples"] == len(on_ao)
            assert out.subjects == out.plan.adoptable == len({t[0] for t in on_ao})
            assert out.tally.index_records == 1 and out.tally.kept_types == 0 and out.tally.kept_blank == 0
            # a second start adopts nothing and appends nothing
            frames = _frames(c)
            again = A.adopt(c)
            assert again.plan.how == "none" and again.txn is None and _frames(c) == frames
            # a live replay changes nothing: no triple of an ao subject is outside the ledger
            assert _replay(c, "live")["equal"] is True
            assert _triples(c) == after
            # writes after adoption: a set replaces the loaded value (no second value beside it)
            film = next(t[0][1:-1] for t in after if t[1] == "<urn:ao:term#name>" and t[2] == '"Film"')
            W.update_rows("term", [film], {"name": "Movie"})
            assert _values(c, film, "urn:ao:term#name") == ['"Movie"']
            assert _values(c, film, SKOS_PREF_LABEL) == ['"Movie"']
            # the index is ledger-owned: deleting a record takes exactly its values
            from auto_ontology.vdb.gardend import P_COLLECTION, P_METADATA, GardendVDB

            assert GardendVDB(collection_name="data_objects_layer").delete_by_id("adopt-col") == 1
            assert _values(c, col, P_COLLECTION) == [] and _values(c, col, P_METADATA) == []
            assert _values(c, col, prop_iri("catalog_column", "name")) == ['"title"']
            # a retract of an adopted row removes it, loaded triples and all
            W.delete_rows("catalog_column", ["adopt-col"])
            assert not [t for t in _triples(c) if t[0] == f"<{col}>"]
            assert ("<urn:nidra:question/q1>", "<urn:nidra:question>", '"why"') in _triples(c)
            assert _replay(c, "oracle")["equal"] is True
    finally:
        W.set_write_mode(None)


def test_a_mixed_graph_adopts_only_what_the_ledger_does_not_own():
    """Seeded by SPARQL, then one row written by commit (as a NIDRA_WRITES=commit
    engine did before adoption existed): the ledger owns one subject, the rest move."""
    c = _fresh("mixed")
    try:
        with use_client(c):
            W.set_write_mode("sparql")
            from auto_ontology.rdf.export import load

            load(FIXTURE_NT.read_text().splitlines())
            W.set_write_mode("commit")
            W.insert_row("tag", {"id": "mixed-tag", "name": "pii"})
            before = _triples(c)
            p = A.plan(c)
            assert p.how == "unowned" and p.owned == 1 and len(p.subjects) == p.adoptable - 1
            assert row_iri_single("tag", "mixed-tag") not in p.subjects
            A.adopt(c)
            assert _triples(c) == before
            assert _replay(c, "oracle")["equal"] is True
            assert A.plan(c).how == "none"
            assert A.plan(c, every=True).how == "none"  # every subject asked: all owned
    finally:
        W.set_write_mode(None)


def test_the_waiter_waits_for_the_adopter_and_gives_up_alone():
    c = _fresh("wait")
    try:
        with use_client(c):
            W.set_write_mode("sparql")
            from auto_ontology.rdf.export import load

            load(FIXTURE_NT.read_text().splitlines())
            W.set_write_mode("commit")
            # nobody adopts: the bounded wait refuses
            with pytest.raises(A.AdoptionPending, match="only the API server adopts"):
                A.wait_until_adopted(c, timeout_s=1)
            # the ingestion service waits while the API server adopts
            done: dict = {}

            def wait() -> None:
                try:
                    A.wait_until_adopted(GardendClient(graph_id=c.graph_id), timeout_s=120)
                    done["at"] = time.monotonic()
                except Exception as exc:  # pragma: no cover - reported below
                    done["error"] = exc

            waiter = threading.Thread(target=wait)
            waiter.start()
            time.sleep(3)
            assert "at" not in done and waiter.is_alive()
            adoption_started = time.monotonic()
            A.adopt(c)
            waiter.join(30)
            assert not waiter.is_alive() and "error" not in done, done
            assert done["at"] > adoption_started  # it went on only once the rows were owned
            A.wait_until_adopted(c, timeout_s=1)  # and now returns at once
    finally:
        W.set_write_mode(None)


def test_an_adoption_that_died_midway_left_the_graph_as_it_was_and_the_next_one_completes():
    """A run of frames never closed is never folded: the graph is unchanged, the ledger
    owns nothing, and the next start adopts everything (after the dead run stops
    blocking, at most 60 s)."""
    c = _fresh("died")
    try:
        with use_client(c):
            W.set_write_mode("sparql")
            from auto_ontology.rdf.export import load

            load(FIXTURE_NT.read_text().splitlines())
            before = _triples(c)
            W.set_write_mode("commit")
            ops, _ = A.adoption_ops(A._triples_of(c, A.adoptable_subjects(c)))
            # the first request of an adoption, then the process dies: no commit frame
            W._post(c, {"witness": "w", "writer": "nidra-engine", "txn": f"{A.TXN_PREFIX}dead", "commit": False, "ops": ops[:50]}, False)
            assert _triples(c) == before
            assert A.fold_status(c)["owned_subjects"] == 0
            assert A.plan(c).how == "all"
            A.adopt(c)
            assert _triples(c) == before
            assert _replay(c, "oracle")["equal"] is True
            assert A.plan(c).how == "none"
    finally:
        W.set_write_mode(None)
