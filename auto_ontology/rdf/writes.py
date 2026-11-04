# SPDX-License-Identifier: Apache-2.0
"""Row-level writes in the Direct-Mapping vocabulary: the primitive every
write port is built from.

A row is a resource; inserting one emits its triples (type, literals, the
``skos:prefLabel`` / ``dcterms:description`` mirrors, ``ref-`` links);
updating replaces the triples of the changed columns; deleting removes the
row's triples and follows the metadata's ``ON DELETE CASCADE`` / ``SET NULL``
foreign keys the way Postgres would. Server defaults declared in the metadata
(uuid ids, ``false`` flags, ``now()`` stamps, string defaults) are applied here.

Every SPARQL update is one request to gardend, which Oxigraph applies
atomically. There is no multi-request transaction: ``write_transaction`` on
this backend runs the block without rollback (recorded as a POC limit).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import Table
from sqlalchemy.sql.elements import TextClause

from auto_ontology.dal.schema import METADATA
from auto_ontology.rdf.client import client, iri, lit, values, wrap
from auto_ontology.rdf.vocab import (
    DCT_DESCRIPTION,
    DESCRIPTION_COLUMNS,
    LABEL_COLUMNS,
    RDF_TYPE,
    SKOS_PREF_LABEL,
    class_iri,
    prop_iri,
    ref_iri,
    row_iri,
    row_iri_single,
)

logger = logging.getLogger(__name__)

_GRAPH_SUFFIX = ":user:rdf"


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
    """N-Triples lines for ``row`` (all columns, or only ``columns``)."""
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


def insert_rows(table_name: str, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert rows (defaults applied) in one update; returns the stored rows."""
    stored = [apply_defaults(table_name, r) for r in rows]
    lines: list[str] = []
    for row in stored:
        lines.extend(row_triples(table_name, row))
    if lines:
        client().update(f"INSERT DATA {{ GRAPH {iri(data_graph_iri())} {{ {' '.join(lines)} }} }}")
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


def update_rows(table_name: str, subjects: Iterable[str], changes: dict[str, Any]) -> None:
    """Set ``changes`` (column → value, ``None`` clears) on every subject IRI."""
    subjects = list(subjects)
    if not subjects or not changes:
        return
    g = iri(data_graph_iri())
    deletes = " ".join(
        f"?s {iri(p)} ?v{idx}_{j} ."
        for idx, column in enumerate(changes)
        for j, p in enumerate(_column_props(table_name, column))
    )
    optionals = " ".join(
        f"OPTIONAL {{ ?s {iri(p)} ?v{idx}_{j} }}"
        for idx, column in enumerate(changes)
        for j, p in enumerate(_column_props(table_name, column))
    )
    inserts: list[str] = []
    for subject in subjects:
        _, pk = _parse(subject)
        row = {**pk, **{k: v for k, v in changes.items() if v is not None}}
        inserts.extend(row_triples(table_name, row, columns=[k for k, v in changes.items() if v is not None]))
    insert_clause = f"INSERT {{ GRAPH {g} {{ {' '.join(inserts)} }} }}" if inserts else ""
    sparql = (
        f"DELETE {{ GRAPH {g} {{ {deletes} }} }} {insert_clause} "
        f"WHERE {{ GRAPH {g} {{ {values('s', subjects)} ?s a {iri(class_iri(table_name))} . {optionals} }} }}"
    )
    client().update(sparql)


def update_row(table_name: str, row_id: str, changes: dict[str, Any]) -> None:
    update_rows(table_name, [row_iri_single(table_name, row_id)], changes)


def _parse(subject: str) -> tuple[str, dict[str, str]]:
    from auto_ontology.rdf.vocab import parse_row_iri

    return parse_row_iri(subject)


def _dependents(table_name: str) -> list[tuple[str, str, str | None]]:
    """``(child_table, child_column, ondelete)`` for every FK pointing here."""
    out: list[tuple[str, str, str | None]] = []
    for table in METADATA.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name == table_name:
                out.append((table.name, fk.parent.name, (fk.ondelete or "").upper() or None))
    return out


def delete_subjects(table_name: str, subjects: Iterable[str]) -> int:
    """Delete rows by subject IRI, following CASCADE and SET NULL like Postgres."""
    subjects = list(dict.fromkeys(subjects))
    if not subjects:
        return 0
    g = iri(data_graph_iri())
    for child_table, child_col, ondelete in _dependents(table_name):
        ref = iri(ref_iri(child_table, child_col))
        rows = client().select(
            f"SELECT DISTINCT ?c WHERE {{ {wrap(f'{values(chr(112), subjects)} ?c {ref} ?p .')} }}"
        )
        children = [r["c"] for r in rows]
        if not children:
            continue
        if ondelete == "CASCADE":
            delete_subjects(child_table, children)
        elif ondelete == "SET NULL":
            update_rows(child_table, children, {child_col: None})
    client().update(
        f"DELETE {{ GRAPH {g} {{ ?s ?p ?o . ?x ?q ?s }} }} "
        f"WHERE {{ GRAPH {g} {{ {values('s', subjects)} ?s ?p ?o . OPTIONAL {{ ?x ?q ?s }} }} }}"
    )
    return len(subjects)


def delete_rows(table_name: str, ids: Iterable[str]) -> int:
    return delete_subjects(table_name, [row_iri_single(table_name, i) for i in ids])
