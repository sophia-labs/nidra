# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.exploration`` detail reads."""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf._common import P, client, iri, wrap
from auto_ontology.rdf.vocab import row_id, row_iri_single
from auto_ontology.semantic.constants import (
    REL_HAS_ATTRIBUTE,
    REL_SEMANTIC_FK,
    SEMANTIC_SOURCE,
)

PORTS = [
    "fetch_column_exploration_details",
    "fetch_column_attribute_exploration_details",
    "fetch_sql_attribute_exploration_details",
]

# Same cap as the upstream constant (private there, so not imported).
_MAX_REFERENCING_COLUMNS = 50

_COLUMN_REF_VARS = (
    "id", "name", "description", "data_type",
    "table_id", "table_name", "database_id", "database_name", "schema_id", "schema_name",
)


def _resolve(zone_ids):
    # Looked up at call time on the DAL module so whichever backend is
    # installed for users (Postgres today, a port later) answers.
    from auto_ontology.dal import users

    return users.resolve_accessible_catalog_ids(zone_ids)


def _table_iris(table_filter: list[str]) -> str:
    return ", ".join(iri(row_iri_single("catalog_table", t)) for t in table_filter)


def _visible(var: str, table_filter: list[str] | None) -> str:
    """``var`` (a catalog_table row) is inside the accessible set."""
    if table_filter is None:
        return ""
    if not table_filter:
        return "FILTER(false)"
    return f"FILTER({var} IN ({_table_iris(table_filter)}))"


def _sql_in_scope(var: str, table_filter: list[str] | None) -> str:
    """Every table the statement ``var`` references is visible (all-or-nothing)."""
    if table_filter is None:
        return ""
    outside = (
        f"?_l {P.sql_query__table.ref_sql_query_id} {var} ; {P.sql_query__table.ref_table_id} ?_t ."
    )
    if table_filter:
        outside += f" FILTER(?_t NOT IN ({_table_iris(table_filter)}))"
    return f"FILTER NOT EXISTS {{ {outside} }}"


def _column_ref_pattern(col: str) -> str:
    """Binds the catalog path of column ``col`` to the ``_COLUMN_REF_VARS`` variables."""
    return (
        f"{col} {P.catalog_column.name} ?name ; {P.catalog_column.ref_table_id} ?table . "
        f"OPTIONAL {{ {col} {P.catalog_column.description} ?description }} "
        f"OPTIONAL {{ {col} {P.catalog_column.data_type} ?data_type }} "
        f"?table {P.catalog_table.name} ?table_name ; {P.catalog_table.ref_schema_id} ?schema . "
        f"?schema {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?database . "
        f"?database {P.catalog_database.name} ?database_name ."
    )


def _column_ref_row(row: dict[str, Any], col_key: str = "col") -> dict[str, Any]:
    return {
        "id": row_id(row[col_key]),
        "name": row.get("name"),
        "description": row.get("description"),
        "data_type": row.get("data_type"),
        "table_id": row_id(row["table"]),
        "table_name": row.get("table_name"),
        "database_id": row_id(row["database"]),
        "database_name": row.get("database_name"),
        "schema_id": row_id(row["schema"]),
        "schema_name": row.get("schema_name"),
    }


def _select(body: str, head: str, tail: str = "") -> list[dict[str, Any]]:
    return client().select(f"SELECT {head} WHERE {{ {wrap(body)} }} {tail}")


def _column_owned(column_id: str, table_filter: list[str] | None) -> bool:
    """The column exists and, when scoped, its table is accessible."""
    rows = _select(
        f"{iri(row_iri_single('catalog_column', column_id))} {P.catalog_column.ref_table_id} ?table .",
        "?table",
    )
    if not rows:
        return False
    if table_filter is None:
        return True
    return row_id(rows[0]["table"]) in set(table_filter)


def _term_row(term_iri: str) -> dict[str, Any]:
    rows = _select(
        f"{iri(term_iri)} {P.term.name} ?name . OPTIONAL {{ {iri(term_iri)} {P.term.description} ?description }}",
        "?name ?description",
    )
    r = rows[0] if rows else {}
    return {"id": row_id(term_iri), "name": r.get("name"), "description": r.get("description")}


def fetch_column_exploration_details(
    column_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Everything one hop off a visible Column (see the upstream docstring)."""
    resolved = _resolve(zone_ids)
    table_filter = None if resolved is None else list(resolved["table_ids"])
    col_iri = iri(row_iri_single("catalog_column", column_id))

    if not _column_owned(column_id, table_filter):
        return {
            "column_attribute": None,
            "foreign_key_column": None,
            "referencing_columns": [],
            "sql_queries": [],
        }

    # -- the attribute this column carries (HAS_ATTRIBUTE or SEMANTIC_FK)
    attr_rows: list[dict[str, Any]] = []
    for rel, link in (
        (REL_HAS_ATTRIBUTE, "column__has_attribute"),
        (REL_SEMANTIC_FK, "column__semantic_fk"),
    ):
        found = _select(
            f"?l {getattr(P, link).ref_column_id} {col_iri} ; {getattr(P, link).ref_attribute_id} ?attr . "
            f"?attr {P.column_attribute.source} {_lit(SEMANTIC_SOURCE)} ; {P.column_attribute.name} ?name . "
            f"OPTIONAL {{ ?attr {P.column_attribute.description} ?description }}",
            "?attr ?name ?description",
        )
        for r in found:
            attr_rows.append(
                {
                    "id": row_id(r["attr"]),
                    "name": r["name"],
                    "description": r.get("description"),
                    "relationship_type": rel,
                }
            )
    attr_rows.sort(key=lambda r: (r["id"], r["relationship_type"]))

    # -- the column its own foreign key points at
    fk_rows = _select(
        f"?fk {P.column__foreign_key.ref_source_column_id} {col_iri} ; "
        f"{P.column__foreign_key.ref_target_column_id} ?col . {_column_ref_pattern('?col')} "
        f"{_visible('?table', table_filter)}",
        "DISTINCT ?col ?name ?description ?data_type ?table ?table_name ?schema ?schema_name ?database ?database_name",
        "ORDER BY ?col LIMIT 1",
    )

    # -- columns whose foreign key points at this one
    ref_rows = _select(
        f"?fk {P.column__foreign_key.ref_target_column_id} {col_iri} ; "
        f"{P.column__foreign_key.ref_source_column_id} ?col . {_column_ref_pattern('?col')} "
        f"{_visible('?table', table_filter)}",
        "DISTINCT ?col ?name ?description ?data_type ?table ?table_name ?schema ?schema_name ?database ?database_name",
        f"ORDER BY ?table_name ?name ?col LIMIT {_MAX_REFERENCING_COLUMNS}",
    )

    # -- statements that referenced the column directly
    sql_rows = _select(
        f"?l {P.sql_query__column.ref_column_id} {col_iri} ; {P.sql_query__column.ref_sql_query_id} ?q . "
        f"OPTIONAL {{ ?q {P.sql_query.sql_full_query} ?sql }} {_sql_in_scope('?q', table_filter)}",
        "DISTINCT ?q ?sql",
        "ORDER BY ?q",
    )

    return {
        "column_attribute": attr_rows[0] if attr_rows else None,
        "foreign_key_column": _column_ref_row(fk_rows[0]) if fk_rows else None,
        "referencing_columns": [_column_ref_row(r) for r in ref_rows],
        "sql_queries": [
            {"id": row_id(r["q"]), "sql": r["sql"]} for r in sql_rows if r.get("sql")
        ],
    }


def _lit(value: str) -> str:
    from auto_ontology.dal_rdf._common import lit

    return lit(value)


def fetch_column_attribute_exploration_details(
    attr_id: str,
    zone_ids: list[str] | None = None,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """The Term owning a ColumnAttribute, and one page of every Column carrying it."""
    from auto_ontology.dal import terms

    resolved = _resolve(zone_ids)
    attr_iri = iri(row_iri_single("column_attribute", attr_id))

    term_rows = _select(
        f"?l {P.column_attribute__term.ref_attribute_id} {attr_iri} ; {P.column_attribute__term.ref_term_id} ?term .",
        "?term",
        "ORDER BY ?term LIMIT 1",
    )
    term = None
    if term_rows:
        term_id = row_id(term_rows[0]["term"])
        if terms.term_is_in_scope(term_id, zone_ids, resolved):
            term = _term_row(term_rows[0]["term"])

    table_filter = None if resolved is None else list(resolved["table_ids"])
    # The linked columns: both link tables, carrying the relationship type.
    link = " UNION ".join(
        f"{{ ?l_{i} {getattr(P, t).ref_attribute_id} {attr_iri} ; {getattr(P, t).ref_column_id} ?col . "
        f"BIND({_lit(rel)} AS ?rel) }}"
        for i, (t, rel) in enumerate(
            (("column__has_attribute", REL_HAS_ATTRIBUTE), ("column__semantic_fk", REL_SEMANTIC_FK))
        )
    )
    body = f"{link} {_column_ref_pattern('?col')} {_visible('?table', table_filter)}"

    total_rows = _select(body, "(COUNT(DISTINCT ?col) AS ?total)")
    total = int(total_rows[0]["total"]) if total_rows else 0
    if not total:
        return {"term": term, "columns": [], "columns_total": 0}

    group = "?col ?name ?description ?data_type ?table ?table_name ?schema ?schema_name ?database ?database_name"
    tail = f"GROUP BY {group} ORDER BY ?table_name ?name ?col OFFSET {int(skip)}"
    if limit is not None:
        tail += f" LIMIT {int(limit)}"
    page = _select(
        body,
        f"{group} (GROUP_CONCAT(DISTINCT ?rel; separator=\"|\") AS ?rels)",
        tail,
    )
    columns = []
    for r in page:
        row = _column_ref_row(r)
        row["relationship_types"] = sorted({v for v in (r.get("rels") or "").split("|") if v})
        columns.append(row)
    return {"term": term, "columns": columns, "columns_total": total}


def fetch_sql_attribute_exploration_details(
    attr_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The statement and owning Term behind a SqlAttribute."""
    resolved = _resolve(zone_ids)
    table_filter = None if resolved is None else list(resolved["table_ids"])
    attr_iri = iri(row_iri_single("sql_attribute", attr_id))

    sql_rows = _select(
        f"?l {P.sql_attribute__sql.ref_attribute_id} {attr_iri} ; {P.sql_attribute__sql.ref_sql_query_id} ?q . "
        f"?q {P.sql_query.sql_full_query} ?sql . {_sql_in_scope('?q', table_filter)}",
        "?q ?sql",
        "ORDER BY ?q LIMIT 1",
    )
    term_rows = _select(
        f"?l {P.sql_attribute__term.ref_attribute_id} {attr_iri} ; {P.sql_attribute__term.ref_term_id} ?term .",
        "?term",
        "ORDER BY ?term LIMIT 1",
    )
    return {
        "sql": {"id": row_id(sql_rows[0]["q"]), "sql": sql_rows[0]["sql"]} if sql_rows else None,
        "term": _term_row(term_rows[0]["term"]) if term_rows else None,
    }
