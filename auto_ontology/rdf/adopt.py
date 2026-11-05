# SPDX-License-Identifier: Apache-2.0
"""The ledger adopts a catalog written by SPARQL (``NIDRA_WRITES=commit``).

**Why.** The fold owns only the subjects it holds frames for
(``plans/2026-11-02-emporium-log/COMMIT-API.md`` §8): a value written by SPARQL
(an older Nidra volume, a graph seeded under ``NIDRA_WRITES=sparql``) is
invisible to ops, so a ``set`` leaves it beside the new value, and a ``remove``
or ``retract`` aimed at it removes nothing. Under commit every ao subject must
be ledger-owned. :func:`adopt` makes the ones the ledger does not own owned,
once, at the API server's start.

**What moves.** Every subject of ``urn:mnemosyne:local:graph:{g}:user:rdf``
typed with an ao class (one of upstream's tables, from ``METADATA``), and every
untyped vector-index record with no catalog row (``urn:ao:vdb/...`` carrying
``urn:ao:vdb#collection``; commit mode types those ``urn:ao:vdb``). Of each, all
its triples but ``rdf:type`` (the class is the op's ``class``) become ``set``
ops holding exactly the values stored, in the lexical form SPARQL returns: the
row's columns, mirrors and ``#ref-`` links, and the index triples that
``vdb/gardend.py`` hangs on it (``dcterms:description``, ``vdb#metadata``,
``vdb#collection``). Nothing else is read: subjects that are not ao rows
(Emporium testimony, wires, corrections, the Nidra service's questions and
certifications) are never touched, and neither are links *to* a row from them.
Not adoptable, so left as they are and counted in the log: a second
``rdf:type``, a blank-node object (neither can be an op), and index triples on
an untyped ``urn:ao:<table>/...`` subject (a row that does not exist; adopting
would type it as one).

**Order, and why nothing is deleted.** The fold writes its output into the same
graph the SPARQL rows live in (``user:rdf``; its own control graph is
``...:ledger:fold``), and a quad is a quad: the triple a ``set`` folds in for an
adopted value *is* the triple already there. So the steps are
1. read the subjects and their triples (cursor-paged, so no row cap cuts them);
2. commit them as ``set`` ops in ONE transaction (txn ``nidra-adopt-<hex>``;
   ``writes.commit`` splits it into requests of at most 32 MiB, the last one
   closes it), so the fold applies all of it in one store transaction or none;
3. wait for the fold, then check the ledger's oracle and the count.
No step deletes anything, by SPARQL or by op. A delete of "the SPARQL copies"
after the commit would delete the fold's own output (the same quads), and a
delete before it would leave the graph empty if adoption failed. As it is, the
graph reads the same before, during and after, and a failure at any step leaves
it as it was: an unclosed transaction is never folded (the ledger lists it as
abandoned), and the next start adopts again.

**When.** :func:`plan` decides, cheaply: no adoptable subjects, nothing to do; the
ledger owns no subject (the upgrade case: nothing in it, or only an abandoned
adoption), adopt them all; it owns fewer subjects than the graph has, ask the
fold (``GET .../heads?subject=``, its ``owned`` flag) which ones and adopt
those; otherwise nothing (a second start: the log says so). ``--every`` on the
command line asks the fold about every subject regardless.

**Concurrency.** Only the API server adopts (``startup_check(adopt=True)`` in
``auto_ontology/server/__main__.py``); every other process, the ingestion service
first, waits in :func:`wait_until_adopted` until the start check says nothing is
left to adopt, bounded by ``NIDRA_ADOPT_WAIT_S`` (default 900 s), then refuses to
start. A write before adoption would be lost under it (adoption sets the values it
read) or split the row between SPARQL and the ledger. One API server per graph.

Command line (``NIDRA_WRITES=commit`` and the gardend environment as for the
engine): ``python -m auto_ontology.rdf.adopt [--check] [--every]``.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator
from urllib.parse import quote

from auto_ontology.dal.schema import METADATA
from auto_ontology.rdf import writes
from auto_ontology.rdf.client import GardendClient, GardendError, client, iri, values
from auto_ontology.rdf.vocab import BASE, RDF_TYPE, class_iri, parse_row_iri

logger = logging.getLogger(__name__)

#: How commit mode types a vector-index record that has no catalog row (``vdb/gardend.py``).
VDB_CLASS = class_iri("vdb")
VDB_PREFIX = f"{BASE}vdb/"
#: ``auto_ontology.vdb.gardend.P_COLLECTION`` (not imported: ``auto_ontology.vdb`` pulls pgvector in).
VDB_COLLECTION = f"{BASE}vdb#collection"
TXN_PREFIX = "nidra-adopt-"
_CHUNK = 200
_WAIT_S = 900.0
_FOLD_WAIT_S = 900.0


class AdoptionPending(GardendError):
    """A process that does not adopt waited for the API server's adoption, and it did not come."""


def ao_classes() -> set[str]:
    return {class_iri(t.name) for t in METADATA.sorted_tables} | {VDB_CLASS}


def _gid(c: GardendClient) -> str:
    return quote(c.graph_id, safe="")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _select_all(c: GardendClient, sparql: str) -> Iterator[dict[str, str]]:
    """Every row of a SELECT, following gardend's ``next`` cursor past its row cap."""
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {"graphId": c.graph_id, "sparql": sparql}
        if cursor:
            body["cursor"] = cursor
        page = c._post("/api/sparql/query", body)
        yield from page.get("rows") or []
        cursor = page.get("next")
        if not cursor:
            return


def _subjects_pattern(classes: set[str]) -> str:
    """The adoptable subjects: typed with an ao class, or an untyped index record with no
    row; each with at least one triple an op can carry (not ``rdf:type``, not a blank node)."""
    cls = " ".join(iri(x) for x in sorted(classes))
    return (
        f"{{ ?s a ?c . VALUES ?c {{ {cls} }} }} UNION "
        f'{{ ?s {iri(VDB_COLLECTION)} ?x . FILTER(STRSTARTS(STR(?s), "{VDB_PREFIX}")) }} '
        f"?s ?p ?o . FILTER(?p != {iri(RDF_TYPE)} && !isBlank(?o))"
    )


def count_adoptable(c: GardendClient | None = None) -> int:
    c = c or client()
    g = iri(writes.data_graph_iri(c.graph_id))
    rows = c.select(f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ GRAPH {g} {{ {_subjects_pattern(ao_classes())} }} }}")
    return int(rows[0].get("n") or 0) if rows else 0


def adoptable_subjects(c: GardendClient | None = None) -> list[str]:
    c = c or client()
    g = iri(writes.data_graph_iri(c.graph_id))
    q = f"SELECT DISTINCT ?s WHERE {{ GRAPH {g} {{ {_subjects_pattern(ao_classes())} }} }} ORDER BY ?s"
    return [r["s"][1:-1] for r in _select_all(c, q)]


def _untyped_index_subjects(c: GardendClient) -> int:
    """Index triples on an untyped ``urn:ao:<table>/...`` subject: a row that does not exist."""
    g = iri(writes.data_graph_iri(c.graph_id))
    rows = c.select(
        f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ GRAPH {g} {{ ?s {iri(VDB_COLLECTION)} ?x . "
        f'FILTER(STRSTARTS(STR(?s), "{BASE}") && !STRSTARTS(STR(?s), "{VDB_PREFIX}")) '
        f"FILTER NOT EXISTS {{ ?s a ?t }} }} }}"
    )
    return int(rows[0].get("n") or 0) if rows else 0


def _graph_size(c: GardendClient) -> int:
    g = iri(writes.data_graph_iri(c.graph_id))
    rows = c.select(f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH {g} {{ ?s ?p ?o }} }}")
    return int(rows[0].get("n") or 0) if rows else 0


def _triples_of(c: GardendClient, subjects: list[str]) -> Iterator[tuple[str, str, str]]:
    g = iri(writes.data_graph_iri(c.graph_id))
    for i in range(0, len(subjects), _CHUNK):
        chunk = subjects[i : i + _CHUNK]
        q = f"SELECT ?s ?p ?o WHERE {{ GRAPH {g} {{ {values('s', chunk)} ?s ?p ?o }} }}"
        for r in _select_all(c, q):
            yield r["s"][1:-1], r["p"][1:-1], r["o"]


# ---------------------------------------------------------------------------
# The fold's view: status (owned_subjects) and heads (owned, per subject)
# ---------------------------------------------------------------------------


def fold_status(c: GardendClient | None = None, *, deadline: float | None = None) -> dict[str, Any]:
    """``GET /emporium/fold/{g}/ao/status``, once the fold has loaded (a read: it never
    creates a ledger; a graph with none answers ``running: false``)."""
    c = c or client()
    end = deadline if deadline is not None else time.monotonic() + _WAIT_S
    while True:
        response = c._http.get(f"/emporium/fold/{_gid(c)}/{writes.VOCAB}/status")
        if response.status_code == 200:
            body = response.json()
            if not body.get("running") or body.get("ready"):
                return body
        elif response.status_code != 503:
            raise writes.CommitError(response.status_code, writes._json(response), "fold status")
        if time.monotonic() > end:
            raise writes.CommitError(503, "the fold has not loaded", "fold status")
        time.sleep(0.5)


def is_owned(c: GardendClient, subject: str) -> bool:
    for attempt in range(40):
        response = c._http.get(f"/emporium/fold/{_gid(c)}/{writes.VOCAB}/heads", params={"subject": subject})
        if response.status_code == 200:
            return bool(response.json().get("owned"))
        if response.status_code == 404:
            return False  # no ledger: nothing is owned
        if response.status_code != 503:
            raise writes.CommitError(response.status_code, writes._json(response), "fold heads")
        time.sleep(0.5)
    raise writes.CommitError(503, "the fold has not loaded", "fold heads")


def _needed(adoptable: int, owned: int) -> bool:
    return adoptable > 0 and (owned == 0 or adoptable > owned)


# ---------------------------------------------------------------------------
# The ops (pure)
# ---------------------------------------------------------------------------


@dataclass
class Tally:
    rows: int = 0
    index_records: int = 0
    values: int = 0
    kept_types: int = 0
    kept_blank: int = 0
    skipped: int = 0


def _class_for(subject: str, types: list[str], classes: set[str]) -> str | None:
    ao_types = sorted(t for t in types if t in classes)
    if ao_types:
        own = class_iri(parse_row_iri(subject)[0]) if subject.startswith(BASE) else None
        return own if own in ao_types else ao_types[0]
    if subject.startswith(VDB_PREFIX):
        return VDB_CLASS
    return None


def adoption_ops(triples: Iterable[tuple[str, str, str]], classes: set[str] | None = None) -> tuple[list[writes.Op], Tally]:
    """``(subject, predicate, object-in-N-Triples)`` → one ``set`` per subject and
    predicate holding exactly the stored values (``rdf:type`` is the op's class)."""
    classes = classes if classes is not None else ao_classes()
    types: dict[str, list[str]] = {}
    preds: dict[str, dict[str, list[str]]] = {}
    tally = Tally()
    for s, p, o in triples:
        if p == RDF_TYPE:
            if o.startswith("<"):
                types.setdefault(s, []).append(o[1:-1])
            else:
                tally.kept_types += 1
            continue
        if o.startswith("_:"):
            tally.kept_blank += 1
            continue
        preds.setdefault(s, {}).setdefault(p, []).append(o)
    ops: list[writes.Op] = []
    for s, by_pred in preds.items():
        cls = _class_for(s, types.get(s, []), classes)
        if cls is None:
            tally.skipped += 1
            continue
        tally.kept_types += sum(1 for t in types.get(s, []) if t != cls)
        if cls == VDB_CLASS:
            tally.index_records += 1
        else:
            tally.rows += 1
        for p, objects in by_pred.items():
            terms = writes._dedupe(writes.nt_term(o) for o in objects)
            tally.values += len(terms)
            ops.append({"op": "set", "subject": s, "class": cls, "predicate": p, "values": terms})
    return ops, tally


# ---------------------------------------------------------------------------
# Plan, adopt, wait
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    adoptable: int
    owned: int
    how: str  # "none" | "all" | "unowned"
    subjects: list[str] = field(default_factory=list)


def plan(c: GardendClient | None = None, *, every: bool = False) -> Plan:
    c = c or client()
    owned = int(fold_status(c).get("owned_subjects") or 0)
    n = count_adoptable(c)
    if n == 0:
        return Plan(n, owned, "none")
    if owned == 0:
        return Plan(n, owned, "all", adoptable_subjects(c))
    if every or n > owned:
        unowned = [s for s in adoptable_subjects(c) if not is_owned(c, s)]
        return Plan(n, owned, "unowned" if unowned else "none", unowned)
    return Plan(n, owned, "none")


@dataclass
class Adoption:
    graph: str
    plan: Plan
    tally: Tally = field(default_factory=Tally)
    txn: str | None = None
    bytes: int = 0
    seconds: float = 0.0
    size_before: int = 0
    size_after: int = 0
    untyped_index: int = 0
    oracle: dict[str, Any] = field(default_factory=dict)

    @property
    def subjects(self) -> int:
        return self.tally.rows + self.tally.index_records


def adopt(c: GardendClient | None = None, *, every: bool = False, check_only: bool = False) -> Adoption:
    """Adopt the ao subjects the ledger does not own (see the module docstring)."""
    c = c or client()
    if writes.write_mode() != "commit":
        raise RuntimeError("adoption is for NIDRA_WRITES=commit")
    started = time.monotonic()
    p = plan(c, every=every)
    out = Adoption(graph=c.graph_id, plan=p)
    if p.how == "none":
        logger.info(
            "ledger adoption: graph %s: nothing to adopt (%d ao subjects; the ledger owns %d)",
            c.graph_id, p.adoptable, p.owned,
        )
        return out
    logger.info(
        "ledger adoption: graph %s: %d of %d ao subjects are not ledger-owned (the ledger owns %d); %s",
        c.graph_id, len(p.subjects), p.adoptable, p.owned, "checking only" if check_only else "adopting",
    )
    ops, out.tally = adoption_ops(_triples_of(c, p.subjects))
    out.bytes = len(json.dumps(ops, ensure_ascii=False))
    out.untyped_index = _untyped_index_subjects(c)
    if check_only or not ops:
        out.seconds = time.monotonic() - started
        _report(out, committed=False)
        return out
    out.size_before = _graph_size(c)
    out.txn = f"{TXN_PREFIX}{uuid.uuid4().hex[:16]}"
    _commit_adoption(ops, out.txn)
    writes._wait_folded(c, float(os.environ.get("NIDRA_ADOPT_FOLD_WAIT_S") or _FOLD_WAIT_S))
    out.size_after = _graph_size(c)
    response = c._http.post(f"/emporium/fold/{_gid(c)}/{writes.VOCAB}/replay", json={"into": "oracle"})
    out.oracle = writes._json(response) if response.status_code == 200 else {"error": f"HTTP {response.status_code}"}
    out.seconds = time.monotonic() - started
    _report(out, committed=True)
    return out


#: An open transaction stops blocking others after 60 s without a request (COMMIT-API §5).
_OPEN_TXN_WAIT_S = 90.0


def _commit_adoption(ops: list[writes.Op], txn: str) -> None:
    """One transaction. A ``409 openTxn`` (another run open on the ledger, such as an
    adoption that died mid-way) is refused before anything is appended, and only on
    the first request (ours then holds the ledger), so it is retried under the same
    txn until that run stops blocking."""
    end = time.monotonic() + _OPEN_TXN_WAIT_S
    while True:
        try:
            writes.commit(ops, txn=txn, wait_folded=False)
            return
        except writes.CommitError as exc:
            blocked = exc.status == 409 and isinstance(exc.body, dict) and "openTxn" in exc.body
            if not blocked or time.monotonic() > end:
                raise
            logger.info("ledger adoption: another transaction (%s) is open on the ledger; waiting", exc.body.get("openTxn"))
            time.sleep(5)


def _report(out: Adoption, *, committed: bool) -> None:
    t = out.tally
    what = "adopted" if committed else "would adopt"
    logger.info(
        "ledger adoption: graph %s: %s %d subjects (%d catalog rows, %d index records), %d values (%.1f MiB of ops)%s, in %.1f s",
        out.graph, what, out.subjects, t.rows, t.index_records, t.values, out.bytes / 1048576,
        f", one transaction {out.txn}" if out.txn else "", out.seconds,
    )
    if committed:
        o = out.oracle
        logger.info(
            "ledger adoption: graph %s: folded; the oracle says equal=%s (%s triples owned, missing %s, extra %s); "
            "user:rdf held %d triples before and %d after",
            out.graph, o.get("equal"), o.get("triples"), o.get("missing"), o.get("extra"), out.size_before, out.size_after,
        )
        if o.get("equal") is not True:
            logger.warning("ledger adoption: graph %s: the oracle is not equal: %s", out.graph, {k: o.get(k) for k in ("missingSample", "extraSample", "error")})
    if t.kept_types or t.kept_blank:
        logger.warning(
            "ledger adoption: graph %s: %d further rdf:type value(s) and %d blank-node object(s) on adopted subjects "
            "stay outside the ledger (no op can carry them); a live replay of the fold drops them",
            out.graph, t.kept_types, t.kept_blank,
        )
    if out.untyped_index:
        logger.warning(
            "ledger adoption: graph %s: %d untyped urn:ao: subject(s) carry index triples for a row that does not exist; "
            "left as they are (adopting would type them as rows)",
            out.graph, out.untyped_index,
        )


def wait_until_adopted(c: GardendClient | None = None, timeout_s: float | None = None) -> None:
    """For every process but the API server: return once nothing is left to adopt (the
    API server adopts at its start), checking every 2 s; raise :class:`AdoptionPending`
    after ``timeout_s`` (``NIDRA_ADOPT_WAIT_S``, default 900)."""
    c = c or client()
    timeout = timeout_s if timeout_s is not None else float(os.environ.get("NIDRA_ADOPT_WAIT_S") or _WAIT_S)
    end = time.monotonic() + timeout
    waited = False
    last_log = 0.0
    while True:
        owned = int(fold_status(c, deadline=end).get("owned_subjects") or 0)
        n = count_adoptable(c)
        if not _needed(n, owned):
            if waited:
                logger.info("ledger adoption: graph %s is ledger-owned (%d ao subjects; the ledger owns %d); going on", c.graph_id, n, owned)
            return
        if time.monotonic() > end:
            raise AdoptionPending(
                f"graph {c.graph_id}: {n} ao subjects and the ledger owns {owned}: rows written by SPARQL wait to be "
                f"adopted, and only the API server adopts them, at its start (or `python -m auto_ontology.rdf.adopt`). "
                f"Waited {timeout:.0f} s (NIDRA_ADOPT_WAIT_S); writing before that would split the catalog."
            )
        if time.monotonic() - last_log > 15:
            logger.info(
                "ledger adoption: graph %s: waiting for the API server to adopt (%d ao subjects; the ledger owns %d)",
                c.graph_id, n, owned,
            )
            last_log = time.monotonic()
        waited = True
        time.sleep(2)


def warn_if_ledger_owned(c: GardendClient | None = None) -> None:
    """Under ``NIDRA_WRITES=sparql``: say so when this graph's catalog is ledger-owned
    (the opt-out writes beside the ledger). Never raises."""
    try:
        c = c or client()
        response = c._http.get(f"/emporium/fold/{_gid(c)}/{writes.VOCAB}/status")
        if response.status_code != 200:
            return
        owned = int(response.json().get("owned_subjects") or 0)
        if owned:
            logger.warning(
                "writes: NIDRA_WRITES=sparql, but the ledger owns %d subjects of graph %s: SPARQL writes go beside it. "
                "Rows created meanwhile are adopted when commit returns; values changed by SPARQL on rows the ledger "
                "owns are not. Keep one mode per graph.",
                owned, c.graph_id,
            )
    except Exception:  # a warning, never a reason not to start
        logger.debug("writes: ledger ownership check skipped", exc_info=True)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    writes.check_ledger()
    out = adopt(every="--every" in argv, check_only="--check" in argv)
    print(json.dumps({"graph": out.graph, "how": out.plan.how, "adoptable": out.plan.adoptable, "ownedBefore": out.plan.owned,
                      "subjects": out.subjects, "rows": out.tally.rows, "indexRecords": out.tally.index_records,
                      "values": out.tally.values, "txn": out.txn, "oracle": {k: out.oracle.get(k) for k in ("equal", "triples", "missing", "extra")} if out.oracle else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
