# SPDX-License-Identifier: Apache-2.0
"""Row-level writes in the Direct-Mapping vocabulary: the primitive every
write port is built from, and the one write seam, :func:`commit`.

A row is a resource; inserting one sets its columns (literals, the
``skos:prefLabel`` / ``dcterms:description`` mirrors, ``ref-`` links);
updating replaces the values of the changed columns; deleting retracts the
row and follows the metadata's ``ON DELETE CASCADE`` / ``SET NULL`` foreign
keys the way Postgres would. Server defaults declared in the metadata (uuid
ids, ``false`` flags, ``now()`` stamps, string defaults) are applied here.

**The seam.** Every write is a list of *ops* in the wire form of gardend's
Emporium commit API (``plans/2026-11-02-emporium-log/COMMIT-API.md`` §3):
``set`` (replace a predicate's whole value set; ``values: []`` clears it),
``add`` / ``remove``, ``retract`` (drop the subject). :func:`row_ops`,
:func:`update_ops` and :func:`delete_ops` build them from rows; :func:`commit`
sends one logical write. ``NIDRA_WRITES`` (read once) picks the transport:

* ``commit`` (default): the ops go to ``POST /emporium/commit/{graph}/ao?wait=folded``.
  The ledger appends one frame (the ack is its durability barrier) and the
  fold applies it to ``urn:mnemosyne:local:graph:{graph}:user:rdf``, the graph
  every read already uses; ``wait=folded`` gives read-your-writes.
* ``sparql`` (the opt-out, for a gardend without the ledger): the ops are
  rendered to ONE SPARQL update request (``DELETE`` of the replaced values,
  ``INSERT DATA`` of the new ones), which Oxigraph applies atomically.

In commit mode **every ao subject must be ledger-owned**: the fold owns only
the subjects it holds frames for, so a value loaded by SPARQL beside a later
commit ``set`` survives it, a ``remove`` of a loaded value is a no-op and a
``retract`` of a loaded subject removes nothing (probed on the W7b-2 binary,
``3bf51b9``; see ``nidra/README.md``). Seeding therefore goes through commit
too (:func:`ntriples_ops` turns a Direct-Mapping export into ``set`` ops), and
SPARQL load stays a sparql-mode tool. A graph whose rows were written by SPARQL
(an older volume, a graph seeded under ``sparql``) is adopted by the ledger
once, at the API server's start (:mod:`auto_ontology.rdf.adopt`, called from
:func:`startup_check`).

Transactions: ``write_transaction()`` under commit collects the block's ops
and sends them as one request (:func:`collecting`); a read inside the block
flushes what is pending first, because a ledger transaction's frames are
invisible to reads until its commit frame lands. :func:`compensating` records,
before each write, the prior state of every subject it touches, so a failed
multi-stage write (the model import) can be undone by one compensating commit.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

from sqlalchemy import Table
from sqlalchemy.sql.elements import TextClause

from auto_ontology.dal.schema import METADATA
from auto_ontology.rdf.client import READ_HOOKS, RDF_JSON, GardendError, _TERM_RE, _unescape, client, iri, lit, values, wrap
from auto_ontology.rdf.vocab import (
    BASE,
    DCT_DESCRIPTION,
    DESCRIPTION_COLUMNS,
    LABEL_COLUMNS,
    RDF_TYPE,
    SKOS_PREF_LABEL,
    XSD,
    class_iri,
    parse_row_iri,
    prop_iri,
    ref_iri,
    row_iri,
    row_iri_single,
)

logger = logging.getLogger(__name__)

_GRAPH_SUFFIX = ":user:rdf"
VOCAB = "ao"
Op = dict[str, Any]


def data_graph_iri(graph_id: str | None = None) -> str:
    gid = graph_id or client().graph_id
    return f"urn:mnemosyne:local:graph:{gid}{_GRAPH_SUFFIX}"


def _table(name: str) -> Table:
    return METADATA.tables[name]


def _pk_cols(table: Table) -> list[str]:
    return [c.name for c in table.primary_key.columns]


def _fk_by_col(table: Table) -> dict[str, tuple[str, str]]:
    return {fk.parent.name: (fk.column.table.name, fk.column.name) for fk in table.foreign_keys}


def _server_default(column) -> Any:
    """A Python value for the metadata's server default, or ``None``."""
    default = column.server_default
    if default is None:
        return None
    arg = default.arg
    text = arg.text if isinstance(arg, TextClause) else str(arg)
    text = text.strip()
    low = text.lower()
    if "uuid" in low:
        return str(uuid.uuid4())
    if low in ("false", "'false'"):
        return False
    if low in ("true", "'true'"):
        return True
    if "now()" in low or "current_timestamp" in low:
        return datetime.now(timezone.utc)
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        return None


def row_subject(table_name: str, row: dict[str, Any]) -> str:
    table = _table(table_name)
    return row_iri(table_name, {c: row[c] for c in _pk_cols(table)})


def row_triples(table_name: str, row: dict[str, Any], columns: Iterable[str] | None = None) -> list[str]:
    """N-Triples lines for ``row`` (all columns, or only ``columns``).

    Kept for readers of the export shape (tests, tools); writes use :func:`row_ops`.
    """
    table = _table(table_name)
    subject = iri(row_subject(table_name, row))
    fk_by_col = _fk_by_col(table)
    out: list[str] = []
    if columns is None:
        out.append(f"{subject} {iri(RDF_TYPE)} {iri(class_iri(table_name))} .")
    for column, value in row.items():
        if columns is not None and column not in columns:
            continue
        if value is None:
            continue
        prop = iri(prop_iri(table_name, column))
        out.append(f"{subject} {prop} {lit(value)} .")
        if column in LABEL_COLUMNS and isinstance(value, str) and value:
            out.append(f"{subject} {iri(SKOS_PREF_LABEL)} {lit(value)} .")
        if column in DESCRIPTION_COLUMNS and isinstance(value, str) and value:
            out.append(f"{subject} {iri(DCT_DESCRIPTION)} {lit(value)} .")
        if isinstance(value, list):
            for member in value:
                out.append(f"{subject} {prop} {lit(member)} .")
        if column in fk_by_col:
            ref_table, ref_col = fk_by_col[column]
            out.append(f"{subject} {iri(ref_iri(table_name, column))} {iri(row_iri(ref_table, {ref_col: value}))} .")
    return out


def apply_defaults(table_name: str, values_in: dict[str, Any]) -> dict[str, Any]:
    table = _table(table_name)
    row = dict(values_in)
    for column in table.columns:
        if column.name not in row or row[column.name] is None:
            default = _server_default(column)
            if default is not None:
                row[column.name] = default
    return row


# ---------------------------------------------------------------------------
# Terms: a Python value as a commit term, exactly as ``client.lit`` writes it
# ---------------------------------------------------------------------------


class Iri(str):
    """A value that is an IRI (``{"@id": ...}``), not a string literal."""


_XSD_STRING = f"{XSD}string"


def term(value: Any) -> Any:
    """``value`` as a commit term, the same RDF term ``lit(value)`` serializes.

    Typed literals carry their lexical form explicitly (never a bare JSON number),
    so the stored literal is the one the SPARQL path would have written.
    """
    if isinstance(value, Iri):
        return {"@id": str(value)}
    if isinstance(value, bool):
        return {"@value": "true" if value else "false", "@type": f"{XSD}boolean"}
    if isinstance(value, int):
        return {"@value": str(value), "@type": f"{XSD}integer"}
    if isinstance(value, float):
        if math.isnan(value):
            lexical = "NaN"  # repr() gives "nan", which the commit route refuses as ill-formed
        elif math.isinf(value):
            lexical = "INF" if value > 0 else "-INF"
        else:
            lexical = repr(value)
        return {"@value": lexical, "@type": f"{XSD}double"}
    if isinstance(value, datetime):
        return {"@value": value.isoformat(), "@type": f"{XSD}dateTime"}
    if isinstance(value, (list, dict)):
        return {"@value": json.dumps(value, ensure_ascii=False, sort_keys=False), "@type": RDF_JSON}
    return str(value)


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape(s: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in s)


def term_nt(t: Any) -> str:
    """A commit term in N-Triples syntax (the inverse of :func:`nt_term`)."""
    if isinstance(t, dict):
        if "@id" in t:
            return f"<{t['@id']}>"
        lexical = _escape(str(t["@value"]))
        if t.get("@type") and t["@type"] != _XSD_STRING:
            return f'"{lexical}"^^<{t["@type"]}>'
        if t.get("@language"):
            return f'"{lexical}"@{t["@language"]}'
        return f'"{lexical}"'
    if isinstance(t, bool):
        return lit(t)
    if isinstance(t, int):
        return lit(t)
    if isinstance(t, float):
        return lit(t)
    return f'"{_escape(str(t))}"'


def nt_term(nt: str) -> Any:
    """An N-Triples term as SPARQL returns it, as a commit term (lexical form kept)."""
    m = _TERM_RE.match(nt)
    if not m:
        raise ValueError(f"not an N-Triples term: {nt!r}")
    if m.group("iri") is not None:
        return {"@id": m.group("iri")}
    if m.group("bnode") is not None:
        raise ValueError(f"a blank node cannot be committed: {nt!r}")
    lexical = _unescape(m.group("lex"))
    if m.group("dt") and m.group("dt") != _XSD_STRING:
        return {"@value": lexical, "@type": m.group("dt")}
    if m.group("lang"):
        return {"@value": lexical, "@language": m.group("lang")}
    return lexical


def _dedupe(terms: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    out = []
    for t in terms:
        key = term_nt(t)
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# Op builders (Direct Mapping → ops; COMMIT-API.md §3 "Direct Mapping conventions")
# ---------------------------------------------------------------------------


def set_op(subject: str, table_name: str, predicate: str, terms: list[Any]) -> Op:
    """Replace the predicate's whole value set (``[]`` clears it)."""
    return {"op": "set", "subject": subject, "class": class_iri(table_name), "predicate": predicate, "values": list(terms)}


def retract_op(table_name: str, subject: str) -> Op:
    return {"op": "retract", "subject": subject, "class": class_iri(table_name)}


def _value_terms(value: Any) -> list[Any]:
    """The values a column's own predicate holds: a list is its rdf:JSON whole plus
    one term per member (the export's shape, exactly what ``row_triples`` writes)."""
    if value is None:
        return []
    if isinstance(value, list):
        return _dedupe([term(value)] + [term(member) for member in value])
    return [term(value)]


def column_ops(table_name: str, subject: str, column: str, value: Any, *, clear: bool = True) -> list[Op]:
    """The ops that give ``column`` the value ``value`` on ``subject``: its own
    predicate (``set values[]`` for a list column), the label/description mirror
    and the ``#ref-`` link. ``clear=False`` (an insert) omits the clearing ops of
    absent mirrors and links, which a fresh row does not have."""
    table = _table(table_name)
    ops: list[Op] = []
    own = _value_terms(value)
    if own or clear:
        ops.append(set_op(subject, table_name, prop_iri(table_name, column), own))
    mirrored = [term(value)] if isinstance(value, str) and value else []
    if column in LABEL_COLUMNS and (mirrored or clear):
        ops.append(set_op(subject, table_name, SKOS_PREF_LABEL, mirrored))
    if column in DESCRIPTION_COLUMNS and (mirrored or clear):
        ops.append(set_op(subject, table_name, DCT_DESCRIPTION, mirrored))
    fk = _fk_by_col(table).get(column)
    if fk is not None:
        link = [term(Iri(row_iri(fk[0], {fk[1]: value})))] if value is not None else []
        if link or clear:
            ops.append(set_op(subject, table_name, ref_iri(table_name, column), link))
    return ops


def row_ops(table_name: str, row: dict[str, Any]) -> list[Op]:
    """Insert ``row`` (a complete row, defaults already applied): one ``set`` per
    non-NULL column, its mirrors and its ``#ref-`` link. ``rdf:type`` is the class."""
    subject = row_subject(table_name, row)
    ops: list[Op] = []
    for column, value in row.items():
        if value is None:
            continue
        ops.extend(column_ops(table_name, subject, column, value, clear=False))
    return ops


def update_ops(table_name: str, subject: str, changes: dict[str, Any]) -> list[Op]:
    """Set ``changes`` (column → value; ``None`` clears) on an existing row."""
    ops: list[Op] = []
    for column, value in changes.items():
        ops.extend(column_ops(table_name, subject, column, value))
    return ops


def _dependents(table_name: str) -> list[tuple[str, str, str | None]]:
    """``(child_table, child_column, ondelete)`` for every FK pointing here."""
    out: list[tuple[str, str, str | None]] = []
    for table in METADATA.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name == table_name:
                out.append((table.name, fk.parent.name, (fk.ondelete or "").upper() or None))
    return out


def delete_ops(table_name: str, subjects: Iterable[str]) -> list[Op]:
    """Retract rows, following ``ON DELETE CASCADE`` and ``SET NULL`` like Postgres:
    every cascaded child is an explicit ``retract`` (the fold knows no foreign keys),
    every nulled child an explicit clear of the column and its link."""
    ops: list[Op] = []
    _collect_deletes(table_name, list(dict.fromkeys(subjects)), ops, set())
    return ops


def _collect_deletes(table_name: str, subjects: list[str], ops: list[Op], seen: set[str]) -> None:
    subjects = [s for s in subjects if s not in seen]
    if not subjects:
        return
    seen.update(subjects)
    dependents = _dependents(table_name)
    if dependents:
        by_ref = {ref_iri(t, c): (t, c, od) for t, c, od in dependents}
        children: dict[tuple[str, str, str | None], list[str]] = {}
        refs = " ".join(iri(r) for r in by_ref)
        for i in range(0, len(subjects), 400):
            chunk = subjects[i : i + 400]
            rows = client().select(
                f"SELECT DISTINCT ?c ?q WHERE {{ {wrap(f'{values(chr(112), chunk)} VALUES ?q {{ {refs} }} ?c ?q ?p .')} }}"
            )
            for r in rows:
                children.setdefault(by_ref[r["q"]], []).append(r["c"])
        for (child_table, child_col, ondelete), found in children.items():
            found = sorted(set(found))
            if ondelete == "CASCADE":
                _collect_deletes(child_table, found, ops, seen)
            elif ondelete == "SET NULL":
                for child in found:
                    ops.extend(update_ops(child_table, child, {child_col: None}))
    ops.extend(retract_op(table_name, s) for s in subjects)


def ntriples_ops(lines: Iterable[str]) -> list[Op]:
    """A Direct-Mapping N-Triples export as ops: one ``set values[]`` per
    (subject, predicate) holding exactly the exported values (``rdf:type`` becomes
    the class). Seeds a graph through the ledger, so every ao subject is owned."""
    by_subject: dict[str, dict[str, list[Any]]] = {}
    classes: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        s, p, o = _split_triple(line)
        if p == RDF_TYPE:
            classes[s] = o[1:-1]
            continue
        by_subject.setdefault(s, {}).setdefault(p, []).append(nt_term(o))
    ops: list[Op] = []
    for s, preds in by_subject.items():
        cls = classes.get(s) or BASE + parse_row_iri(s)[0]
        for p, terms in preds.items():
            ops.append({"op": "set", "subject": s, "class": cls, "predicate": p, "values": _dedupe(terms)})
    return ops


def _split_triple(line: str) -> tuple[str, str, str]:
    """``<s> <p> o .`` → ``(s, p, o-as-N-Triples)`` (subject and predicate are IRIs here)."""
    assert line.endswith("."), line
    body = line[:-1].rstrip()
    s_end = body.index(">")
    s = body[1:s_end]
    rest = body[s_end + 1 :].lstrip()
    p_end = rest.index(">")
    p = rest[1:p_end]
    o = rest[p_end + 1 :].strip()
    return s, p, o


# ---------------------------------------------------------------------------
# The switch, the identities
# ---------------------------------------------------------------------------

_MODE: str | None = None
_MODES = ("sparql", "commit")


def write_mode() -> str:
    """``NIDRA_WRITES``: ``commit`` (default) or ``sparql``, read once per process."""
    global _MODE
    if _MODE is None:
        raw = (os.environ.get("NIDRA_WRITES") or "commit").strip().lower()
        if raw not in _MODES:
            raise ValueError(f"NIDRA_WRITES must be one of {_MODES}, not {raw!r}")
        _MODE = raw
        logger.info("writes: NIDRA_WRITES=%s", _MODE)
    return _MODE


def set_write_mode(mode: str | None) -> None:
    """Pin the mode (tests), or ``None`` to read ``NIDRA_WRITES`` again."""
    global _MODE, _LEDGER_CHECKED
    if mode is not None and mode not in _MODES:
        raise ValueError(mode)
    _MODE = mode
    _LEDGER_CHECKED = set()


def writer_id() -> str:
    """The body's ``writer``: who asked (``NIDRA_ORIGIN``), tenant-stable, never a cell id."""
    return os.environ.get("NIDRA_ORIGIN") or "nidra-engine"


_witness: ContextVar[str | None] = ContextVar("nidra_write_witness", default=None)


def current_witness() -> str:
    """The engine user in scope (:func:`witness_scope`), else the service identity
    (``NIDRA_WITNESS``, else the writer id)."""
    return _witness.get() or os.environ.get("NIDRA_WITNESS") or writer_id()


@contextmanager
def witness_scope(user_id: str | None) -> Iterator[None]:
    """Writes in the block are testified by ``user_id`` (no-op when empty)."""
    if not user_id:
        yield
        return
    token = _witness.set(user_id)
    try:
        yield
    finally:
        _witness.reset(token)


# ---------------------------------------------------------------------------
# The commit transport
# ---------------------------------------------------------------------------


class CommitError(GardendError):
    """The ledger refused (or could not take) a commit."""

    def __init__(self, status: int, body: Any, where: str = "commit") -> None:
        self.status = status
        self.body = body
        message = body.get("error") if isinstance(body, dict) else body
        super().__init__(f"{where}: HTTP {status}: {message}")


class LedgerUnavailable(GardendError):
    """``NIDRA_WRITES=commit`` against a gardend with no commit route."""


_LEDGER_CHECKED: set[tuple[str, str]] = set()
_MAX_BODY = 32 * 1024 * 1024  # split above this (the route takes 64 MiB)
_FOLD_WAIT_S = 120.0


def _path(graph: str, suffix: str = "") -> str:
    from urllib.parse import quote

    return f"/emporium/commit/{quote(graph, safe='')}/{VOCAB}{suffix}"


def check_ledger(c=None) -> dict[str, Any]:
    """Startup probe: ``GET /emporium/commit/{g}/ao/watermark``. A gardend without
    the ledger answers the route with a bare 404: that raises :class:`LedgerUnavailable`.
    A 404 naming the graph means the route exists and the graph does not (yet)."""
    c = c or client()
    response = c._http.get(_path(c.graph_id, "/watermark"))
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code == 200:
        _LEDGER_CHECKED.add((c.url, c.graph_id))
        return body
    if response.status_code == 404 and isinstance(body, dict) and "graph not found" in str(body.get("error", "")):
        return {"ok": True, "graph": c.graph_id, "missing": True}
    if response.status_code == 404:
        raise LedgerUnavailable(
            f"NIDRA_WRITES=commit (the default), but {c.url} has no Emporium commit route "
            f"(GET {_path(c.graph_id, '/watermark')} -> 404): this gardend has no ledger. "
            "Run a gardend with the ledger, or set NIDRA_WRITES=sparql (the opt-out)."
        )
    raise CommitError(response.status_code, body, "watermark")


def startup_check(*, adopt: bool = False) -> None:
    """At service start, under ``NIDRA_WRITES=commit``:

    * the gardend must have the ledger's commit route, else :class:`LedgerUnavailable`
      (fail loudly, not at the first write hours later);
    * the graph's catalog must be ledger-owned. ``adopt=True`` (the API server, and
      only it) adopts the rows written by SPARQL (:func:`auto_ontology.rdf.adopt.adopt`,
      once: a second start finds nothing to do); every other process (the ingestion
      service) waits until it has, bounded by ``NIDRA_ADOPT_WAIT_S``
      (:func:`auto_ontology.rdf.adopt.wait_until_adopted`), so two adoptions never run
      at once and nothing is written before the rows are owned.

    Under ``sparql``: only a warning when the ledger already owns this graph's catalog.
    """
    from auto_ontology.rdf import adopt as adoption

    if write_mode() == "commit":
        check_ledger()
        logger.info("writes: NIDRA_WRITES=commit; the ledger answers on %s", client().url)
        if adopt:
            adoption.adopt()
        else:
            adoption.wait_until_adopted()
    else:
        adoption.warn_if_ledger_owned()


class WitnessMiddleware:
    """ASGI middleware: a request's writes are testified by the engine user the
    trusted gateway names in ``x-auto-ontology-user-id`` (``server/identity.py``)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            for name, value in scope.get("headers") or ():
                if name == b"x-auto-ontology-user-id":
                    user = value.decode("latin-1").strip()
                    if user:
                        with witness_scope(user):
                            await self.app(scope, receive, send)
                        return
                    break
        await self.app(scope, receive, send)


def watermark(c=None) -> dict[str, Any]:
    c = c or client()
    response = c._http.get(_path(c.graph_id, "/watermark"))
    if response.status_code != 200:
        raise CommitError(response.status_code, _json(response), "watermark")
    return response.json()


def _json(response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]


def _wait_folded(c, deadline_s: float = _FOLD_WAIT_S) -> None:
    """After a 202 (the fold was behind at the route's 10 s), poll the watermark."""
    end = time.monotonic() + deadline_s
    while True:
        mark = watermark(c)
        if mark.get("folded"):
            return
        if time.monotonic() > end:
            raise CommitError(202, mark, "fold wait")
        time.sleep(0.2)


def _post(c, body: dict[str, Any], wait_folded: bool) -> dict[str, Any]:
    path = _path(c.graph_id, "?wait=folded" if wait_folded else "")
    for attempt in range(6):
        response = c._http.post(path, json=body)
        payload = _json(response)
        if response.status_code in (200, 202):
            if response.status_code == 202 and wait_folded:
                _wait_folded(c)
            return payload
        transient = response.status_code == 503 or (
            response.status_code == 409 and isinstance(payload, dict) and "openTxn" in payload
        )
        if transient and attempt < 5:
            time.sleep(0.5 * (attempt + 1))
            continue
        raise CommitError(response.status_code, payload)
    raise AssertionError("unreachable")


def _send_commit(ops: list[Op], witness: str, origin: str, txn: str | None, wait_folded: bool) -> dict[str, Any] | None:
    c = client()
    if (c.url, c.graph_id) not in _LEDGER_CHECKED:
        check_ledger(c)
        _LEDGER_CHECKED.add((c.url, c.graph_id))
    base = {"witness": witness, "writer": origin}
    size = len(json.dumps(ops, ensure_ascii=False))
    if size <= _MAX_BODY:
        body = {**base, "ops": ops}
        if txn:
            body.update(txn=txn, commit=True)
        return _post(c, body, wait_folded)
    # Too big for one body: one transaction over several requests, closed by the last.
    run = txn or f"nidra-{uuid.uuid4().hex}"
    chunks: list[list[Op]] = [[]]
    used = 0
    for op in ops:
        n = len(json.dumps(op, ensure_ascii=False)) + 1
        if chunks[-1] and used + n > _MAX_BODY:
            chunks.append([])
            used = 0
        chunks[-1].append(op)
        used += n
    out = None
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        out = _post(c, {**base, "txn": run, "commit": last, "ops": chunk}, wait_folded and last)
    return out


_FOREIGN_SWEEP_TABLES: str | None = None


def _sweep_foreign(subjects: list[str]) -> None:
    """After a commit retracted ``subjects`` (and the fold applied it), remove what
    the ledger does not own and SPARQL's ``DELETE {?s ?p ?o . ?x ?q ?s}`` removed:
    triples left on those subjects (the vector-index rows hang there) and links to
    them from subjects that are not ao rows (the Nidra service's question records).
    A live replay would drop the former too; nothing here is a ledger head."""
    global _FOREIGN_SWEEP_TABLES
    if not subjects:
        return
    if _FOREIGN_SWEEP_TABLES is None:
        _FOREIGN_SWEEP_TABLES = ", ".join(json.dumps(t.name) for t in METADATA.sorted_tables)
    g = iri(data_graph_iri())
    for i in range(0, len(subjects), 400):
        vals = values("s", subjects[i : i + 400])
        client().update(
            f"DELETE {{ GRAPH {g} {{ ?s ?p ?o }} }} WHERE {{ GRAPH {g} {{ {vals} ?s ?p ?o }} }} ; "
            f"DELETE {{ GRAPH {g} {{ ?x ?q ?s }} }} WHERE {{ GRAPH {g} {{ {vals} ?x ?q ?s . "
            f'BIND(STRBEFORE(STRAFTER(STR(?x), "{BASE}"), "/") AS ?t) FILTER(!(?t IN ({_FOREIGN_SWEEP_TABLES}))) }} }}'
        )


# ---------------------------------------------------------------------------
# The SPARQL rendering of ops (sparql mode)
# ---------------------------------------------------------------------------


def _net_effect(ops: list[Op]) -> tuple[dict[str, str], dict[str, bool], dict[tuple[str, str], tuple]]:
    """Fold a request's ops in order into its net effect: which subjects were wiped
    (``retract``), and per (subject, predicate) either ``("set", terms)`` (the final
    value set) or ``("delta", adds, removes)`` relative to what is stored."""
    classes: dict[str, str] = {}
    wiped: dict[str, bool] = {}
    preds: dict[tuple[str, str], tuple] = {}
    for op in ops:
        s = op["subject"]
        classes.setdefault(s, op["class"])
        kind = op["op"]
        if kind == "retract":
            wiped[s] = True
            for key in [k for k in preds if k[0] == s]:
                del preds[key]
            continue
        p = op["predicate"]
        vals = op.get("values")
        if vals is None:
            vals = [] if op.get("value") is None else [op["value"]]
        key = (s, p)
        if kind == "set":
            preds[key] = ("set", _dedupe(vals))
            continue
        state = preds.get(key)
        names = {term_nt(v) for v in vals}
        if state is None and wiped.get(s):
            state = ("set", [])
        if kind == "add":
            if state is None:
                preds[key] = ("delta", _dedupe(vals), [])
            elif state[0] == "set":
                preds[key] = ("set", _dedupe(list(state[1]) + list(vals)))
            else:
                preds[key] = ("delta", _dedupe(list(state[1]) + list(vals)), [r for r in state[2] if term_nt(r) not in names])
        elif kind == "remove":
            if state is None:
                preds[key] = ("delta", [], _dedupe(vals))
            elif state[0] == "set":
                preds[key] = ("set", [v for v in state[1] if term_nt(v) not in names])
            else:
                preds[key] = ("delta", [a for a in state[1] if term_nt(a) not in names], _dedupe(list(state[2]) + list(vals)))
        else:
            raise ValueError(f"unknown op {kind!r}")
    return classes, wiped, preds


def render_sparql(ops: list[Op], graph_iri: str | None = None) -> str:
    """One SPARQL update request with the ops' net effect (empty string: nothing).

    A ``retract`` deletes the subject's triples and the links to it, as
    ``delete_subjects`` always has; a ``set`` deletes the predicate's values and
    inserts the new set; ``add``/``remove`` insert/delete the named values; every
    subject left with values gets its ``rdf:type`` (what the fold does in commit mode).
    """
    if not ops:
        return ""
    classes, wiped, preds = _net_effect(ops)
    g = iri(graph_iri or data_graph_iri())
    parts: list[str] = []
    if wiped:
        subjects = " ".join(iri(s) for s in wiped)
        parts.append(
            f"DELETE {{ GRAPH {g} {{ ?s ?p ?o . ?x ?q ?s }} }} "
            f"WHERE {{ GRAPH {g} {{ VALUES ?s {{ {subjects} }} ?s ?p ?o . OPTIONAL {{ ?x ?q ?s }} }} }}"
        )
    replaced = [f"({iri(s)} {iri(p)})" for (s, p), st in preds.items() if st[0] == "set" and not wiped.get(s)]
    if replaced:
        parts.append(
            f"DELETE {{ GRAPH {g} {{ ?s ?p ?v }} }} "
            f"WHERE {{ GRAPH {g} {{ VALUES (?s ?p) {{ {' '.join(replaced)} }} ?s ?p ?v }} }}"
        )
    removed = [f"{iri(s)} {iri(p)} {term_nt(v)} ." for (s, p), st in preds.items() if st[0] == "delta" for v in st[2]]
    if removed:
        parts.append(f"DELETE DATA {{ GRAPH {g} {{ {' '.join(removed)} }} }}")
    lines: list[str] = []
    typed: set[str] = set()
    for (s, p), st in preds.items():
        added = st[1]
        for v in added:
            lines.append(f"{iri(s)} {iri(p)} {term_nt(v)} .")
        if added and s not in typed:
            typed.add(s)
            lines.append(f"{iri(s)} {iri(RDF_TYPE)} {iri(classes[s])} .")
    if lines:
        parts.append(f"INSERT DATA {{ GRAPH {g} {{ {' '.join(lines)} }} }}")
    return " ; ".join(parts)


# ---------------------------------------------------------------------------
# Collection (write_transaction) and compensation (the model import)
# ---------------------------------------------------------------------------


class _Collector:
    def __init__(self) -> None:
        self.ops: list[Op] = []
        self.witness: str | None = None
        self.origin: str | None = None
        self.early_flushes = 0


_collector: ContextVar[_Collector | None] = ContextVar("nidra_write_collector", default=None)


def _flush_collector(col: _Collector) -> None:
    if not col.ops:
        return
    ops, col.ops = col.ops, []
    _commit_now(ops, col.witness or current_witness(), col.origin or writer_id(), None, True)


def _before_read() -> None:
    """A read inside a collecting block sees the block's earlier writes: flush them
    (a ledger transaction's frames stay invisible to reads until it commits)."""
    col = _collector.get()
    if col is not None and col.ops:
        col.early_flushes += 1
        _flush_collector(col)


READ_HOOKS.append(_before_read)


@contextmanager
def collecting() -> Iterator[_Collector]:
    """Gather every :func:`commit` in the block into one request sent at the end.

    Nested blocks join the outer one. An exception discards what is still pending
    (it was never sent); what a read inside the block forced out early stays.
    """
    outer = _collector.get()
    if outer is not None:
        yield outer
        return
    col = _Collector()
    token = _collector.set(col)
    try:
        yield col
    except BaseException:
        if col.ops:
            logger.info("writes: block failed; %d pending op(s) not sent", len(col.ops))
        col.ops = []
        raise
    else:
        _collector.reset(token)
        token = None
        _flush_collector(col)
        if col.early_flushes:
            logger.debug("writes: a read inside the block flushed %d time(s) before its end", col.early_flushes)
    finally:
        if token is not None:
            _collector.reset(token)


class Compensation:
    """The prior state of every subject a sequence of writes touches, recorded
    before each write, and the ops that put it back."""

    def __init__(self) -> None:
        self.prior: dict[str, tuple[str, dict[str, list[Any]] | None]] = {}
        self.touched: dict[str, set[str]] = {}
        self.writes = 0

    def observe(self, ops: list[Op]) -> None:
        new: dict[str, str] = {}
        for op in ops:
            s = op["subject"]
            if op["op"] != "retract":
                self.touched.setdefault(s, set()).add(op["predicate"])
            if s not in self.prior and s not in new:
                new[s] = op["class"]
        if new:
            found = _snapshot(list(new))
            for s, cls in new.items():
                self.prior[s] = (cls, found.get(s))
        self.writes += 1

    def ops(self) -> list[Op]:
        """Created subjects → ``retract``; changed subjects → every predicate the
        writes touched (or, for a subject they retracted, every predicate it had)
        set back to its prior values."""
        out: list[Op] = []
        for s, (cls, before) in self.prior.items():
            if before is None:
                out.append({"op": "retract", "subject": s, "class": cls})
                continue
            for p in sorted(set(before) | self.touched.get(s, set())):
                out.append({"op": "set", "subject": s, "class": cls, "predicate": p, "values": before.get(p, [])})
        return out


_VDB_PREDICATES = (f"{BASE}vdb#metadata", f"{BASE}vdb#collection")


def _snapshot(subjects: list[str]) -> dict[str, dict[str, list[Any]]]:
    """``{subject: {predicate: [terms]}}`` as stored now (absent: never written).
    ``rdf:type`` and the vector index's own predicates are not the row's state."""
    out: dict[str, dict[str, list[Any]]] = {}
    g = iri(data_graph_iri())
    for i in range(0, len(subjects), 400):
        chunk = subjects[i : i + 400]
        rows = client().select_raw(f"SELECT ?s ?p ?o WHERE {{ GRAPH {g} {{ {values('s', chunk)} ?s ?p ?o }} }}")
        for r in rows:
            s, p = r["s"][1:-1], r["p"][1:-1]
            if p == RDF_TYPE or p in _VDB_PREDICATES:
                continue
            if r["o"].startswith("_:"):
                continue
            out.setdefault(s, {}).setdefault(p, []).append(nt_term(r["o"]))
    return out


_compensation: ContextVar[Compensation | None] = ContextVar("nidra_write_compensation", default=None)


@contextmanager
def compensating() -> Iterator[Compensation]:
    """Record every write in the block; on an exception, undo them all with one
    compensating commit and re-raise. The lineage keeps both the writes and the
    undo (``compensated``, not rolled back: the ledger never forgets a frame)."""
    comp = Compensation()
    token = _compensation.set(comp)
    try:
        yield comp
    except BaseException:
        _compensation.reset(token)
        token = None
        undo = comp.ops()
        if undo:
            logger.warning("writes: compensating %d write(s) over %d subject(s)", comp.writes, len(comp.prior))
            _commit_now(undo, current_witness(), writer_id(), None, True)
        raise
    finally:
        if token is not None:
            _compensation.reset(token)


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def commit(
    ops: list[Op],
    witness: str | None = None,
    origin: str | None = None,
    txn: str | None = None,
    *,
    wait_folded: bool = True,
) -> dict[str, Any] | None:
    """Apply the ops of ONE logical write.

    ``witness`` defaults to :func:`current_witness`, ``origin`` (the body's
    ``writer``) to :func:`writer_id`; ``txn`` names the request's transaction (it
    is committed by this request; never start one with ``commit-``, the server's
    own prefix). Under ``NIDRA_WRITES=commit`` the ops are posted to the ledger
    (``wait_folded`` = read-your-writes); under ``sparql`` they are rendered to one
    SPARQL update. Inside :func:`collecting` they are queued instead.
    Returns the commit response (commit mode, sent now), else ``None``.
    """
    ops = [op for op in ops if op]
    if not ops:
        return None
    if txn is not None and txn.startswith("commit-"):
        raise ValueError("a txn named commit-… collides with the server's own transactions")
    col = _collector.get()
    if col is not None and txn is None:
        col.ops.extend(ops)
        col.witness = col.witness or witness
        col.origin = col.origin or origin
        return None
    return _commit_now(ops, witness or current_witness(), origin or writer_id(), txn, wait_folded)


def _commit_now(ops: list[Op], witness: str, origin: str, txn: str | None, wait_folded: bool) -> dict[str, Any] | None:
    comp = _compensation.get()
    if comp is not None:
        comp.observe(ops)
    if write_mode() == "commit":
        response = _send_commit(ops, witness, origin, txn, wait_folded)
        _, wiped, preds = _net_effect(ops)
        revived = {s for (s, _p), st in preds.items() if st[1]}
        gone = [s for s in wiped if s not in revived]
        if gone:
            if not wait_folded:
                _wait_folded(client())
            _sweep_foreign(gone)
        return response
    sparql = render_sparql(ops)
    if sparql:
        client().update(sparql)
    return None


# ---------------------------------------------------------------------------
# The row primitives
# ---------------------------------------------------------------------------


def insert_rows(table_name: str, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert rows (defaults applied) in one write; returns the stored rows."""
    stored = [apply_defaults(table_name, r) for r in rows]
    ops: list[Op] = []
    for row in stored:
        ops.extend(row_ops(table_name, row))
    commit(ops)
    return stored


def insert_row(table_name: str, values_in: dict[str, Any]) -> dict[str, Any]:
    return insert_rows(table_name, [values_in])[0]


def _column_props(table_name: str, column: str) -> list[str]:
    """Every property a column contributes (literal, mirrors, ref link)."""
    props = [prop_iri(table_name, column)]
    if column in LABEL_COLUMNS:
        props.append(SKOS_PREF_LABEL)
    if column in DESCRIPTION_COLUMNS:
        props.append(DCT_DESCRIPTION)
    if column in _fk_by_col(_table(table_name)):
        props.append(ref_iri(table_name, column))
    return props


def existing_subjects(table_name: str, subjects: Iterable[str]) -> set[str]:
    """Which of the row IRIs are rows of ``table_name`` (an UPDATE matches no other)."""
    subjects = list(dict.fromkeys(subjects))
    found: set[str] = set()
    for i in range(0, len(subjects), 400):
        chunk = subjects[i : i + 400]
        rows = client().select(
            f"SELECT ?s WHERE {{ {wrap(f'{values(chr(115), chunk)} ?s a {iri(class_iri(table_name))} .')} }}"
        )
        found.update(r["s"] for r in rows)
    return found


def update_rows(table_name: str, subjects: Iterable[str], changes: dict[str, Any]) -> None:
    """Set ``changes`` (column → value, ``None`` clears) on every existing subject IRI."""
    subjects = list(subjects)
    if not subjects or not changes:
        return
    ops: list[Op] = []
    alive = existing_subjects(table_name, subjects)
    for subject in dict.fromkeys(subjects):
        if subject in alive:
            ops.extend(update_ops(table_name, subject, changes))
    commit(ops)


def update_row(table_name: str, row_id: str, changes: dict[str, Any]) -> None:
    update_rows(table_name, [row_iri_single(table_name, row_id)], changes)


def _parse(subject: str) -> tuple[str, dict[str, str]]:
    return parse_row_iri(subject)


def delete_subjects(table_name: str, subjects: Iterable[str]) -> int:
    """Delete rows by subject IRI, following CASCADE and SET NULL like Postgres, in one write."""
    subjects = list(dict.fromkeys(subjects))
    if not subjects:
        return 0
    commit(delete_ops(table_name, subjects))
    return len(subjects)


def delete_rows(table_name: str, ids: Iterable[str]) -> int:
    return delete_subjects(table_name, [row_iri_single(table_name, i) for i in ids])
