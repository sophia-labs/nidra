# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.reset``: wipe a database's catalog rows,
semantic rows and vector-store records.

Containment is a parent reference, so deleting a ``catalog_database`` row removes
the whole catalog tier through ``writes.delete_subjects`` (which follows the
metadata's ``ON DELETE CASCADE``). The vector store is the same ``get_*_vdb()``
upstream calls (gardend's, on this backend).
"""
from __future__ import annotations

import logging

from auto_ontology.dal.reset import ResetResult
from auto_ontology.dal_rdf import _semantic_writes as sw
from auto_ontology.dal_rdf._common import P, client, iri, lit, row_id, wrap, class_iri
from auto_ontology.rdf import writes as W
from auto_ontology.vdb import get_data_vdb, get_semantic_vdb

logger = logging.getLogger(__name__)

PORTS = ["delete_semantic_layer", "delete_data_layer", "delete_all_data"]


def _table_ids(database_name: str) -> set[str]:
    """The tables of one database."""
    body = (
        f"?d a {iri(class_iri('catalog_database'))} ; {P.catalog_database.name} {lit(database_name)} . "
        f"?sc {P.catalog_schema.ref_database_id} ?d . "
        f"?t {P.catalog_table.ref_schema_id} ?sc ."
    )
    return {row_id(r["t"]) for r in client().select(f"SELECT DISTINCT ?t WHERE {{ {wrap(body)} }}")}


def _only_ours(pairs: list[tuple[str, str]], tables: set[str]) -> list[str]:
    """Owners with a link inside *tables* and none outside."""
    inside = {o for o, t in pairs if t in tables}
    outside = {o for o, t in pairs if t not in tables}
    return sorted(inside - outside)


def _delete_scoped_semantic(database_name: str) -> int:
    tables = _table_ids(database_name)
    deleted = 0

    # Term: reached through table__term.
    pairs = [(r["term_id"], r["table_id"]) for r in sw.link_rows("table__term", ["term_id", "table_id"])]
    deleted += W.delete_rows("term", _only_ours(pairs, tables))

    # ColumnAttribute: owned outright by one table (a plain string column).
    ca = [r["id"] for r in sw.link_rows("column_attribute", ["id", "table_id"]) if r["table_id"] in tables]
    deleted += W.delete_rows("column_attribute", ca)

    # SqlAttribute and CustomAnalysis: reached through the tables their SQL hits.
    statement_tables = sw.link_rows("sql_query__table", ["sql_query_id", "table_id"])
    by_query: dict[str, set[str]] = {}
    for r in statement_tables:
        by_query.setdefault(r["sql_query_id"], set()).add(r["table_id"])
    for owner_table, link_table, owner_col in (
        ("sql_attribute", "sql_attribute__sql", "attribute_id"),
        ("custom_analysis", "custom_analysis__sql", "analysis_id"),
    ):
        touched = [
            (r[owner_col], t)
            for r in sw.link_rows(link_table, [owner_col, "sql_query_id"])
            for t in by_query.get(r["sql_query_id"], ())
        ]
        deleted += W.delete_rows(owner_table, _only_ours(touched, tables))
    return deleted


def _delete_all_semantic() -> int:
    deleted = 0
    for table in (
        "term",
        "column_attribute",
        "sql_attribute",
        "text_attribute",
        "analysis",
        "pql_analysis",
        "custom_analysis",
    ):
        deleted += W.delete_rows(table, sw.all_ids(table))
    return deleted


def _delete_semantic_rows(database_name: str | None) -> int:
    deleted = _delete_all_semantic() if database_name is None else _delete_scoped_semantic(database_name)
    logger.info("delete_semantic_layer: removed %d semantic rows for database %s", deleted, database_name or "<all>")
    return deleted


def _delete_semantic_vectors(database_name: str | None) -> int:
    semantic_vdb = get_semantic_vdb()
    if database_name is None:
        semantic_deleted = semantic_vdb.delete_all()
    else:
        semantic_deleted = len(semantic_vdb.delete_by_database(database_name))
    logger.info(
        "delete_semantic_layer: removed %d semantic vector rows for database %s",
        semantic_deleted,
        database_name or "<all>",
    )
    return semantic_deleted


def delete_semantic_layer(database_name: str | None = None) -> int:
    """Delete semantic rows and their vectors. Catalog rows survive.

    Returns the number of **vector-store rows** deleted, not database rows.
    """
    _delete_semantic_rows(database_name)
    return _delete_semantic_vectors(database_name)


def _delete_orphaned_statements(candidate_ids: list[str] | None = None) -> int:
    """Delete ``sql_query`` rows nothing references any more (scoped to *candidate_ids* when given)."""
    if candidate_ids is not None and not candidate_ids:
        return 0
    referenced: set[str] = set()
    for link in ("sql_query__table", "sql_query__column", "sql_attribute__sql", "custom_analysis__sql"):
        referenced.update(r["sql_query_id"] for r in sw.link_rows(link, ["sql_query_id"]))
    pool = sw.all_ids("sql_query") if candidate_ids is None else sorted(set(candidate_ids))
    orphans = [i for i in pool if i not in referenced and sw.exists("sql_query", i)] if candidate_ids is not None else [
        i for i in pool if i not in referenced
    ]
    removed = W.delete_rows("sql_query", orphans)
    if removed:
        logger.info("delete_data_layer: removed %d orphaned statement rows", removed)
    return removed


def _delete_data_rows(database_name: str | None = None) -> int:
    """Catalog rows (cascading through containment) and the statements they orphaned."""
    candidates: list[str] | None = None
    if database_name is not None:
        tables = _table_ids(database_name)
        candidates = sorted(
            {r["sql_query_id"] for r in sw.link_rows("sql_query__table", ["sql_query_id", "table_id"]) if r["table_id"] in tables}
        )

    if database_name is None:
        db_ids = sw.all_ids("catalog_database")
    else:
        db_ids = [
            r["id"] for r in sw.link_rows("catalog_database", ["id"], {"name": database_name})
        ]
    deleted = W.delete_rows("catalog_database", db_ids)
    logger.info("delete_data_layer: removed %d database rows for database %s", deleted, database_name or "<all>")
    _delete_orphaned_statements(candidates)
    return deleted


def _delete_data_vectors(database_name: str | None) -> int:
    data_vdb = get_data_vdb()
    if database_name is None:
        data_deleted = data_vdb.delete_all()
    else:
        data_deleted = len(data_vdb.delete_by_database(database_name))
    logger.info(
        "delete_data_layer: removed %d data vector rows for database %s", data_deleted, database_name or "<all>"
    )
    return data_deleted


def delete_data_layer(database_name: str | None = None) -> int:
    """Delete a database's catalog rows and its vectors. Returns the vector-row count."""
    _delete_data_rows(database_name)
    return _delete_data_vectors(database_name)


def delete_all_data(database_name: str | None = None) -> ResetResult:
    """Delete every trace of a database, both layers. Semantic first: it is found *through* the catalog."""
    _delete_semantic_rows(database_name)
    _delete_data_rows(database_name)
    semantic_rows = _delete_semantic_vectors(database_name)
    data_rows = _delete_data_vectors(database_name)
    return ResetResult(database_name=database_name, data_rows=data_rows, semantic_rows=semantic_rows)
