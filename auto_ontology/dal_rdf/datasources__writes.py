# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the ``auto_ontology.dal.datasources`` writes (catalog metadata).

Each function is one update request however many rows it touches; the subjects
the SQL ``WHERE`` clauses select are resolved by a read first.
"""
from __future__ import annotations

from typing import Any

from auto_ontology.dal import schema as s
from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, row_iri_single, wrap
from auto_ontology.dal_rdf._wdata import Cells, apply_cells, existing
from auto_ontology.dal_rdf.datasources__b import fetch_item_by_id as _fetch_item_by_id
from auto_ontology.utils.sample_values import dump_sample_values

PORTS = [
    "store_column_sample_values",
    "store_column_uniqueness",
    "store_column_date_formats",
    "mark_table_as_junction",
    "apply_metadata_batch",
    "patch_catalog_node",
]

#: Labels ``patch_catalog_node`` tries, in order, and their tables.
_NODE_TABLES = {
    "Database": s.catalog_database,
    "Schema": s.catalog_schema,
    "Table": s.catalog_table,
    "Column": s.catalog_column,
}


def _columns_of_table(table_id: str, names: list[str]) -> dict[str, str]:
    """``{column name: column IRI}`` for the named columns of one table."""
    terms = " ".join(lit(n) for n in names)
    rows = client().select(
        "SELECT ?c ?name WHERE { "
        + wrap(
            f"?c a {iri(class_iri('catalog_column'))} ; {P.catalog_column.ref_table_id} "
            f"{iri(row_iri_single('catalog_table', table_id))} ; {P.catalog_column.name} ?name . "
            f"VALUES ?name {{ {terms} }}"
        )
        + " }"
    )
    return {r["name"]: r["c"] for r in rows}


def _set_column_property(table_id: str, values: dict[str, Any], column: str) -> None:
    """One property across several of a table's columns, in one request."""
    if not values:
        return
    found = _columns_of_table(table_id, list(values))
    cells: Cells = {"catalog_column": {found[n]: {column: v} for n, v in values.items() if n in found}}
    apply_cells(cells)


def store_column_sample_values(table_id: str, samples: dict[str, list]) -> None:
    """Write sample values, JSON-encoded, onto a table's columns."""
    if not samples:
        return
    encoded = {
        name: dumped
        for name, values in samples.items()
        if (dumped := dump_sample_values(values)) is not None
    }
    _set_column_property(table_id, encoded, "sample_values")


def store_column_uniqueness(table_id: str, uniqueness: dict[str, bool]) -> None:
    """Write ``is_unique`` flags onto a table's columns."""
    if not uniqueness:
        return
    _set_column_property(table_id, {name: bool(flag) for name, flag in uniqueness.items()}, "is_unique")


def store_column_date_formats(table_id: str, date_formats: dict[str, str]) -> None:
    """Write inferred value notations onto Column ``format``."""
    _set_column_property(table_id, {name: str(fmt) for name, fmt in date_formats.items() if fmt}, "format")


def mark_table_as_junction(table_id: str) -> None:
    """Promote a catalog table to a junction table without ever clearing it."""
    subject = row_iri_single("catalog_table", table_id)
    if subject in existing("catalog_table", [subject]):
        apply_cells({"catalog_table": {subject: {"is_junction_table": True}}})


def apply_metadata_batch(database_name: str, table_rows: list[dict], column_rows: list[dict]) -> None:
    """Write descriptions and sample values, without overwriting existing ones.

    ``coalesce(new, existing)``: a ``None`` in the batch leaves the stored value.
    The tables are those named so in any schema of ``database_name``; the columns
    those named so of a table of that name in the database.
    """
    cells: Cells = {}
    db_path = (
        f"?db a {iri(class_iri('catalog_database'))} ; {P.catalog_database.name} {lit(database_name)} . "
        f"?sch {P.catalog_schema.ref_database_id} ?db . ?t {P.catalog_table.ref_schema_id} ?sch ; "
        f"{P.catalog_table.name} ?tname . "
    )
    if table_rows:
        wanted = {row["table_name"]: row.get("description") for row in table_rows}
        wanted = {n: d for n, d in wanted.items() if d is not None}
        if wanted:
            terms = " ".join(lit(n) for n in wanted)
            found = client().select(
                f"SELECT ?t ?tname WHERE {{ {wrap(db_path + f'VALUES ?tname {{ {terms} }}')} }}"
            )
            cells["catalog_table"] = {r["t"]: {"description": wanted[r["tname"]]} for r in found}
    if column_rows:
        wanted_c: dict[tuple[str, str], dict[str, Any]] = {}
        for row in column_rows:
            changes = {
                k: v
                for k, v in (("description", row.get("description")), ("sample_values", row.get("sample_values")))
                if v is not None
            }
            wanted_c[(row["table_name"], row["column_name"])] = changes
        wanted_c = {k: v for k, v in wanted_c.items() if v}
        if wanted_c:
            tnames = " ".join(lit(n) for n in {k[0] for k in wanted_c})
            cnames = " ".join(lit(n) for n in {k[1] for k in wanted_c})
            found = client().select(
                "SELECT ?c ?tname ?cname WHERE { "
                + wrap(
                    db_path
                    + f"?c {P.catalog_column.ref_table_id} ?t ; {P.catalog_column.name} ?cname . "
                    + f"VALUES ?tname {{ {tnames} }} VALUES ?cname {{ {cnames} }}"
                )
                + " }"
            )
            cells["catalog_column"] = {
                r["c"]: wanted_c[(r["tname"], r["cname"])] for r in found if (r["tname"], r["cname"]) in wanted_c
            }
    apply_cells(cells)


def patch_catalog_node(node_id: str, properties: dict[str, Any]) -> dict[str, Any] | None:
    """Write properties onto whichever catalog row carries *node_id*."""
    subjects = {label: row_iri_single(t.name, node_id) for label, t in _NODE_TABLES.items()}
    classes = {class_iri(t.name): label for label, t in _NODE_TABLES.items()}
    terms = " ".join(iri(x) for x in subjects.values())
    rows = client().select(
        f"SELECT ?s ?c WHERE {{ {wrap(f'VALUES ?s {{ {terms} }} ?s a ?c .')} }}"
    )
    present = {classes[r["c"]] for r in rows if r["c"] in classes}
    for label, table in _NODE_TABLES.items():
        if label not in present:
            continue
        columns = {c.name for c in table.columns}
        values = {k: v for k, v in properties.items() if k in columns and k != "id"}
        if isinstance(values.get("sample_values"), list):
            values["sample_values"] = dump_sample_values(values["sample_values"])
        if values:
            apply_cells({table.name: {subjects[label]: values}})
        props = _fetch_item_by_id(node_id, label)
        props.pop("label", None)
        return {"id": node_id, "label": label, "props": props}
    return None
