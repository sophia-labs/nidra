# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.datasources`` column-side and lookup reads.

Integrator note: this module is named ``datasources_b`` to avoid colliding with
the sibling lane's ``dal_rdf/datasources.py``. ``patch.py`` maps a port module to
the DAL module by name, so merge ``PORTS`` and the functions below into
``dal_rdf/datasources.py``.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import ARRAY

from auto_ontology.dal import schema as s
from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, prop_iri, row_id, row_iri_single, values, wrap
from auto_ontology.rdf.client import RDF_JSON
from auto_ontology.utils.sample_values import stringify_sample_values

logger = logging.getLogger(__name__)

PORTS = [
    "fetch_columns_for_table",
    "count_columns_for_table",
    "fetch_parent_table_id_for_column",
    "find_column_id_by_table_and_name",
    "find_table_key_columns",
    "fetch_item_by_id",
    "fetch_join_neighbors",
]

#: Labels ``fetch_node_properties_by_id`` accepts, and their tables.
_NODE_TABLES = {
    "Database": s.catalog_database,
    "Schema": s.catalog_schema,
    "Table": s.catalog_table,
    "Column": s.catalog_column,
}


def _non_blank(text: Any) -> str | None:
    """``nullif(trim(coalesce(x, '')), '')``."""
    if text is None:
        return None
    trimmed = str(text).strip()
    return trimmed or None


def _attribute_descriptions(column_iris: list[str]) -> dict[str, str]:
    """Fallback description per column IRI: first non-blank attribute description
    (lowest attribute id) via ``column__has_attribute``, else ``column__semantic_fk``."""
    if not column_iris:
        return {}
    found: dict[str, dict[str, str]] = {}
    for link in ("column__has_attribute", "column__semantic_fk"):
        body = (
            f"{values('col', column_iris)} "
            f"?link {getattr(P, link).ref_column_id} ?col ; {getattr(P, link).ref_attribute_id} ?attr . "
            f"?attr {P.column_attribute.description} ?description ."
        )
        rows = client().select(f"SELECT DISTINCT ?col ?attr ?description WHERE {{ {wrap(body)} }}")
        best: dict[str, tuple[str, str]] = {}
        for row in rows:
            if _non_blank(row["description"]) is None:
                continue
            attr_id = row_id(row["attr"])
            if row["col"] not in best or attr_id < best[row["col"]][0]:
                best[row["col"]] = (attr_id, row["description"])
        found[link] = {c: d for c, (_, d) in best.items()}
    return {
        c: found["column__has_attribute"].get(c) or found["column__semantic_fk"].get(c)
        for c in column_iris
        if c in found["column__has_attribute"] or c in found["column__semantic_fk"]
    }


def fetch_columns_for_table(
    table_id: str,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> dict[str, Any] | None:
    """A table with its columns nested, or ``None`` if the table is missing."""
    table_iri = row_iri_single("catalog_table", table_id)
    header_body = (
        f"BIND({iri(table_iri)} AS ?t) ?t a {iri(class_iri('catalog_table'))} ; {P.catalog_table.name} ?table_name ; "
        f"{P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?db . "
        f"?db {P.catalog_database.name} ?database_name . "
        f"OPTIONAL {{ ?t {P.catalog_table.table_type} ?table_type }}"
    )
    header = client().select(
        f"SELECT ?table_name ?table_type ?schema_name ?database_name WHERE {{ {wrap(header_body)} }} LIMIT 1"
    )
    if not header:
        return None

    col_body = (
        f"?c {P.catalog_column.ref_table_id} {iri(table_iri)} ; {P.catalog_column.name} ?column_name . "
        f"OPTIONAL {{ ?c {P.catalog_column.ordinal_position} ?ordinal_position }} "
        f"OPTIONAL {{ ?c {P.catalog_column.data_type} ?data_type }} "
        f"OPTIONAL {{ ?c {P.catalog_column.description} ?own_description }} "
        f"OPTIONAL {{ ?c {P.catalog_column.description_certified} ?description_certified }} "
        f"OPTIONAL {{ ?c {P.catalog_column.sample_values} ?sample_values }}"
    )
    rows = client().select(
        f"SELECT ?c ?column_name ?ordinal_position ?data_type ?own_description "
        f"?description_certified ?sample_values WHERE {{ {wrap(col_body)} }}"
    )
    # ordinal_position is nullable (NULLs last, as in Postgres ASC); id breaks ties.
    rows.sort(key=lambda r: (r.get("ordinal_position") is None, r.get("ordinal_position") or 0, row_id(r["c"])))
    rows = rows[max(skip, 0):]
    if limit is not None:
        rows = rows[:limit]

    fallback = _attribute_descriptions([r["c"] for r in rows])
    from auto_ontology.dal import tags as _tags

    ids = [row_id(r["c"]) for r in rows]
    tags = _tags.fetch_tags_map(_tags.TARGET_COLUMN, ids)

    table = dict(header[0])
    table.setdefault("table_type", None)
    table["columns"] = [
        {
            "id": cid,
            "ordinal_position": r.get("ordinal_position"),
            "column_name": r["column_name"],
            "data_type": r.get("data_type"),
            "description": _non_blank(r.get("own_description")) or fallback.get(r["c"]),
            "description_certified": bool(r.get("description_certified", False)),
            "sample_values": stringify_sample_values(r.get("sample_values")),
            "tags": tags.get(cid, []),
        }
        for cid, r in zip(ids, rows)
    ]
    return table


def count_columns_for_table(table_id: str) -> int:
    body = f"?c {P.catalog_column.ref_table_id} {iri(row_iri_single('catalog_table', table_id))} ."
    rows = client().select(f"SELECT (COUNT(?c) AS ?total) WHERE {{ {wrap(body)} }}")
    return int(rows[0]["total"]) if rows else 0


def fetch_parent_table_id_for_column(column_id: str) -> str | None:
    body = f"{iri(row_iri_single('catalog_column', column_id))} {P.catalog_column.ref_table_id} ?t ."
    rows = client().select(f"SELECT ?t WHERE {{ {wrap(body)} }} LIMIT 1")
    return row_id(rows[0]["t"]) if rows else None


def _lower_literal(text: str) -> str:
    return lit(text.lower())


def find_column_id_by_table_and_name(
    table_name: str,
    column_name: str,
    database_name: str | None = None,
) -> str | None:
    """Resolve a ``table.column`` reference from generated SQL to its Column id."""
    if not table_name or not column_name:
        return None
    body = (
        f"?c {P.catalog_column.ref_table_id} ?t ; {P.catalog_column.name} ?cn . "
        f"?t {P.catalog_table.name} ?tn . "
        f"FILTER(LCASE(?tn) = {_lower_literal(table_name)} && LCASE(?cn) = {_lower_literal(column_name)})"
    )
    if database_name:
        body += (
            f" ?t {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} {lit(database_name)} ."
        )
    max_rows = 1 if database_name else 2
    rows = client().select(f"SELECT DISTINCT ?c WHERE {{ {wrap(body)} }} ORDER BY ?c LIMIT {max_rows}")
    if not database_name and len(rows) > 1:
        logger.info(
            "find_column_id_by_table_and_name: ambiguous match for %s.%s "
            "with no database_name given — treating as unresolved",
            table_name,
            column_name,
        )
        return None
    return row_id(rows[0]["c"]) if rows else None


def find_table_key_columns(table_name: str, database_name: str | None = None) -> dict[str, list[str]]:
    """``{"pk": [...], "unique": [...]}`` column names (lowercased) for a bare table name."""
    empty: dict[str, list[str]] = {"pk": [], "unique": []}
    if not table_name:
        return empty
    body = (
        f"?t {P.catalog_table.name} ?tn . FILTER(LCASE(?tn) = {_lower_literal(table_name)}) "
        f"OPTIONAL {{ ?t {P.catalog_table.pk} ?pk FILTER(datatype(?pk) = <{RDF_JSON}>) }}"
    )
    if database_name:
        body += (
            f" ?t {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} {lit(database_name)} ."
        )
    rows = client().select(f"SELECT ?t ?pk WHERE {{ {wrap(body)} }} ORDER BY ?t LIMIT 1")
    if not rows:
        return empty
    table_iri, pk = rows[0]["t"], rows[0].get("pk")
    pk_cols = [str(c).lower() for c in (pk if isinstance(pk, list) else [])]
    ubody = (
        f"?c {P.catalog_column.ref_table_id} {iri(table_iri)} ; {P.catalog_column.name} ?name ; "
        f"{P.catalog_column.is_unique} true . "
        f"OPTIONAL {{ ?c {P.catalog_column.ordinal_position} ?pos }}"
    )
    unique_rows = client().select(f"SELECT ?c ?name ?pos WHERE {{ {wrap(ubody)} }}")
    unique_rows.sort(key=lambda r: (r.get("pos") is None, r.get("pos") or 0, r["c"]))
    unique_cols = list(dict.fromkeys(r["name"].lower() for r in unique_rows if r["name"]))
    return {"pk": pk_cols, "unique": unique_cols}


def _node_properties_by_id(node_id: str, label: str | list[str]) -> dict | None:
    """All columns of the catalog row, plus ``label`` (``fetch_node_properties_by_id``)."""
    labels = label if isinstance(label, list) else [label]
    for candidate in labels:
        if candidate not in _NODE_TABLES:
            logger.warning("Rejecting unknown label %r in fetch_node_properties_by_id", candidate)
            return None
    for candidate in labels:
        table = _NODE_TABLES[candidate]
        subject = iri(row_iri_single(table.name, node_id))
        names = [c.name for c in table.columns]
        opts = []
        for i, c in enumerate(table.columns):
            filt = f" FILTER(datatype(?v{i}) = <{RDF_JSON}>)" if isinstance(c.type, ARRAY) else ""
            opts.append(f"OPTIONAL {{ {subject} {iri(prop_iri(table.name, c.name))} ?v{i}{filt} }}")
        body = f"{subject} a {iri(class_iri(table.name))} . " + " ".join(opts)
        rows = client().select(
            f"SELECT {' '.join(f'?v{i}' for i in range(len(names)))} WHERE {{ {wrap(body)} }} LIMIT 1"
        )
        if rows:
            props = {n: rows[0].get(f"v{i}") for i, n in enumerate(names)}
            props["label"] = candidate
            return props
    return None


def fetch_item_by_id(item_id: str, label: str | list[str]) -> dict | None:
    """As ``fetch_node_properties_by_id``, but logs when nothing matches."""
    result = _node_properties_by_id(item_id, label)
    if result is None:
        logger.error("Required item with id %r not found.", item_id)
    return result


def fetch_join_neighbors(table_id: str) -> list[dict[str, Any]]:
    """Tables joined to this one, in either direction."""
    me = iri(row_iri_single("catalog_table", table_id))
    out: dict[str, dict[str, Any]] = {}
    for near, far in (("source_table_id", "target_table_id"), ("target_table_id", "source_table_id")):
        body = (
            f"?j {getattr(P.table__join, 'ref_' + near)} {me} ; {getattr(P.table__join, 'ref_' + far)} ?o . "
            f"?o {P.catalog_table.name} ?name . OPTIONAL {{ ?o {P.catalog_table.description} ?description }}"
        )
        for row in client().select(f"SELECT DISTINCT ?o ?name ?description WHERE {{ {wrap(body)} }}"):
            oid = row_id(row["o"])
            out[oid] = {"id": oid, "name": row["name"], "description": row.get("description")}
    return [out[k] for k in sorted(out)]
