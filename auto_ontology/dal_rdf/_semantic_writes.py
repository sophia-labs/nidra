# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by the semantic write ports (terms, sql_attributes,
custom_analyses, pql_analyses, reset). Underscore-named: not installed as a port.

Generic operations missing from ``auto_ontology.rdf.writes`` that these ports
needed (candidates to move there): ``insert_batch`` (rows of several tables in
ONE write), ``link_rows`` (link-table rows by a column value),
``delete_link_ops`` (the deletion of link rows as composable ops),
``raise_unique`` / ``require_row`` (the IntegrityErrors Postgres would raise).
"""
from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.exc import IntegrityError

from auto_ontology.dal_rdf._common import client, iri, lit, wrap
from auto_ontology.dal_rdf.sql_attributes import read_rows  # noqa: F401  (re-export)
from auto_ontology.rdf import writes as W
from auto_ontology.rdf.vocab import class_iri, prop_iri, row_id, row_iri_single


def graph() -> str:
    return iri(W.data_graph_iri())


def exists(table: str, row: str) -> bool:
    sparql = (
        f"SELECT ?s WHERE {{ {wrap(f'VALUES ?s {{ {iri(row_iri_single(table, row))} }} ?s a {iri(class_iri(table))} .')} }} LIMIT 1"
    )
    return bool(client().select(sparql))


def all_ids(table: str) -> list[str]:
    rows = client().select(f"SELECT ?s WHERE {{ {wrap(f'?s a {iri(class_iri(table))} .')} }}")
    return sorted(row_id(r["s"]) for r in rows)


def link_rows(table: str, cols: list[str], where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Rows of a link table as ``{col: value, "_s": subject}``; ``where`` pins literal columns."""
    body = f"?s a {iri(class_iri(table))} ."
    for i, c in enumerate(cols):
        body += f" ?s {iri(prop_iri(table, c))} ?c{i} ."
    for c, v in (where or {}).items():
        body += f" ?s {iri(prop_iri(table, c))} {lit(v)} ."
    sel = " ".join(f"?c{i}" for i in range(len(cols)))
    out = []
    for r in client().select(f"SELECT DISTINCT ?s {sel} WHERE {{ {wrap(body)} }}"):
        row = {c: r[f"c{i}"] for i, c in enumerate(cols)}
        row["_s"] = r["s"]
        out.append(row)
    return out


def insert_batch(batch: dict[str, Iterable[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Insert rows of several tables (defaults applied) in ONE write."""
    stored: dict[str, list[dict[str, Any]]] = {}
    ops: list[W.Op] = []
    for table, rows in batch.items():
        stored[table] = [W.apply_defaults(table, r) for r in rows]
        for row in stored[table]:
            ops.extend(W.row_ops(table, row))
    W.commit(ops)
    return stored


def delete_link_ops(table: str, subjects: Iterable[str]) -> list[W.Op]:
    """The ops deleting link-table rows (``W.delete_ops``: their dependents, if any, cascade)."""
    subjects = list(dict.fromkeys(subjects))
    return W.delete_ops(table, subjects) if subjects else []


def raise_unique(table: str, constraint: str) -> None:
    raise IntegrityError(
        f"write to {table}", {}, Exception(f'duplicate key value violates unique constraint "{constraint}"')
    )


def raise_fk(table: str, constraint: str) -> None:
    raise IntegrityError(
        f"write to {table}", {}, Exception(f'insert or update violates foreign key constraint "{constraint}"')
    )
