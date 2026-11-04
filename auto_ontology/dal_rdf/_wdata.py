# SPDX-License-Identifier: Apache-2.0
"""Shared helpers of the data-lane write ports (datasources, attributes, pii,
connections): batched per-subject column writes in ONE write (``rdf.writes.commit``), and the
small existence reads the SQL ``WHERE`` clauses become.

``apply_cells`` is the generic operation ``rdf/writes.py`` lacks: ``update_rows``
sets one ``changes`` dict on every subject, while a ``CASE``/``VALUES``-style SQL
update gives each row its own value. Candidate for ``writes.py``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from auto_ontology.dal_rdf._common import client, iri, wrap
from auto_ontology.rdf import writes as W
from auto_ontology.rdf.vocab import class_iri, row_iri, row_iri_single

#: ``{table: {subject IRI: {column: value}}}``; ``None`` clears the column.
Cells = dict[str, dict[str, dict[str, Any]]]


@contextmanager
def no_transaction() -> Iterator[None]:
    """``write_transaction`` on this backend: under ``NIDRA_WRITES=commit`` the
    block's writes go as one request (``writes.collecting``); under ``sparql`` a
    no-op (no multi-request rollback)."""
    if W.write_mode() == "commit":
        with W.collecting():
            yield
        return
    yield


def apply_cells(cells: Cells | None = None, inserts: Iterable[W.Op] = ()) -> None:
    """Set per-subject column values and insert rows' ops, as one write.

    ``inserts`` are ops (``W.row_ops``). The caller has already checked that every
    subject exists (an ``UPDATE`` that matches nothing writes nothing; an insert
    would write it anyway).
    """
    ops: list[W.Op] = list(inserts)
    for table, by_subject in (cells or {}).items():
        for subject, changes in by_subject.items():
            if changes:
                ops.extend(W.update_ops(table, subject, changes))
    W.commit(ops)


def existing(table: str, subjects: Iterable[str]) -> set[str]:
    """The subjects among ``subjects`` that are rows of ``table``."""
    subjects = list(dict.fromkeys(subjects))
    out: set[str] = set()
    for i in range(0, len(subjects), 2000):
        chunk = subjects[i : i + 2000]
        terms = " ".join(iri(x) for x in chunk)
        rows = client().select(
            f"SELECT ?s WHERE {{ {wrap(f'VALUES ?s {{ {terms} }} ?s a {iri(class_iri(table))} .')} }}"
        )
        out.update(r["s"] for r in rows)
    return out


def row_exists(table: str, row_id: str) -> bool:
    subject = row_iri_single(table, row_id)
    return subject in existing(table, [subject])


def link_iri(table: str, **pk: str) -> str:
    return row_iri(table, pk)
