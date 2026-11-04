# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the ``auto_ontology.dal.pii`` writes (automatic PII classification).

``detach_tag`` and ``get_tag_by_name`` stay calls on ``auto_ontology.dal.tags``
(the tags lane's ports replace them when installed), looked up at call time.
"""
from __future__ import annotations

import importlib
from typing import Any

from auto_ontology.dal_rdf._common import P, class_iri, client, iri, row_id, row_iri_single, wrap
from auto_ontology.dal_rdf._wdata import apply_cells, existing, no_transaction
from auto_ontology.rdf import writes as W

PORTS = [
    "mark_columns_pii_processed",
    "tag_attributes_of_tagged_columns",
    "realign_sql_attribute_pii",
    "untag_attributes_of_column",
    "untag_attributes_of_columns_being_deleted",
]

IN_QUERY_BATCH = 1000
_PII_TAG_NAME = "PII"
_TARGET_COLUMN, _TARGET_COLUMN_ATTRIBUTE, _TARGET_SQL_ATTRIBUTE = "column", "column_attribute", "sql_attribute"


def _tags():
    return importlib.import_module("auto_ontology.dal.tags")


def _sel(sparql: str) -> list[dict[str, Any]]:
    return client().select(sparql)


def _unprocessed(var: str, table: str) -> str:
    """Pattern: ``?var`` has ``pii_processed`` false (NOT NULL upstream, so absent = false)."""
    return (
        f"OPTIONAL {{ ?{var} {iri(f'urn:ao:{table}#pii_processed')} ?{var}_p }} "
        f"FILTER(!BOUND(?{var}_p) || ?{var}_p = false)"
    )


def mark_columns_pii_processed(column_ids: list[str]) -> int:
    """Mark successfully classified catalog columns as processed; returns how many changed."""
    if not column_ids:
        return 0
    marked: list[str] = []
    seen: set[str] = set()
    for offset in range(0, len(column_ids), IN_QUERY_BATCH):
        chunk = [row_iri_single("catalog_column", c) for c in column_ids[offset : offset + IN_QUERY_BATCH]]
        rows = _sel(
            "SELECT DISTINCT ?c WHERE { "
            + wrap(
                f"VALUES ?c {{ {' '.join(iri(x) for x in chunk)} }} ?c a {iri(class_iri('catalog_column'))} . "
                + _unprocessed("c", "catalog_column")
            )
            + " }"
        )
        for r in rows:
            if r["c"] not in seen:
                seen.add(r["c"])
                marked.append(r["c"])
    apply_cells({"catalog_column": {c: {"pii_processed": True} for c in marked}})
    return len(marked)


def _candidates(tag_id: str, kind: str) -> list[str]:
    """Subjects of unprocessed, Term-linked attributes built from a column carrying *tag_id*."""
    tag = iri(row_iri_single("tag", tag_id))
    if kind == "column_attribute":
        path = (
            f"?t {P.tag_target.ref_tag_id} {tag} ; {P.tag_target.ref_column_id} ?col . "
            f"?h {P.column__has_attribute.ref_column_id} ?col ; {P.column__has_attribute.ref_attribute_id} ?a . "
            f"?l {P.column_attribute__term.ref_attribute_id} ?a . ?a a {iri(class_iri('column_attribute'))} . "
        )
    else:
        path = (
            f"?t {P.tag_target.ref_tag_id} {tag} ; {P.tag_target.ref_column_id} ?col . "
            f"?qc {P.sql_query__column.ref_column_id} ?col ; {P.sql_query__column.ref_sql_query_id} ?q . "
            f"?hs {P.sql_attribute__sql.ref_sql_query_id} ?q ; {P.sql_attribute__sql.ref_attribute_id} ?a . "
            f"?l {P.sql_attribute__term.ref_attribute_id} ?a . ?a a {iri(class_iri('sql_attribute'))} . "
        )
    rows = _sel(f"SELECT DISTINCT ?a WHERE {{ {wrap(path + _unprocessed('a', kind))} }}")
    return [r["a"] for r in rows]


def tag_attributes_of_tagged_columns(tag_id: str) -> tuple[int, int]:
    """Copy *tag_id* from tagged columns onto the attributes built from them, once each."""
    applied: list[int] = []
    inserts: list[str] = []
    flags: dict[str, dict[str, dict[str, Any]]] = {}
    for kind, column in (("column_attribute", "column_attribute_id"), ("sql_attribute", "sql_attribute_id")):
        found = _candidates(tag_id, kind)
        already = {
            r["a"]
            for r in _sel(
                "SELECT ?a WHERE { "
                + wrap(
                    f"?t {P.tag_target.ref_tag_id} {iri(row_iri_single('tag', tag_id))} ; "
                    f"{iri(f'urn:ao:tag_target#ref-{column}')} ?a ."
                )
                + " }"
            )
        }
        new = [a for a in found if a not in already]
        for a in new:
            stored = W.apply_defaults("tag_target", {"tag_id": tag_id, column: row_id(a)})
            inserts.extend(W.row_triples("tag_target", stored))
        applied.append(len(new))
        flags[kind] = {a: {"pii_processed": True} for a in found}
    apply_cells(flags, inserts)
    return applied[0], applied[1]


def _sql_attribute_reads_tagged_column(attr_id: str, tag_id: str) -> bool:
    return bool(
        _sel(
            "SELECT ?t WHERE { "
            + wrap(
                f"?t {P.tag_target.ref_tag_id} {iri(row_iri_single('tag', tag_id))} ; {P.tag_target.ref_column_id} ?col . "
                f"?qc {P.sql_query__column.ref_column_id} ?col ; {P.sql_query__column.ref_sql_query_id} ?q . "
                f"?hs {P.sql_attribute__sql.ref_sql_query_id} ?q ; "
                f"{P.sql_attribute__sql.ref_attribute_id} {iri(row_iri_single('sql_attribute', attr_id))} ."
            )
            + " } LIMIT 1"
        )
    )


def realign_sql_attribute_pii(attr_id: str, tag_id: str | None) -> bool:
    """Drop *tag_id* after an expression rewrite if the SQL no longer reads it."""
    if tag_id is not None and _sql_attribute_reads_tagged_column(attr_id, tag_id):
        return False
    with no_transaction():
        if tag_id is not None:
            _tags().detach_tag(tag_id=tag_id, kind=_TARGET_SQL_ATTRIBUTE, item_id=attr_id)
        subject = row_iri_single("sql_attribute", attr_id)
        if subject in existing("sql_attribute", [subject]):
            apply_cells({"sql_attribute": {subject: {"pii_processed": False}}})
    return True


def untag_attributes_of_column(column_id: str, tag_id: str) -> None:
    """Drop *tag_id* from attributes of *column_id* after the column lost it."""
    col = iri(row_iri_single("catalog_column", column_id))
    with no_transaction():
        column_attr_ids = [
            row_id(r["a"])
            for r in _sel(
                "SELECT ?a WHERE { "
                + wrap(
                    f"?h {P.column__has_attribute.ref_column_id} {col} ; {P.column__has_attribute.ref_attribute_id} ?a ."
                )
                + " }"
            )
        ]
        sql_attr_ids = [
            row_id(r["a"])
            for r in _sel(
                "SELECT DISTINCT ?a WHERE { "
                + wrap(
                    f"?qc {P.sql_query__column.ref_column_id} {col} ; {P.sql_query__column.ref_sql_query_id} ?q . "
                    f"?hs {P.sql_attribute__sql.ref_sql_query_id} ?q ; {P.sql_attribute__sql.ref_attribute_id} ?a ."
                )
                + " }"
            )
        ]
        if column_attr_ids:
            for attr_id in column_attr_ids:
                _tags().detach_tag(tag_id=tag_id, kind=_TARGET_COLUMN_ATTRIBUTE, item_id=attr_id)
            apply_cells(
                {"column_attribute": {row_iri_single("column_attribute", a): {"pii_processed": False} for a in column_attr_ids}}
            )
        for attr_id in sql_attr_ids:
            realign_sql_attribute_pii(attr_id, tag_id)


def untag_attributes_of_columns_being_deleted(column_ids: list[str]) -> None:
    """Drop PII from attributes before *column_ids* leave the catalog."""
    ids = list(dict.fromkeys(column_id for column_id in column_ids if column_id))
    if not ids:
        return
    pii_tag = _tags().get_tag_by_name(_PII_TAG_NAME)
    if pii_tag is None:
        return
    pii_id = str(pii_tag["id"])
    with no_transaction():
        for column_id in ids:
            _tags().detach_tag(tag_id=pii_id, kind=_TARGET_COLUMN, item_id=column_id)
        for column_id in ids:
            untag_attributes_of_column(column_id, pii_id)
