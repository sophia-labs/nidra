# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.catalog.store.schemas``: the schema/table/column
ingest writer and its readers.

``load_schema_from_graph`` is not ported: it only composes ``get_schema_tables``
and ``get_schema_columns`` through module globals, which the install replaces.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from auto_ontology.catalog.store.registry import entity_spec
from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, row_id, row_iri_single, wrap
from auto_ontology.dal_rdf.store_queries import _existing_lists
from auto_ontology.dal_rdf.store_rows import Upserter, commit, existing_subjects, new_row
from auto_ontology.rdf import writes
from auto_ontology.rdf.writes import row_subject

logger = logging.getLogger(__name__)

TARGET = "auto_ontology.catalog.store.schemas"
PORTS = [
    "get_schema_columns",
    "get_schema_tables",
    "get_schemas_ids_and_names",
    "get_table_ids",
    "get_column_ids",
    "add_schemas_edge",
    "merge_schema_nodes",
    "merge_schema_edges",
    "add_fks",
    "delete_old_fks",
    "reset_pks",
    "add_pks",
]


def _source_owned(node):
    from auto_ontology.catalog.store.schemas import _source_owned as source_owned

    return source_owned(node)


def _cls(table: str) -> str:
    return iri(class_iri(table))


def _walk(database_name: str, schema_name: str | None = None) -> str:
    """Pattern binding ?db ?sc ?t (database, schema, table) for a database name."""
    schema_name_pattern = f"; {P.catalog_schema.name} {lit(schema_name)} " if schema_name is not None else f"; {P.catalog_schema.name} ?sn "
    return (
        f"?db a {_cls('catalog_database')} ; {P.catalog_database.name} {lit(database_name)} . "
        f"?sc a {_cls('catalog_schema')} ; {P.catalog_schema.ref_database_id} ?db {schema_name_pattern}. "
        f"?t a {_cls('catalog_table')} ; {P.catalog_table.ref_schema_id} ?sc ; {P.catalog_table.name} ?tn ."
    )


def get_schema_columns(database_name, schema_name):
    from auto_ontology.catalog.normalize import normalize_columns

    rows = client().select(
        "SELECT ?c ?tn ?cn ?data_type ?is_nullable WHERE { "
        + wrap(
            _walk(database_name, schema_name)
            + f" ?c a {_cls('catalog_column')} ; {P.catalog_column.ref_table_id} ?t ; {P.catalog_column.name} ?cn . "
            f"OPTIONAL {{ ?c {P.catalog_column.data_type} ?data_type }} "
            f"OPTIONAL {{ ?c {P.catalog_column.is_nullable} ?is_nullable }}"
        )
        + " } ORDER BY ?c"
    )
    return normalize_columns(
        pd.DataFrame(
            [
                {
                    "database": database_name,
                    "table_schema": schema_name,
                    "table_name": r["tn"],
                    "column_name": r["cn"],
                    "id": row_id(r["c"]),
                    "data_type": r.get("data_type"),
                    "is_nullable": r.get("is_nullable"),
                }
                for r in rows
            ]
        )
    )


def get_schema_tables(database_name, schema_name):
    from auto_ontology.catalog.normalize import normalize_tables

    rows = client().select(
        "SELECT ?t ?tn ?description ?table_type WHERE { "
        + wrap(
            _walk(database_name, schema_name)
            + f" OPTIONAL {{ ?t {P.catalog_table.description} ?description }} "
            f"OPTIONAL {{ ?t {P.catalog_table.table_type} ?table_type }}"
        )
        + " } ORDER BY ?t"
    )
    frame = pd.DataFrame(
        [
            {
                "database": database_name,
                "table_schema": schema_name,
                "table_name": r["tn"],
                "id": row_id(r["t"]),
                "description": r.get("description"),
                "table_type": r.get("table_type"),
            }
            for r in rows
        ]
    )
    if not frame.empty and "created" not in frame.columns:
        frame["created"] = None
    return normalize_tables(frame)


def get_schemas_ids_and_names(database_id: str = None, database_name: str = None):
    if database_id:
        scope = f"?sc {P.catalog_schema.ref_database_id} {iri(row_iri_single('catalog_database', database_id))} ."
    elif database_name:
        scope = (
            f"?sc {P.catalog_schema.ref_database_id} ?db . "
            f"?db a {_cls('catalog_database')} ; {P.catalog_database.name} {lit(database_name)} ."
        )
    else:
        scope = f"?sc {P.catalog_schema.ref_database_id} ?db ."
    rows = client().select(
        "SELECT ?sc ?sn WHERE { "
        + wrap(f"?sc a {_cls('catalog_schema')} ; {P.catalog_schema.name} ?sn . {scope}")
        + " } ORDER BY ?sc"
    )
    return [{"schema_name": r["sn"], "schema_id": row_id(r["sc"])} for r in rows]


def _tables_by_name(database_name: str) -> list[dict]:
    rows = client().select(
        "SELECT ?t ?sn ?tn WHERE { "
        + wrap(_walk(database_name).replace("; ", "; ", 1) + f" ?sc {P.catalog_schema.name} ?sn .")
        + " } ORDER BY ?t"
    )
    return [{"table_schema": r["sn"], "table_name": r["tn"], "id": row_id(r["t"])} for r in rows]


def _columns_by_name(database_name: str) -> list[dict]:
    rows = client().select(
        "SELECT ?c ?sn ?tn ?cn ?pii WHERE { "
        + wrap(
            _walk(database_name)
            + f" ?sc {P.catalog_schema.name} ?sn . ?c a {_cls('catalog_column')} ; {P.catalog_column.ref_table_id} ?t ; "
            f"{P.catalog_column.name} ?cn . OPTIONAL {{ ?c {P.catalog_column.pii_processed} ?pii }}"
        )
        + " } ORDER BY ?c"
    )
    return [
        {"table_schema": r["sn"], "table_name": r["tn"], "column_name": r["cn"], "id": row_id(r["c"]), "pii_processed": r.get("pii")}
        for r in rows
    ]


def get_table_ids(tables_df: pd.DataFrame, database_name: str) -> pd.DataFrame:
    """Add an ``id`` column carrying each table's stored id, or None."""
    if tables_df is None or tables_df.empty:
        return tables_df
    rows = _tables_by_name(database_name)
    if not rows:
        return tables_df
    return tables_df.merge(pd.DataFrame(rows), on=["table_name", "table_schema"], how="left")


def get_column_ids(columns_df: pd.DataFrame, database_name: str) -> pd.DataFrame:
    """Add an ``id`` column carrying each column's stored id, or None."""
    if columns_df is None or columns_df.empty:
        return columns_df
    rows = [
        {k: r[k] for k in ("table_schema", "table_name", "column_name", "id", "pii_processed")}
        for r in _columns_by_name(database_name)
    ]
    if not rows:
        return columns_df
    return columns_df.merge(pd.DataFrame(rows), on=["table_name", "table_schema", "column_name"], how="left")


def _table_ids_by_name(database_name: str) -> dict[tuple[str, str], str]:
    return {(r["table_schema"], r["table_name"]): r["id"] for r in _tables_by_name(database_name)}


def _column_ids_by_name(database_name: str) -> dict[tuple[str, str, str], str]:
    return {(r["table_schema"], r["table_name"], r["column_name"]): r["id"] for r in _columns_by_name(database_name)}


def add_schemas_edge(edge, created):
    """Upsert a parent and child, linking them by the child's parent column."""
    node_from, node_to = edge[0], edge[1]
    try:
        up = Upserter()
        parent_id = up.upsert(node_from.get_label(), node_from.get_match_props(), node_from.get_properties())
        up.upsert(node_to.get_label(), node_to.get_match_props(), node_to.get_properties(), parent_id=parent_id)
        up.flush()
    except Exception as err:
        logger.exception(err)
        raise Exception(f'Error in "add_schemas_edge" when adding edge: {edge}')


def merge_schema_nodes(nodes, created, pending):
    """Hold each row's properties until its parent is known."""
    up = Upserter()
    try:
        for node in nodes:
            node_id = node["props"].get("id") or node["match_props"].get("id")
            if node_id is None:
                up.upsert(node["label"], node["match_props"], node["props"], on_match=_source_owned(node))
                continue
            pending.hold(node_id, node)
    finally:
        up.flush()


def merge_schema_edges(edges, from_label, to_label, pending):
    """Write each child row, now that its parent id is known (one request)."""
    child = entity_spec(to_label)
    if child.parent_column is None:
        raise ValueError(f"{to_label} has no parent column")

    work = []
    for edge in edges:
        child_id, parent_id = edge["uid"], edge["vid"]
        work.append((child_id, parent_id, pending.claim(child_id)))

    up = Upserter()
    by_label: dict[str, list[dict]] = {}
    for _, _, node in work:
        if node is not None:
            by_label.setdefault(entity_spec(node["label"]).label, []).append(node["match_props"])
    try:
        for label, matches in by_label.items():
            up.prefetch(label, matches)
        for child_id, parent_id, node in work:
            if node is not None:
                up.upsert(
                    node["label"], node["match_props"], node["props"], parent_id=parent_id, on_match=_source_owned(node)
                )
            else:
                up.set_columns(child.table.name, child_id, {child.parent_column: parent_id})
    finally:
        up.flush()


def add_fks(fks_df, last_seen, database_name: str):
    """Record each foreign key, stamping *last_seen* (one read of ids, one write)."""
    if fks_df is None or fks_df.empty:
        return

    ids = _column_ids_by_name(database_name)
    rows = []
    seen = set()
    for row in fks_df.to_dict(orient="records"):
        source = ids.get((row["table_schema"], row["table_name"], row["column_name"]))
        target = ids.get((row["referenced_schema"], row["referenced_table"], row["referenced_column"]))
        if source is None or target is None:
            continue
        if (source, target) in seen:
            continue
        seen.add((source, target))
        rows.append({"source_column_id": source, "target_column_id": target, "last_seen": last_seen})
    if not rows:
        return

    table = "column__foreign_key"
    alive = existing_subjects(table, [row_subject(table, r) for r in rows])
    inserts = [new_row(table, r) for r in rows if row_subject(table, r) not in alive]
    updates = [(table, row_subject(table, r), {"last_seen": last_seen}) for r in rows if row_subject(table, r) in alive]
    commit({table: inserts}, updates)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def delete_old_fks(last_seen, database_name: str):
    """Remove foreign keys this ingest did not see (scoped to the database)."""
    if not isinstance(last_seen, datetime):
        last_seen = datetime.fromisoformat(str(last_seen))
    rows = client().select(
        "SELECT ?fk ?ls WHERE { "
        + wrap(
            f"?fk a {_cls('column__foreign_key')} ; {iri(writes.prop_iri('column__foreign_key', 'last_seen'))} ?ls ; "
            f"{iri(writes.ref_iri('column__foreign_key', 'source_column_id'))} ?src . "
            f"?src {P.catalog_column.ref_table_id} ?t . "
            f"?t {P.catalog_table.ref_schema_id} ?sc . ?sc {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} {lit(database_name)} ."
        )
        + " }"
    )
    # Rows without last_seen (imported keys) never match: `last_seen IS NOT NULL AND <> :ls`.
    stale = [r["fk"] for r in rows if _utc(r["ls"]) != _utc(last_seen)]
    writes.delete_subjects("column__foreign_key", stale)


def _tables_of_database(database_name: str) -> list[str]:
    rows = client().select(
        "SELECT DISTINCT ?t WHERE { " + wrap(_walk(database_name)) + " } ORDER BY ?t"
    )
    return [r["t"] for r in rows]


def reset_pks(database_name: str):
    commit(updates=[("catalog_table", t, {"pk": None}) for t in _tables_of_database(database_name)])


def add_pks(pks_df, database_name: str):
    """Append each primary-key column name to its table's ``pk`` array."""
    if pks_df is None or pks_df.empty:
        return

    ids = _table_ids_by_name(database_name)

    if "ordinal_position" in pks_df.columns:
        pks_df = pks_df.assign(
            _key_position=pd.to_numeric(pks_df["ordinal_position"], errors="coerce")
        ).sort_values("_key_position", kind="stable")

    parts: dict[str, list[str]] = {}
    for row in pks_df.to_dict(orient="records"):
        table_id = ids.get((row["table_schema"], row["table_name"]))
        if table_id is None:
            continue
        parts.setdefault(table_id, []).append(row["column_name"])

    if not parts:
        return

    subjects = {t: row_iri_single("catalog_table", t) for t in parts}
    stored = _existing_lists("catalog_table", list(subjects.values()), "pk")
    commit(
        updates=[
            ("catalog_table", subjects[t], {"pk": list(stored.get(subjects[t]) or []) + cols})
            for t, cols in parts.items()
        ]
    )
