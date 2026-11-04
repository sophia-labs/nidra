# SPDX-License-Identifier: Apache-2.0
"""write_diff drivers for the catalog store ports (``dal_rdf.store_*``).

Each case takes JSON kwargs (``<token>`` fixture ids) and returns a read-back of
what it wrote through the store readers, so ``write_diff`` compares the writer
*and* the readers on both backends, besides the DAL reads it captures itself.
Run, for example::

    uv run --no-sync python -m dev_tools.fixtures.write_diff \\
        dev_tools.fixtures.write_cases.store:upsert_new_table --kwargs '{"schema_id": "<schema:pagila.public>"}'
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pandas as pd

import auto_ontology.dal  # noqa: F401  (installs the RDF ports before the store modules are imported)
from auto_ontology.catalog.constants import Labels, Props
from auto_ontology.catalog.model.node import CatalogNode
from auto_ontology.catalog.store import db as store_db
from auto_ontology.catalog.store import queries as store_queries
from auto_ontology.catalog.store import schemas as store_schemas
from auto_ontology.catalog.store.rows import resolve_id, upsert_row
from auto_ontology.dal.session import write_transaction

STAMP = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)


def _readback(database: str = "pagila", schema: str = "public") -> dict:
    cols = store_schemas.get_schema_columns(database, schema)
    tabs = store_schemas.get_schema_tables(database, schema)
    drop = [c for c in ("id",) if c in cols.columns]
    return {
        "tables": tabs.drop(columns=[c for c in ("id",) if c in tabs.columns]).to_dict(orient="records"),
        "columns": cols.drop(columns=drop).to_dict(orient="records"),
        "schemas": store_schemas.get_schemas_ids_and_names(database_name=database),
    }


def _node(label, props, match):
    return CatalogNode(name=props.get("name", ""), label=label, props=props, match_props=match)


def _new_table(schema_id, name, cols):
    table_id = str(uuid.uuid4())
    upsert_row(Labels.TABLE, {"id": table_id}, {"name": name, "id": table_id, "table_type": "base table"}, parent_id=schema_id)
    out = [table_id]
    for i, c in enumerate(cols, 1):
        cid = str(uuid.uuid4())
        upsert_row(
            Labels.COLUMN,
            {"id": cid},
            {"name": c, "id": cid, "data_type": "text", "is_nullable": bool(i % 2), "ordinal_position": str(i)},
            parent_id=table_id,
        )
        out.append(cid)
    return out


# -- rows -----------------------------------------------------------------------


def upsert_new_table(schema_id):
    """A table with two columns under pagila.public (string ordinal coerced to int)."""
    ids = _new_table(schema_id, "lane_new_table", ["alpha", "beta"])
    return {"n": len(ids), **_readback()}


def upsert_existing_with_on_match(schema_id, table_id):
    """Existing table, matched by id: on_match refreshes table_type/pk, description untouched."""
    got = upsert_row(
        Labels.TABLE,
        {"id": table_id},
        {"name": "film", "id": table_id, "description": "ignored: only on insert"},
        parent_id=schema_id,
        on_match={"table_type": "view", "pk": ["film_id"], "id": str(uuid.uuid4())},
    )
    return {"same_id": got == table_id, **_readback()}


def upsert_existing_by_natural_key(schema_id, table_id):
    """Matched by (parent, name) with a freshly generated incoming id: the stored id wins."""
    got = upsert_row(
        Labels.TABLE,
        {"name": "film"},
        {"name": "film", "id": str(uuid.uuid4()), "table_type": "base table"},
        parent_id=schema_id,
        on_match={"table_type": "materialized view"},
    )
    return {"same_id": got == table_id, **_readback()}


def upsert_no_on_match(schema_id, table_id):
    got = upsert_row(Labels.TABLE, {"id": table_id}, {"name": "film", "id": table_id, "table_type": "view"}, parent_id=schema_id)
    return {"same_id": got == table_id, **_readback()}


def upsert_database_and_schema():
    """New database by name, then a schema through database_name; both re-run are idempotent."""
    db1 = upsert_row(Labels.DB, {"name": "lane_db"}, {"name": "lane_db", "dialect": "postgres"})
    db2 = upsert_row(Labels.DB, {"name": "lane_db"}, {"name": "lane_db"})
    sc1 = upsert_row(Labels.SCHEMA, {"database_name": "lane_db", "name": "lane_s"}, {"name": "lane_s"}, parent_id=db1)
    sc2 = upsert_row(Labels.SCHEMA, {"database_name": "lane_db", "name": "lane_s"}, {"name": "lane_s"}, parent_id=db1)
    return {"db_same": db1 == db2, "schema_same": sc1 == sc2, **_readback("lane_db", "lane_s")}


def resolve_ids(schema_id, table_id):
    return {
        "by_id": resolve_id(Labels.TABLE, {"id": table_id}) == table_id,
        "by_key": resolve_id(Labels.TABLE, {"name": "film"}, parent_id=schema_id) == table_id,
        "by_db_name": resolve_id(Labels.SCHEMA, {"database_name": "pagila", "name": "public"}) == schema_id,
        "unknown_id_falls_to_key": resolve_id(Labels.TABLE, {"id": "nope", "name": "film"}, parent_id=schema_id) == table_id,
        "absent": resolve_id(Labels.TABLE, {"name": "no_such"}, parent_id=schema_id),
        "absent_db": resolve_id(Labels.SCHEMA, {"database_name": "no_such_db", "name": "public"}),
        "sql_absent": resolve_id(Labels.SQL, {"sql_full_query": "SELECT 1 /* absent */"}),
    }


# -- db.py ----------------------------------------------------------------------


def db_exists_and_update(db_id):
    node = _node(Labels.DB, {"name": "pagila"}, {"name": "pagila"})
    ghost = _node(Labels.DB, {"name": "ghost"}, {"name": "ghost"})
    before = store_db.db_exists(node)
    store_db.update_node_property("db", db_id, {"pulled": "2026-11-03T12:00:00+00:00", "dialect": "postgres", "bogus": 1})
    store_db.update_node_property("Database", db_id, {"nothing": 1})
    store_db.update_node_property("db", "no-such-id", {"dialect": "x"})
    return {"before": [before[0] == db_id, before[1]], "ghost": store_db.db_exists(ghost), **_readback()}


def delete_table_cascade(table_id):
    """film_actor takes its columns with it."""
    store_db.delete_table(table_id)
    return _readback()


def delete_schema_cascade(schema_id):
    store_db.delete_schema(schema_id)
    return _readback("pagila", "analytics") | {"public": _readback()["tables"]}


def delete_columns(column_ids):
    store_db.delete_columns_batch(column_ids)
    store_db.delete_columns_batch([])
    store_db.delete_columns_batch(["no-such-column"])
    return _readback()


def edge_batch(schema_id, table_id):
    """New table + columns under a schema, plus an existing table re-linked with fresh props."""
    new_table, c1, c2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    edges = [
        {"from_label": Labels.SCHEMA, "from_identProps": {"id": schema_id}, "v_props": {"id": schema_id},
         "to_label": Labels.TABLE, "to_identProps": {"id": new_table},
         "u_props": {"id": new_table, "name": "lane_edge_table", "table_type": "base table"}, "optional_edge_props": {}},
        {"from_label": Labels.TABLE, "from_identProps": {"id": new_table}, "v_props": {"id": new_table, "name": "lane_edge_table"},
         "to_label": Labels.COLUMN, "to_identProps": {"id": c1},
         "u_props": {"id": c1, "name": "one", "data_type": "int4", "ordinal_position": 1}, "optional_edge_props": {}},
        {"from_label": Labels.TABLE, "from_identProps": {"id": new_table}, "v_props": {"id": new_table, "name": "lane_edge_table"},
         "to_label": Labels.COLUMN, "to_identProps": {"id": c2},
         "u_props": {"id": c2, "name": "two", "data_type": "text", "ordinal_position": "2"}, "optional_edge_props": {}},
        {"from_label": Labels.SCHEMA, "from_identProps": {"id": schema_id}, "v_props": {"id": schema_id},
         "to_label": Labels.TABLE, "to_identProps": {"id": table_id},
         "u_props": {"id": table_id, "name": "film", "table_type": "view"}, "optional_edge_props": {}},
    ]
    store_db.add_schemas_edge_batch(edges, created=STAMP)
    store_db.add_schemas_edge_batch([], created=STAMP)
    return _readback()


def edge_batch_conflict(schema_id):
    """A bad row (a table without a name) fails with the wrapped message on both."""
    edges = [{"from_label": Labels.SCHEMA, "from_identProps": {"id": schema_id}, "v_props": {"id": schema_id},
              "to_label": Labels.TABLE, "to_identProps": {"id": "x1"}, "u_props": {"id": "x1"}, "optional_edge_props": {}}]
    try:
        store_db.add_schemas_edge_batch(edges, created=STAMP)
    except Exception as err:  # noqa: BLE001
        return {"error": str(err).split(":")[0], **_readback()}
    return {"error": None}


def update_properties_batch(table_id, column_id, other_column_id):
    """Description coalesce: film.title keeps its text, a column without one gains it."""
    store_db.update_properties_in_graph_batch(
        [
            {"id": table_id, "label": Labels.TABLE, "props": {"description": "a table description", "table_type": "view", "id": "ignored"}},
            {"id": column_id, "label": Labels.COLUMN, "props": {"description": "first", "data_type": "citext"}},
            {"id": column_id, "label": Labels.COLUMN, "props": {"description": "second"}},
            {"id": other_column_id, "label": Labels.COLUMN, "props": {"data_type": "int8", "bogus": 1}},
        ]
    )
    store_db.update_properties_in_graph_batch([])
    return _readback()


# -- queries.py -----------------------------------------------------------------


def add_query_over_film(table_id, column_id, other_column_id):
    """A statement over film: table link, two column links, a column join with refs (twice)."""
    text = "SELECT title FROM film JOIN film_actor USING (film_id) /* lane */"
    sql = lambda: _node(Labels.SQL, {"sql_full_query": text, "nodes_count": 7, "join_count": 1, "total_counter": 3}, {"sql_full_query": text})
    table = lambda: _node(Labels.TABLE, {"id": table_id}, {"id": table_id})
    col = lambda i: _node(Labels.COLUMN, {"id": i}, {"id": i})
    store_queries.add_query(
        [
            (sql(), table(), {Props.SQL_ID: "s1"}),
            (sql(), col(column_id), {Props.SQL_ID: "s1"}),
            (col(column_id), col(other_column_id), {Props.JOIN: "film.a=film.b", "refs": ["s1|a=b"]}),
            (col(column_id), col(other_column_id), {Props.JOIN: "film.a=film.b", "refs": ["s2|a=b", "s1|a=b"]}),
            (col(other_column_id), col(column_id), {Props.UNION: "u", "refs": ["s1|u"]}),
        ]
    )
    store_queries.add_query([])
    again = store_queries.get_sql_by_full_query(text)
    store_queries.add_query([(sql(), table(), {Props.SQL_ID: "s1"})])  # idempotent
    frame = store_queries.load_sqls_to_tables()
    return {
        "found": again is not None,
        "missing": store_queries.get_sql_by_full_query("SELECT nothing"),
        "sqls": frame.drop(columns=["sql_id"]).to_dict(orient="records"),
    }


def add_query_nested_rejected(table_id):
    text = "SELECT 1"
    sql = _node(Labels.SQL, {"sql_full_query": text, "bad": {"a": 1}}, {"sql_full_query": text})
    try:
        store_queries.add_query([(sql, _node(Labels.TABLE, {"id": table_id}, {"id": table_id}), {Props.SQL_ID: "s"})])
    except ValueError as err:
        return {"error": str(err).splitlines()[0]}


def counters(table_id):
    text = "SELECT count(*) FROM film /* counters */"
    sql = _node(Labels.SQL, {"sql_full_query": text, "total_counter": 2, "last_query_timestamp": STAMP}, {"sql_full_query": text})
    table = _node(Labels.TABLE, {"id": table_id}, {"id": table_id})
    store_queries.add_query([(sql, table, {Props.SQL_ID: "c"})])
    sql_id = store_queries.get_sql_by_full_query(text)
    bump = _node(Labels.SQL, {"sql_full_query": text, "last_query_timestamp": LATER, "total_counter": 5}, {"sql_full_query": text})
    store_queries.update_counters_and_timestamps_for_query_and_affected_data(sql_id, bump)
    store_queries.update_counters_and_timestamps_for_query_and_affected_data(sql_id, bump, False)
    store_queries.update_counters_and_timestamps_for_query_and_affected_data("no-such-sql", bump)
    return {"rows": store_queries.load_sqls_to_tables().drop(columns=["sql_id"]).to_dict(orient="records")}


# -- schemas.py -----------------------------------------------------------------


def schemas_reads():
    tabs = pd.DataFrame({"table_name": ["film", "nope"], "table_schema": ["public", "public"]})
    cols = pd.DataFrame({"table_name": ["film", "film"], "table_schema": ["public"] * 2, "column_name": ["title", "zzz"]})
    return {
        "tables": store_schemas.get_table_ids(tabs, "pagila").drop(columns=[]).fillna("").assign(id=lambda d: d["id"].astype(bool)).to_dict(orient="records"),
        "cols": store_schemas.get_column_ids(cols, "pagila").assign(id=lambda d: d["id"].astype(bool)).fillna("").to_dict(orient="records"),
        "ids_by_db": len(store_schemas.get_schemas_ids_and_names(database_name="pagila")),
        "ids_all": len(store_schemas.get_schemas_ids_and_names()),
        "ids_missing": store_schemas.get_schemas_ids_and_names(database_name="ghost"),
        "empty": [store_schemas.get_table_ids(pd.DataFrame(), "pagila").empty, store_schemas.get_column_ids(None, "pagila")],
        "unknown_db": store_schemas.get_table_ids(tabs, "ghost").columns.tolist(),
    }


def add_schemas_edge(schema_id):
    new_table = str(uuid.uuid4())
    schema = _node(Labels.SCHEMA, {"name": "public"}, {"id": schema_id})
    table = _node(Labels.TABLE, {"name": "lane_single", "table_type": "base table"}, {"id": new_table})
    table.props["id"] = new_table
    store_schemas.add_schemas_edge((schema, table), created=STAMP)
    return _readback()


def merge_nodes_and_edges(schema_id, table_id, column_id):
    """The two-pass ingest write: nodes held, then written by their edges; plus a re-point."""
    pending = store_schemas.PendingRows()
    t_new, c_new = str(uuid.uuid4()), str(uuid.uuid4())
    nodes = [
        {"label": Labels.TABLE, "match_props": {"id": t_new}, "props": {"id": t_new, "name": "lane_merge", "table_type": "base table", "pk": ["a"]}},
        {"label": Labels.TABLE, "match_props": {"id": table_id}, "props": {"id": table_id, "name": "film", "table_type": "view", "pk": ["x"]}},
        {"label": Labels.COLUMN, "match_props": {"id": c_new}, "props": {"id": c_new, "name": "k", "data_type": "int4", "ordinal_position": "1", "sample_values": "[1]"}},
        {"label": Labels.DB, "match_props": {"name": "pagila"}, "props": {"name": "pagila"}},  # no id: written at once
        {"label": [Labels.DB], "match_props": {"name": "lane_db2"}, "props": {"name": "lane_db2", "dialect": "x"}},
    ]
    store_schemas.merge_schema_nodes(nodes, STAMP, pending)
    store_schemas.merge_schema_edges(
        [{"uid": t_new, "vid": schema_id}, {"uid": table_id, "vid": schema_id}], Labels.SCHEMA, Labels.TABLE, pending
    )
    store_schemas.merge_schema_edges([{"uid": c_new, "vid": t_new}], Labels.TABLE, Labels.COLUMN, pending)
    store_schemas.merge_schema_edges([{"uid": column_id, "vid": table_id}], Labels.TABLE, Labels.COLUMN, pending)  # unheld: re-point
    store_schemas.merge_schema_edges([], Labels.TABLE, Labels.COLUMN, pending)
    return _readback()


def fks_and_pks():
    fks = pd.DataFrame(
        [
            {"table_schema": "public", "table_name": "film", "column_name": "language_id", "referenced_schema": "public", "referenced_table": "language", "referenced_column": "language_id"},
            {"table_schema": "public", "table_name": "film", "column_name": "language_id", "referenced_schema": "public", "referenced_table": "language", "referenced_column": "language_id"},
            {"table_schema": "public", "table_name": "film", "column_name": "title", "referenced_schema": "public", "referenced_table": "outside", "referenced_column": "x"},
            {"table_schema": "public", "table_name": "film_actor", "column_name": "film_id", "referenced_schema": "public", "referenced_table": "film", "referenced_column": "film_id"},
        ]
    )
    store_schemas.add_fks(fks, STAMP, "pagila")
    store_schemas.add_fks(fks.iloc[:1], LATER, "pagila")  # re-stamp one
    store_schemas.add_fks(None, LATER, "pagila")
    store_schemas.delete_old_fks(LATER, "pagila")  # drops the unrestamped ones, keeps imported (no last_seen)
    pks = pd.DataFrame(
        [
            {"table_schema": "public", "table_name": "film_actor", "column_name": "film_id", "ordinal_position": 2},
            {"table_schema": "public", "table_name": "film_actor", "column_name": "actor_id", "ordinal_position": 1},
            {"table_schema": "public", "table_name": "film", "column_name": "film_id", "ordinal_position": 1},
            {"table_schema": "public", "table_name": "nowhere", "column_name": "z", "ordinal_position": 1},
        ]
    )
    store_schemas.reset_pks("pagila")
    store_schemas.add_pks(pks, "pagila")
    store_schemas.add_pks(pd.DataFrame(), "pagila")
    return _readback()


def reset_pks_only():
    store_schemas.reset_pks("pagila")
    return _readback()


def transaction():
    with write_transaction():
        with write_transaction():
            pass
    return "ok"
