"""write_diff drivers for the data-lane write ports (datasources, attributes,
pii, connections): the cases whose arguments are not plain JSON.

Each returns what the DAL reads back for the rows it touched, so ``returned``
is compared too (a vacuous SAME is the failure mode of a diff-only oracle).
"""
from __future__ import annotations

from typing import Any


def _columns(table_id: str) -> list[dict[str, Any]]:
    """Every property of every column of the table (name order), read back through the DAL."""
    from auto_ontology.dal.datasources import fetch_columns_for_table, fetch_item_by_id

    page = fetch_columns_for_table(table_id) or {"columns": []}
    props = [fetch_item_by_id(c["id"], "Column") for c in page["columns"]]
    return sorted(props, key=lambda p: p["name"])


def sample_values_create(table_id: str):
    from auto_ontology.dal.datasources import store_column_sample_values

    store_column_sample_values(
        table_id,
        {"film_id": [1, 2, 3], "title": ["Alien", "Heat"], "rating": [None, None], "no_such_column": ["x"]},
    )
    return _columns(table_id)


def sample_values_update(table_id: str):
    from auto_ontology.dal.datasources import store_column_sample_values

    store_column_sample_values(table_id, {"title": ["old", "older"], "film_id": [9]})
    store_column_sample_values(table_id, {"title": ["new"], "rating": ["PG", "R"]})
    return _columns(table_id)


def sample_values_noop(table_id: str):
    from auto_ontology.dal.datasources import store_column_sample_values

    store_column_sample_values(table_id, {"title": ["same"]})
    store_column_sample_values(table_id, {"title": ["same"]})
    return _columns(table_id)


def sample_values_empty(table_id: str):
    from auto_ontology.dal.datasources import store_column_sample_values

    store_column_sample_values(table_id, {})
    store_column_sample_values("no-such-table", {"title": ["x"]})
    return _columns(table_id)


def uniqueness_create(table_id: str):
    from auto_ontology.dal.datasources import store_column_uniqueness

    store_column_uniqueness(table_id, {"film_id": True, "title": False, "rating": 1, "no_such_column": True})
    return _columns(table_id)


def uniqueness_update(table_id: str):
    from auto_ontology.dal.datasources import store_column_uniqueness

    store_column_uniqueness(table_id, {"film_id": True, "title": True})
    store_column_uniqueness(table_id, {"film_id": False})
    store_column_uniqueness(table_id, {})
    return _columns(table_id)


def date_formats_create(table_id: str):
    from auto_ontology.dal.datasources import store_column_date_formats

    store_column_date_formats(table_id, {"last_update": "YYYY-MM-DD", "title": "", "rating": None, "release_year": "YYYY"})
    return _columns(table_id)


def date_formats_update(table_id: str):
    from auto_ontology.dal.datasources import store_column_date_formats

    store_column_date_formats(table_id, {"last_update": "YYMMDD"})
    store_column_date_formats(table_id, {"last_update": "YYYY-MM-DD", "title": None})
    store_column_date_formats(table_id, {})
    return _columns(table_id)


def junction_cases(table_id: str):
    from auto_ontology.dal.datasources import fetch_table_by_id, mark_table_as_junction

    mark_table_as_junction(table_id)
    mark_table_as_junction(table_id)  # already a junction: stays
    mark_table_as_junction("no-such-table")
    return fetch_table_by_id(table_id)


def metadata_batch(database_name: str):
    from auto_ontology.dal.datasources import apply_metadata_batch, fetch_table_by_name

    apply_metadata_batch(
        database_name,
        [
            {"table_name": "film", "description": "Films we rent"},
            {"table_name": "actor", "description": None},
            {"table_name": "no_such_table", "description": "x"},
        ],
        [
            {"table_name": "film", "column_name": "title", "description": "The title", "sample_values": '["Alien"]'},
            {"table_name": "film", "column_name": "rating", "description": None, "sample_values": '["PG"]'},
            {"table_name": "film", "column_name": "film_id", "description": "Key", "sample_values": None},
            {"table_name": "film", "column_name": "no_such", "description": "x", "sample_values": None},
        ],
    )
    return [fetch_table_by_name("film"), fetch_table_by_name("actor"), _columns_of("film")]


def metadata_batch_keeps(database_name: str):
    """A second batch with nothing to say leaves the first batch's values."""
    from auto_ontology.dal.datasources import apply_metadata_batch, fetch_table_by_name

    first = [{"table_name": "film", "column_name": "title", "description": "Curated", "sample_values": '["a"]'}]
    apply_metadata_batch(database_name, [{"table_name": "film", "description": "Curated table"}], first)
    apply_metadata_batch(
        database_name,
        [{"table_name": "film", "description": None}],
        [{"table_name": "film", "column_name": "title", "description": None, "sample_values": None}],
    )
    return [fetch_table_by_name("film"), _columns_of("film")]


def metadata_batch_empty(database_name: str):
    from auto_ontology.dal.datasources import apply_metadata_batch, fetch_table_by_name

    apply_metadata_batch(database_name, [], [])
    apply_metadata_batch("no-such-db", [{"table_name": "film", "description": "x"}], [])
    return fetch_table_by_name("film")


def _columns_of(table_name: str):
    from auto_ontology.dal.datasources import fetch_table_by_name

    table = fetch_table_by_name(table_name)
    return _columns(table["id"]) if table else None


def patch_node_cases(table_id: str, column_id: str, database_id: str, schema_id: str):
    from auto_ontology.dal.datasources import patch_catalog_node

    out = [
        patch_catalog_node(table_id, {"description": "Patched", "description_certified": True, "ignored": 1, "id": "zzz"}),
        patch_catalog_node(table_id, {"description": None}),
        patch_catalog_node(column_id, {"sample_values": ["a", "b"], "format": "YYYY", "name": "renamed", "is_unique": True}),
        patch_catalog_node(column_id, {"sample_values": []}),
        patch_catalog_node(column_id, {}),
        patch_catalog_node(database_id, {"description": "A database", "dialect": "postgresql"}),
        patch_catalog_node(schema_id, {"description": "A schema", "description_certified": True}),
        patch_catalog_node("no-such-node", {"description": "x"}),
    ]
    return out


# -- attributes ---------------------------------------------------------------


def _attr_state(term_id: str):
    from auto_ontology.dal.terms import fetch_column_attributes_by_term_id

    return sorted(fetch_column_attributes_by_term_id(term_id), key=lambda a: str(a.get("name")))


def merge_attr_cases(table_id: str, term_id: str):
    """create, update (datatype assigned, description coalesced), no-op, missing column/term."""
    from auto_ontology.dal.attributes import merge_column_attribute

    def call(**kw):
        base = dict(term_name="Film", table_id=table_id, source_column="rental_duration", attr_name="rental duration",
                    datatype="integer", description="Days")
        return merge_column_attribute(**{**base, **kw})

    created = call()
    again = call()  # same key: same id, nothing new
    same = created == again
    call(datatype="smallint", description=None)  # datatype assigned, description kept
    call(description="Changed")
    existing = merge_column_attribute(term_name="Film", table_id=table_id, source_column="title", attr_name="film title",
                                      datatype="text", description=None)
    missing_column = call(source_column="no_such_column")
    missing_term = call(term_name="No such term")
    return {"same": same, "existing_is_str": isinstance(existing, str), "missing": [missing_column, missing_term],
            "state": _attr_state(term_id)}


def _is_rdf() -> bool:
    import os

    return os.environ.get("CATALOG_READ_BACKEND", "postgres").lower() == "rdf"


def _pin_modified(attr_id: str) -> bool:
    """Replace the (clock-dependent) ``modified`` stamp with a fixed one on either backend.

    Returns whether a stamp was there to replace, so the case still proves ``touch`` wrote it.
    """
    from datetime import datetime, timezone

    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    if _is_rdf():
        from auto_ontology.rdf import writes as W

        W.update_row("column_attribute", attr_id, {"modified": fixed})
        return True
    from sqlalchemy import update

    from auto_ontology.dal import schema as s
    from auto_ontology.dal.session import store

    rows = store().query_write(
        update(s.column_attribute).where(s.column_attribute.c.id == attr_id).values(modified=fixed).returning(s.column_attribute.c.id)
    )
    return bool(rows)


def _drop_attribute(attr_id: str) -> None:
    """Delete an attribute (cascading its links) on either backend: test cleanup."""
    if _is_rdf():
        from auto_ontology.rdf import writes as W

        W.delete_rows("column_attribute", [attr_id])
        return
    from sqlalchemy import delete

    from auto_ontology.dal import schema as s
    from auto_ontology.dal.session import store

    store().query_write(delete(s.column_attribute).where(s.column_attribute.c.id == attr_id))


def update_attr_cases(attr_id: str, term_id: str, other_term_id: str):
    from auto_ontology.dal.attributes import update_column_attribute

    out = [
        update_column_attribute(attr_id, term_id, name="film title (renamed)"),
        update_column_attribute(attr_id, term_id, description="Edited", certified=True, touch=True, modified_by="user-1"),
    ]
    stamped = _pin_modified(attr_id)
    out += [
        update_column_attribute(attr_id, term_id),  # nothing to change
        update_column_attribute(attr_id, term_id, name="film title"),  # renamed back
        update_column_attribute(attr_id, other_term_id, name="wrong term"),  # not its term: None
        update_column_attribute("no-such-attr", term_id, name="x"),
    ]
    touched = update_column_attribute(attr_id, term_id, touch=True)  # modified_by cleared, modified re-stamped
    _pin_modified(attr_id)
    return {"stamped": stamped, "touched_has_context": touched is not None, "calls": out, "state": _attr_state(term_id)}


def update_attr_conflict(attr_id: str, term_id: str, table_id: str):
    """Renaming onto an existing merge key raises IntegrityError on both backends."""
    from auto_ontology.dal.attributes import merge_column_attribute, update_column_attribute

    dup = merge_column_attribute(term_name="Film", table_id=table_id, source_column="title", attr_name="film title two",
                                 datatype="text", description=None)
    try:
        update_column_attribute(dup, term_id, name="film title")
        raised = None
    except Exception as exc:  # noqa: BLE001
        raised = type(exc).__name__
    _drop_attribute(dup)  # two attributes on one column make some reads order-dependent
    return {"raised": raised, "state": _attr_state(term_id)}


def semantic_fk_cases(new_col: str, new_attr: str, old_col: str, old_attr: str, foreign_col: str):
    from auto_ontology.dal.attributes import delete_semantic_fk, merge_semantic_fk

    out = [
        merge_semantic_fk(new_col, new_attr),   # create
        merge_semantic_fk(new_col, new_attr),   # idempotent
        merge_semantic_fk(old_col, old_attr),   # already there
        merge_semantic_fk(foreign_col, old_attr),  # another database: refused
        merge_semantic_fk("no-such-col", old_attr),
    ]
    delete_semantic_fk(old_col, old_attr)
    delete_semantic_fk(old_col, old_attr)  # not there: no-op
    delete_semantic_fk("no-such-col", "no-such-attr")
    return out


def semantic_fk_join_default(table_id: str, attr_id: str, selected: str, sibling: str):
    from auto_ontology.dal.attributes import merge_semantic_fk, set_semantic_fk_join_default

    merge_semantic_fk(sibling, attr_id)  # a second member of the (table, attribute) group
    set_semantic_fk_join_default(table_id, attr_id, sibling)
    set_semantic_fk_join_default(table_id, attr_id, selected)
    try:
        set_semantic_fk_join_default(table_id, attr_id, "no-such-col")
        raised = None
    except ValueError as exc:
        raised = str(exc)
    return raised


# -- pii ----------------------------------------------------------------------


def pii_mark_cases(c1: str, c2: str, c3: str):
    from auto_ontology.dal.pii import mark_columns_pii_processed

    column_ids = [c1, c2, c3]
    first = [c1, c2]
    return [
        mark_columns_pii_processed([]),
        mark_columns_pii_processed(first + first + ["no-such-col"]),
        mark_columns_pii_processed(first),   # already processed: 0
        mark_columns_pii_processed(list(column_ids)),
    ]


def _seed_pg_or_graph(table_name: str, row: dict[str, Any]) -> dict[str, Any]:
    """Seed one row identically on both backends (Postgres insert, or the graph primitive)."""
    import os

    if os.environ.get("CATALOG_READ_BACKEND", "postgres").lower() == "rdf":
        from auto_ontology.rdf import writes as W

        return W.insert_row(table_name, row)
    from sqlalchemy import insert

    from auto_ontology.dal import schema as s
    from auto_ontology.dal.session import store

    return dict(store().query_write(insert(s.METADATA.tables[table_name]).values(**row).returning(s.METADATA.tables[table_name]))[0])


def pii_tag_propagation(column_id: str):
    """A tagged column's tag lands on its attributes once; processed attributes are not candidates again."""
    from auto_ontology.dal.pii import tag_attributes_of_tagged_columns
    from auto_ontology.dal.terms import fetch_column_attributes_by_term_id

    tag = _seed_pg_or_graph("tag", {"name": "PII"})
    _seed_pg_or_graph("tag_target", {"tag_id": tag["id"], "column_id": column_id})
    first = tag_attributes_of_tagged_columns(tag["id"])
    second = tag_attributes_of_tagged_columns(tag["id"])
    nothing = tag_attributes_of_tagged_columns("no-such-tag")
    return {"first": first, "second": second, "nothing": nothing}


def _set_processed(table_name: str, row_id: str, flag: bool) -> None:
    """Set ``pii_processed`` on either backend (test setup: the DAL only ever sets it through the writes under test)."""
    if _is_rdf():
        from auto_ontology.rdf import writes as W

        W.update_row(table_name, row_id, {"pii_processed": flag})
        return
    from sqlalchemy import update

    from auto_ontology.dal import schema as s
    from auto_ontology.dal.session import store

    t = s.METADATA.tables[table_name]
    store().query_write(update(t).where(t.c.id == row_id).values(pii_processed=flag))


def pii_realign_untag(column_id: str, sql_attr_id: str, sql_attr_two: str, attr_id: str):
    """No such tag: the labels are untouched, the processed flags are cleared."""
    from auto_ontology.dal.pii import (
        realign_sql_attribute_pii,
        untag_attributes_of_column,
        untag_attributes_of_columns_being_deleted,
    )

    for sa in (sql_attr_id, sql_attr_two):
        _set_processed("sql_attribute", sa, True)
    _set_processed("column_attribute", attr_id, True)
    out = [
        realign_sql_attribute_pii(sql_attr_id, None),  # no tag: True, flag cleared
        realign_sql_attribute_pii("no-such-attr", None),
        untag_attributes_of_column(column_id, "no-such-tag"),  # the column's attributes lose the flag (read back by the captures)
        untag_attributes_of_columns_being_deleted([column_id, column_id, ""]),  # no PII tag in the catalog: nothing
        untag_attributes_of_columns_being_deleted([]),
    ]
    return out


# -- connections ----------------------------------------------------------------


def connection_cases():
    from auto_ontology.dal.connections import clear_connection, insert_connection, list_connections
    from auto_ontology.dal.datasources import fetch_databases

    out = []
    created = insert_connection(connection='{"host": "db1", "port": 5432, "tags": ["a", "b"], "ssl": true}', database_name="newdb")
    out.append(created)
    out.append(insert_connection(connection='{"host": "db2"}', database_name="newdb"))      # upsert: replaced
    out.append(insert_connection(connection='{"host": "pg"}', database_name="pagila"))      # attach to an ingested database
    out.append(insert_connection(connection="", database_name="chinook"))                   # empty: no connection json
    out.append(insert_connection(connection="   ", database_name="vaultdb"))                # created with none
    clear_connection(database_name="newdb")
    clear_connection(database_name="newdb")                                                 # already clear
    clear_connection(database_name="no-such-db")                                            # no such row
    out.append(sorted(fetch_databases(), key=lambda d: d["name"]))
    return out


def vacuity_check(table_id: str):
    """Self-test of the oracle: writes on Postgres only, so write_diff MUST say DIFFERENT."""
    from auto_ontology.dal.datasources import store_column_sample_values

    if not _is_rdf():
        store_column_sample_values(table_id, {"title": ["only on postgres"]})
    return _columns(table_id)
