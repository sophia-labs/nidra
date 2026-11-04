# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.catalog.store.db``: database-level writes.

``update_diff_from_existing_schema`` and the ``accumulate_*`` helpers are shared
code (``catalog.diff``) and are not ported.
"""
from __future__ import annotations

from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, prop_iri, row_id, row_iri_single, values, wrap
from auto_ontology.dal_rdf.store_rows import Upserter, commit
from auto_ontology.rdf import writes

TARGET = "auto_ontology.catalog.store.db"
PORTS = [
    "db_exists",
    "update_node_property",
    "delete_schema",
    "delete_table",
    "delete_columns_batch",
    "add_schemas_edge_batch",
    "update_properties_in_graph_batch",
]


def _spec_for(label):
    # Imported late: the install runs while auto_ontology.catalog.store.db is
    # still being imported (it imports the DAL, whose package installs the ports).
    from auto_ontology.catalog.store.db import _spec_for as spec_for

    return spec_for(label)


def db_exists(db_node):
    """``(id, loaded)`` for a database, or ``(None, None)``."""
    rows = client().select(
        "SELECT ?d (COUNT(?sc) AS ?children) WHERE { "
        + wrap(
            f"?d a {iri(class_iri('catalog_database'))} ; {P.catalog_database.name} {lit(db_node.get_name())} . "
            f"OPTIONAL {{ ?sc a {iri(class_iri('catalog_schema'))} ; {P.catalog_schema.ref_database_id} ?d }}"
        )
        + " } GROUP BY ?d ORDER BY ?d"
    )
    if not rows:
        return None, None
    return row_id(rows[0]["d"]), rows[0]["children"] > 0


def update_node_property(label, node_id, update_properties):
    """Set properties on one row."""
    spec = _spec_for(label)
    changes = {k: v for k, v in update_properties.items() if k in spec.columns and k != "id"}
    if not changes:
        return
    commit(updates=[(spec.table.name, row_iri_single(spec.table.name, node_id), changes)])


def _ids(sparql: str, var: str) -> list[str]:
    return [row_id(r[var]) for r in client().select(sparql)]


def _column_ids_of_schema(schema_node_id: str) -> list[str]:
    return _ids(
        "SELECT DISTINCT ?c WHERE { "
        + wrap(
            f"?c a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} ?t . "
            f"?t {P.catalog_table.ref_schema_id} {iri(row_iri_single('catalog_schema', schema_node_id))} ."
        )
        + " }",
        "c",
    )


def _column_ids_of_table(table_id: str) -> list[str]:
    return _ids(
        "SELECT DISTINCT ?c WHERE { "
        + wrap(
            f"?c a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} {iri(row_iri_single('catalog_table', table_id))} ."
        )
        + " }",
        "c",
    )


def _untag(column_ids: list[str]) -> None:
    # Through the module so an installed port of dal.pii is the one called.
    from auto_ontology.dal import pii

    pii.untag_attributes_of_columns_being_deleted(column_ids)


def delete_schema(schema_node_id):
    """Delete a schema; its tables and columns go with it, by cascade."""
    from auto_ontology.dal import session

    with session.write_transaction():
        _untag(_column_ids_of_schema(schema_node_id))
        writes.delete_rows("catalog_schema", [schema_node_id])


def delete_table(table_id):
    """Delete a table; its columns cascade."""
    from auto_ontology.dal import session

    with session.write_transaction():
        _untag(_column_ids_of_table(table_id))
        writes.delete_rows("catalog_table", [table_id])


def delete_columns_batch(column_ids):
    """Delete catalog columns, realigning PII on their attributes first."""
    from auto_ontology.dal import session

    ids = list(column_ids)
    if not ids:
        return
    with session.write_transaction():
        _untag(ids)
        writes.delete_rows("catalog_column", ids)


def add_schemas_edge_batch(edges, created):
    """Upsert parent/child pairs, linking each child to its parent (one request).

    *created* is ignored: the column carries a server default.
    """
    up = Upserter()
    by_label: dict[str, list[dict]] = {}
    for edge in edges:
        by_label.setdefault(edge["from_label"], []).append(edge["from_identProps"])
        by_label.setdefault(edge["to_label"], []).append(edge["to_identProps"])
    try:
        for label, matches in by_label.items():
            up.prefetch(label, matches)
        for edge in edges:
            parent_id = up.upsert(edge["from_label"], edge["from_identProps"], edge["v_props"])
            up.upsert(edge["to_label"], edge["to_identProps"], edge["u_props"], parent_id=parent_id)
        up.flush()
    except Exception as err:
        raise Exception(f'Error in "add_schemas_edge_batch": {err}') from err


def update_properties_in_graph_batch(items):
    """Update rows by id, preserving any description already stored (one request)."""
    planned = []
    for item in items:
        spec = _spec_for(item["label"])
        props = {k: v for k, v in item["props"].items() if k in spec.columns}
        description = props.pop("description", None)
        props.pop("id", None)
        planned.append((spec, item["id"], props, description))

    stored: dict[tuple[str, str], bool] = {}
    wanted = [(spec.table.name, i) for spec, i, _, d in planned if d is not None]
    for table in {t for t, _ in wanted}:
        have = client().select(
            "SELECT DISTINCT ?s WHERE { "
            + wrap(
                f"{values('s', [row_iri_single(table, i) for t, i in wanted if t == table])} "
                f"?s {iri(prop_iri(table, 'description'))} ?d ."
            )
            + " }"
        )
        for r in have:
            stored[(table, row_id(r["s"]))] = True

    updates = []
    for spec, item_id, props, description in planned:
        changes = dict(props)
        # coalesce(stored, new): the stored description wins when there is one.
        if description is not None and not stored.get((spec.table.name, item_id)):
            changes["description"] = description
            stored[(spec.table.name, item_id)] = True  # a later item for the row sees it
        if changes:
            updates.append((spec.table.name, row_iri_single(spec.table.name, item_id), changes))
    commit(updates=updates)
