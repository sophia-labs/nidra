# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the remaining ``auto_ontology.dal.terms`` reads.

Companion to :mod:`auto_ontology.dal_rdf.terms` (same target module, see
``rdf/patch.py``). Private helpers of that file are reused, not edited.
Collaborators in other DAL modules (zone resolution, ``fetch_term_table_pairs``,
``fetch_all_terms``, ``fetch_col_table_contexts``) are looked up at call time so
whichever backend is installed for them is the one used.
"""
from __future__ import annotations

import logging
from typing import Any

from auto_ontology.catalog.constants import Edges, Labels
from auto_ontology.dal_rdf._common import P, client, iri, lit, values, wrap
from auto_ontology.dal_rdf.terms import (
    _certifications,
    _in_scope_filter,
    _own_flags,
    _scope_table_ids,
    _semantic_terms_body,
)
from auto_ontology.rdf.vocab import row_id, row_iri_single
from auto_ontology.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_TERM,
    REL_HAS_ATTRIBUTE,
    REL_PROPERTY_OF,
    REL_REPRESENTS,
    REL_SEMANTIC_FK,
    SEMANTIC_SOURCE,
)

logger = logging.getLogger(__name__)

PORTS = [
    "count_column_attributes_by_term_id",
    "fetch_all_terms_and_attributes",
    "fetch_column_attribute_counts",
    "fetch_column_attribute_embedding_contexts_by_column_id",
    "fetch_related_term_ids",
    "fetch_related_terms",
    "fetch_related_terms_counts",
    "fetch_table_schema_map",
    "fetch_term_and_column_attributes_for_embedding",
    "fetch_term_zones_map",
    "fetch_terms_by_ids",
    "fetch_terms_with_sqls",
    "find_column_attribute_by_column_id",
    "get_slim_term_by_id",
    "get_term_certification",
    "get_term_record_for_table",
    "semantic_layer_calculated",
    "term_is_in_scope",
    "find_term_link_path",
]

_RDF_JSON = "http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON"


def _opt(subject: str, prop: str, var: str) -> str:
    return f"OPTIONAL {{ {subject} {prop} {var} }} "


def _json_opt(subject: str, prop: str, var: str) -> str:
    return f"OPTIONAL {{ {subject} {prop} {var} . FILTER(datatype({var}) = <{_RDF_JSON}>) }} "


def _iris(kind: str, ids) -> list[str]:
    return [row_iri_single(kind, i) for i in ids]


def _table_id_in(var: str, table_ids: list[str]) -> str:
    """``VALUES`` restriction of a literal ``table_id`` variable (never empty)."""
    return values(var[1:], list(table_ids), as_iri=False)


# ---------------------------------------------------------------------------
# Single-term reads
# ---------------------------------------------------------------------------


def get_term_certification(term_id: str, zone_ids: list[str] | None = None) -> str | None:
    """One Term's aggregate certification, or ``None`` when it is not visible."""
    table_ids = _scope_table_ids(zone_ids)
    body = (
        f"{values('t', [row_iri_single('term', term_id)])} ?t a <urn:ao:term> . "
        f"{_in_scope_filter('?t', table_ids)} "
        f"{_opt('?t', P.term.name_certified, '?name_certified')}"
        f"{_opt('?t', P.term.description_certified, '?description_certified')}"
    )
    rows = client().select(
        f"SELECT ?name_certified ?description_certified WHERE {{ {wrap(body)} }} LIMIT 1"
    )
    if not rows:
        return None
    return _certifications({term_id: _own_flags(rows[0])}, table_ids)[term_id]


def term_is_in_scope(
    term_id: str,
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> bool:
    """Would ``fetch_all_terms`` return this term?"""
    table_ids = _scope_table_ids(zone_ids, data_ids_by_zone)
    rows = client().select(
        f"SELECT ?t WHERE {{ {wrap(_semantic_terms_body(table_ids, None, term_id))} }} LIMIT 1"
    )
    return bool(rows)


def semantic_layer_calculated() -> bool:
    """Has the semantic layer been built at all?"""
    rows = client().select(
        f"SELECT ?t WHERE {{ {wrap(f'?t a <urn:ao:term> ; {P.term.source} {lit(SEMANTIC_SOURCE)}')} }} LIMIT 1"
    )
    return bool(rows)


def get_term_record_for_table(table_id: str) -> dict[str, str] | None:
    """``{id, name, description}`` for the Term a table represents (lowest id)."""
    tb = iri(row_iri_single("catalog_table", table_id))
    rows = client().select(
        "SELECT ?t ?name ?description WHERE { "
        + wrap(
            f"?l {P.table__term.ref_table_id} {tb} ; {P.table__term.ref_term_id} ?t . "
            f"?t {P.term.source} {lit(SEMANTIC_SOURCE)} ; {P.term.name} ?name . "
            f"{_opt('?t', P.term.description, '?description')}"
        )
        + " }"
    )
    if not rows:
        return None
    r = min(rows, key=lambda x: row_id(x["t"]))
    return {"id": row_id(r["t"]), "name": r["name"], "description": r.get("description") or ""}


def get_slim_term_by_id(term_id: str) -> dict[str, str] | None:
    """``{id, name}``, for callers that only need to name a term."""
    rows = client().select(
        "SELECT ?name WHERE { "
        + wrap(
            f"{values('t', [row_iri_single('term', term_id)])} ?t a <urn:ao:term> ; {P.term.name} ?name"
        )
        + " } LIMIT 1"
    )
    return {"id": term_id, "name": rows[0]["name"]} if rows else None


def fetch_terms_by_ids(
    term_ids: list[str], *, skip: int = 0, limit: int | None = None
) -> list[dict[str, Any]]:
    """``{id, name, description}`` for *term_ids*, ordered by name and paged."""
    if not term_ids:
        return []
    unique = list(dict.fromkeys(term_ids))
    sparql = (
        "SELECT ?t ?name ?description WHERE { "
        + wrap(
            f"{values('t', _iris('term', unique))} ?t {P.term.name} ?name . "
            f"{_opt('?t', P.term.description, '?description')}"
        )
        + " } ORDER BY LCASE(?name) ?t"
    )
    if skip:
        sparql += f" OFFSET {int(skip)}"
    if limit is not None:
        sparql += f" LIMIT {int(limit)}"
    return [
        {"id": row_id(r["t"]), "name": r["name"], "description": r.get("description")}
        for r in client().select(sparql)
    ]


# ---------------------------------------------------------------------------
# Suggester and embedding inputs
# ---------------------------------------------------------------------------


def fetch_table_schema_map(database_name: str) -> dict[str, str]:
    """``{table_name_lower: schema_name}`` for one database; last (by id) wins."""
    rows = client().select(
        "SELECT ?tb ?tname ?sname WHERE { "
        + wrap(
            f"?db {P.catalog_database.name} {lit(database_name)} . "
            f"?sch {P.catalog_schema.ref_database_id} ?db ; {P.catalog_schema.name} ?sname . "
            f"?tb {P.catalog_table.ref_schema_id} ?sch ; {P.catalog_table.name} ?tname"
        )
        + " }"
    )
    rows.sort(key=lambda r: row_id(r["tb"]))
    return {
        r["tname"].lower(): r["sname"] for r in rows if r.get("tname") and r.get("sname")
    }


def fetch_terms_with_sqls(
    source: str = SEMANTIC_SOURCE,
    database_name: str | None = None,
) -> list[dict[str, Any]]:
    """Every Term with the **ingestion** SQL of its connected tables."""
    from auto_ontology.dal import schema as s

    columns = [c.name for c in s.sql_query.c if c.name != "id"]
    db = ""
    if database_name is not None:
        db = (
            f"?tb {P.catalog_table.ref_schema_id} ?dbsch . "
            f"?dbsch {P.catalog_schema.ref_database_id} ?dbdb . "
            f"?dbdb {P.catalog_database.name} {lit(database_name)} . "
        )
    body = (
        f"?t a <urn:ao:term> ; {P.term.source} {lit(source)} ; {P.term.name} ?term_name . "
        f"{_opt('?t', P.term.description, '?term_description')}"
        f"?tl {P.table__term.ref_term_id} ?t ; {P.table__term.ref_table_id} ?tb . "
        f"?ql {P.sql_query__table.ref_table_id} ?tb ; {P.sql_query__table.ref_sql_query_id} ?q . "
        f"{db}"
        f"FILTER NOT EXISTS {{ ?o1 {P.sql_attribute__sql.ref_sql_query_id} ?q }} "
        f"FILTER NOT EXISTS {{ ?o2 {P.custom_analysis__sql.ref_sql_query_id} ?q }} "
        + "".join(_opt("?q", iri("urn:ao:sql_query#" + c), f"?c_{c}") for c in columns)
    )
    rows = client().select(
        "SELECT DISTINCT ?t ?term_name ?term_description ?q "
        + " ".join(f"?c_{c}" for c in columns)
        + f" WHERE {{ {wrap(body)} }}"
    )
    rows.sort(key=lambda r: (row_id(r["t"]), row_id(r["q"])))
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        tid = row_id(r["t"])
        entry = grouped.setdefault(
            tid,
            {
                "term_id": tid,
                "term_name": r["term_name"],
                "term_description": r.get("term_description"),
                "sqls": [],
            },
        )
        props = {"id": row_id(r["q"])}
        props.update({c: r.get(f"c_{c}") for c in columns})
        entry["sqls"].append(
            {"sql_text": props.get("sql_full_query"), "sql_id": props["id"], "props": props}
        )
    return list(grouped.values())


def fetch_all_terms_and_attributes(
    zone_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every Term and every ColumnAttribute, for a one-shot bulk re-embed."""
    from auto_ontology.dal import terms as dal_terms

    terms = dal_terms.fetch_all_terms(zone_ids=zone_ids)
    table_ids = _scope_table_ids(zone_ids)
    if table_ids is not None and not table_ids:
        return terms, []
    scope = "" if table_ids is None else values("tb", _iris("catalog_table", table_ids))
    body = (
        f"{scope} "
        f"?a {P.column_attribute.source} {lit(SEMANTIC_SOURCE)} ; {P.column_attribute.name} ?name ; "
        f"   {P.column_attribute.term_name} ?term_name ; {P.column_attribute.source_column} ?source_column . "
        f"?l {P.column__has_attribute.ref_attribute_id} ?a ; {P.column__has_attribute.ref_column_id} ?col . "
        f"?col {P.catalog_column.ref_table_id} ?tb ; {P.catalog_column.name} ?column_name . "
        f"?tb {P.catalog_table.name} ?table_name ; {P.catalog_table.ref_schema_id} ?sch . "
        f"?sch {P.catalog_schema.name} ?schema_name . "
        f"{_opt('?a', P.column_attribute.description, '?description')}"
        f"{_opt('?a', P.column_attribute.datatype, '?datatype')}"
        f"{_opt('?col', P.catalog_column.sample_values, '?sample_values')}"
        f"{_opt('?col', P.catalog_column.is_unique, '?is_unique')}"
    )
    rows = client().select(
        "SELECT ?a ?col ?name ?description ?term_name ?source_column ?column_name ?sample_values "
        "?is_unique ?tb ?table_name ?schema_name ?datatype "
        f"WHERE {{ {wrap(body)} }}"
    )
    attrs = [
        {
            "name": r["name"],
            "description": r.get("description"),
            "term_name": r["term_name"],
            "source_column": r["source_column"],
            "column_name": r["column_name"],
            "sample_values": r.get("sample_values"),
            "is_unique": r.get("is_unique"),
            "table_id": row_id(r["tb"]),
            "table_name": r["table_name"],
            "id": row_id(r["a"]),
            "schema_name": r["schema_name"],
            "datatype": r.get("datatype"),
        }
        for r in rows
    ]
    return terms, attrs


def fetch_term_and_column_attributes_for_embedding(
    term_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """One Term and its ColumnAttributes, for a semantic VDB re-embed."""
    t = iri(row_iri_single("term", term_id))
    found = client().select(
        "SELECT ?name ?description ?synonyms WHERE { "
        + wrap(
            f"{t} a <urn:ao:term> ; {P.term.name} ?name . "
            f"{_opt(t, P.term.description, '?description')}"
            f"{_json_opt(t, P.term.synonyms, '?synonyms')}"
        )
        + " } LIMIT 1"
    )
    if not found:
        return None, []
    # The database: lowest id among those the Term's tables sit in (NULL sorts last).
    dbs = client().select(
        "SELECT ?db ?dbname WHERE { "
        + wrap(
            f"?l {P.table__term.ref_term_id} {t} ; {P.table__term.ref_table_id} ?tb . "
            f"?tb {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} ?dbname"
        )
        + " }"
    )
    database_name = min(dbs, key=lambda r: row_id(r["db"]))["dbname"] if dbs else None
    term = {
        "name": found[0]["name"],
        "description": found[0].get("description"),
        "synonyms": found[0].get("synonyms"),
        "id": term_id,
        "database_name": database_name,
    }
    rows = client().select(
        "SELECT ?a ?col ?name ?description ?term_name ?source_column ?sample_values ?is_unique "
        "?tb ?table_name WHERE { "
        + wrap(
            f"?l {P.column_attribute__term.ref_term_id} {t} ; {P.column_attribute__term.ref_attribute_id} ?a . "
            f"?a {P.column_attribute.name} ?name ; {P.column_attribute.term_name} ?term_name ; "
            f"   {P.column_attribute.source_column} ?source_column . "
            f"{_opt('?a', P.column_attribute.description, '?description')}"
            f"OPTIONAL {{ ?h {P.column__has_attribute.ref_attribute_id} ?a ; "
            f"{P.column__has_attribute.ref_column_id} ?col . "
            f"{_opt('?col', P.catalog_column.sample_values, '?sample_values')}"
            f"{_opt('?col', P.catalog_column.is_unique, '?is_unique')}"
            f"OPTIONAL {{ ?col {P.catalog_column.ref_table_id} ?tb . "
            f"?tb {P.catalog_table.name} ?table_name }} }}"
        )
        + " }"
    )
    rows.sort(key=lambda r: (row_id(r["a"]), row_id(r["col"]) if r.get("col") else ""))
    attrs = [
        {
            "name": r["name"],
            "description": r.get("description"),
            "term_name": r["term_name"],
            "source_column": r["source_column"],
            "sample_values": r.get("sample_values"),
            "is_unique": r.get("is_unique"),
            "table_id": row_id(r["tb"]) if r.get("tb") else None,
            "table_name": r.get("table_name"),
            "id": row_id(r["a"]),
        }
        for r in rows
    ]
    return term, attrs


def fetch_column_attribute_embedding_contexts_by_column_id(
    column_id: str,
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """The Term contexts a column's sample-value update invalidates."""
    col = iri(row_iri_single("catalog_column", column_id))
    rows = client().select(
        "SELECT ?t ?a ?tname ?tdesc ?tsyn ?dbname ?name ?description ?term_name ?source_column "
        "?sample_values WHERE { "
        + wrap(
            f"?h {P.column__has_attribute.ref_column_id} {col} ; {P.column__has_attribute.ref_attribute_id} ?a . "
            f"?l {P.column_attribute__term.ref_attribute_id} ?a ; {P.column_attribute__term.ref_term_id} ?t . "
            f"?t {P.term.name} ?tname . "
            f"?a {P.column_attribute.name} ?name ; {P.column_attribute.term_name} ?term_name ; "
            f"   {P.column_attribute.source_column} ?source_column . "
            f"{_opt('?t', P.term.description, '?tdesc')}"
            f"{_json_opt('?t', P.term.synonyms, '?tsyn')}"
            f"{_opt('?a', P.column_attribute.description, '?description')}"
            f"{_opt(col, P.catalog_column.sample_values, '?sample_values')}"
            f"OPTIONAL {{ {col} {P.catalog_column.ref_table_id} ?tb . "
            f"?tb {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} ?dbname }}"
        )
        + " }"
    )
    rows.sort(key=lambda r: (row_id(r["t"]), row_id(r["a"])))
    contexts: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for r in rows:
        tid = row_id(r["t"])
        if tid not in contexts:
            contexts[tid] = (
                {
                    # upstream reads ``term_name`` from a row where the
                    # attribute's own column shadows the Term's label
                    "name": r["term_name"] or "",
                    "description": r.get("tdesc") or "",
                    "synonyms": r.get("tsyn") or [],
                    "id": tid,
                    "database_name": r.get("dbname") or "",
                },
                [],
            )
        contexts[tid][1].append(
            {
                "name": r["name"],
                "description": r.get("description"),
                "term_name": r["term_name"],
                "source_column": r["source_column"],
                "sample_values": r.get("sample_values"),
                "id": row_id(r["a"]),
            }
        )
    return list(contexts.values())


# ---------------------------------------------------------------------------
# Zones of a Term
# ---------------------------------------------------------------------------


def fetch_term_zones_map(
    zone_ids: list[str] | None = None,
    term_ids: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """``{term_id: [zone, ...]}``, resolving many terms in one pass."""
    if term_ids is not None and not term_ids:
        return {}
    if zone_ids is not None and not zone_ids:
        return {}

    restrict = ""
    if term_ids is not None:
        restrict += values("t", _iris("term", dict.fromkeys(term_ids))) + " "
    if zone_ids is not None:
        restrict += values("z", _iris("zone", dict.fromkeys(zone_ids))) + " "
    enabled = f"FILTER(?enabled = true) " if zone_ids is not None else ""
    zone_tail = (
        f"?tb {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.ref_database_id} ?db . "
        f"{{ ?zt {P.zone_target.ref_table_id} ?tb }} UNION {{ ?zt {P.zone_target.ref_schema_id} ?sch }} "
        f"UNION {{ ?zt {P.zone_target.ref_database_id} ?db }} "
        f"?zt {P.zone_target.ref_zone_id} ?z . "
        f"?z {P.zone.name} ?zname ; {P.zone.enabled} ?enabled . "
        f"{_opt('?z', P.zone.color, '?zcolor')}{enabled}"
    )
    column_path = (
        f"?cl {P.column_attribute__term.ref_term_id} ?t ; {P.column_attribute__term.ref_attribute_id} ?a . "
        f"?ha {P.column__has_attribute.ref_attribute_id} ?a ; {P.column__has_attribute.ref_column_id} ?col . "
        f"?col {P.catalog_column.ref_table_id} ?tb . "
    )
    sql_path = (
        f"?sl {P.sql_attribute__term.ref_term_id} ?t ; {P.sql_attribute__term.ref_attribute_id} ?a . "
        f"?sq {P.sql_attribute__sql.ref_attribute_id} ?a ; {P.sql_attribute__sql.ref_sql_query_id} ?q . "
        f"?qt {P.sql_query__table.ref_sql_query_id} ?q ; {P.sql_query__table.ref_table_id} ?tb . "
    )
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for path in (column_path, sql_path):
        for r in client().select(
            "SELECT DISTINCT ?t ?z ?zname ?zcolor ?enabled WHERE { "
            + wrap(restrict + path + zone_tail)
            + " }"
        ):
            found[(row_id(r["t"]), row_id(r["z"]))] = {
                "id": row_id(r["z"]),
                "name": r["zname"],
                "color": r.get("zcolor"),
                "enabled": r["enabled"],
            }
    result: dict[str, list[dict[str, Any]]] = {}
    for (tid, _zid), zone in sorted(found.items()):
        result.setdefault(tid, []).append(zone)
    for tid, zones in result.items():
        result[tid] = sorted(zones, key=lambda zone: zone["name"])
    return result


# ---------------------------------------------------------------------------
# ColumnAttributes of a Term
# ---------------------------------------------------------------------------


def fetch_column_attribute_counts(
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
    term_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """``[{term_id, count}]`` per Term. Terms with none are omitted."""
    table_ids = _scope_table_ids(zone_ids, data_ids_by_zone)
    if (table_ids is not None and not table_ids) or (term_ids is not None and not term_ids):
        return []
    restrict = ""
    if table_ids is not None:
        restrict += _table_id_in("?table_id", list(dict.fromkeys(table_ids))) + " "
    if term_ids is not None:
        restrict += values("t", _iris("term", dict.fromkeys(term_ids))) + " "
    body = (
        f"{restrict}"
        f"?a {P.column_attribute.source} {lit(SEMANTIC_SOURCE)} . "
        f"{'?a ' + P.column_attribute.table_id + ' ?table_id . ' if table_ids is not None else ''}"
        f"?l {P.column_attribute__term.ref_attribute_id} ?a ; {P.column_attribute__term.ref_term_id} ?t"
    )
    rows = client().select(
        f"SELECT ?t (COUNT(DISTINCT ?a) AS ?count) WHERE {{ {wrap(body)} }} GROUP BY ?t"
    )
    return [{"term_id": row_id(r["t"]), "count": int(r["count"])} for r in rows]


def count_column_attributes_by_term_id(term_id: str, zone_ids: list[str] | None = None) -> int:
    """The unpaged size of ``fetch_column_attributes_by_term_id``."""
    table_ids = _scope_table_ids(zone_ids)
    if table_ids is not None and not table_ids:
        return 0
    scope = ""
    if table_ids is not None:
        scope = (
            f"?a {P.column_attribute.table_id} ?table_id . "
            + _table_id_in("?table_id", list(dict.fromkeys(table_ids)))
        )
    body = (
        f"{iri(row_iri_single('term', term_id))} {P.term.name} ?term_name . "
        f"?a {P.column_attribute.source} {lit(SEMANTIC_SOURCE)} ; {P.column_attribute.term_name} ?term_name . "
        f"{scope}"
    )
    rows = client().select(f"SELECT (COUNT(DISTINCT ?a) AS ?total) WHERE {{ {wrap(body)} }}")
    return int(rows[0]["total"]) if rows and rows[0].get("total") is not None else 0


def find_column_attribute_by_column_id(column_id: str) -> str | None:
    """The semantic ColumnAttribute a column carries (lowest id), if any."""
    rows = client().select(
        "SELECT ?a WHERE { "
        + wrap(
            f"?h {P.column__has_attribute.ref_column_id} {iri(row_iri_single('catalog_column', column_id))} ; "
            f"{P.column__has_attribute.ref_attribute_id} ?a . "
            f"?a {P.column_attribute.source} {lit(SEMANTIC_SOURCE)}"
        )
        + " }"
    )
    return min((row_id(r["a"]) for r in rows), default=None)


# ---------------------------------------------------------------------------
# Related terms
# ---------------------------------------------------------------------------


def _resolve(zone_ids, data_ids_by_zone):
    from auto_ontology.dal import users

    return users.resolve_accessible_catalog_ids(zone_ids, data_ids_by_zone)


def _table_pairs(*args, **kwargs):
    from auto_ontology.dal import terms as dal_terms

    return dal_terms.fetch_term_table_pairs(*args, **kwargs)


def _build_term_table_maps(pairs):
    term_tables: dict[str, set[str]] = {}
    table_terms: dict[str, set[str]] = {}
    for row in pairs:
        term_id, table_id = row.get("term_id"), row.get("table_id")
        if term_id and table_id:
            term_tables.setdefault(term_id, set()).add(table_id)
            table_terms.setdefault(table_id, set()).add(term_id)
    return term_tables, table_terms


def fetch_related_term_ids(
    term_id: str,
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> list[str]:
    """Ids of every Term sharing a table with *term_id*."""
    resolved = _resolve(zone_ids, data_ids_by_zone)
    own_pairs = _table_pairs(zone_ids, resolved, term_ids=[term_id])
    shared = sorted({r["table_id"] for r in own_pairs if r.get("table_id")})
    if not shared:
        return []
    _, table_terms = _build_term_table_maps(_table_pairs(zone_ids, resolved, table_ids=shared))
    related: set[str] = set()
    for table_id in shared:
        related.update(table_terms.get(table_id, set()))
    related.discard(term_id)
    return sorted(related)


def fetch_related_terms(
    term_id: str,
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Terms related to *term_id* by sharing a table, paged by name."""
    from auto_ontology.dal import terms as dal_terms

    related_ids = dal_terms.fetch_related_term_ids(term_id, zone_ids, data_ids_by_zone)
    return dal_terms.fetch_terms_by_ids(related_ids, skip=skip, limit=limit)


def fetch_related_terms_counts(
    zone_ids: list[str] | None = None,
    term_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """``[{term_id, count}]`` — how many terms each one shares a table with."""
    resolved = _resolve(zone_ids, data_ids_by_zone)
    if term_ids is None:
        term_tables, table_terms = _build_term_table_maps(_table_pairs(zone_ids, resolved))
        wanted = list(term_tables)
    else:
        if not term_ids:
            return []
        own_pairs = _table_pairs(zone_ids, resolved, term_ids=term_ids)
        term_tables, _ = _build_term_table_maps(own_pairs)
        touched = sorted({r["table_id"] for r in own_pairs})
        _, table_terms = _build_term_table_maps(
            _table_pairs(zone_ids, resolved, table_ids=touched) if touched else []
        )
        wanted = term_ids
    result: list[dict[str, Any]] = []
    for term_id in wanted:
        related: set[str] = set()
        for table_id in term_tables.get(term_id, set()):
            related.update(table_terms.get(table_id, set()))
        related.discard(term_id)
        result.append({"term_id": term_id, "count": len(related)})
    return result


# ---------------------------------------------------------------------------
# Term-to-term link paths
# ---------------------------------------------------------------------------

_MAX_LINK_PATH_DEPTH = 10

_LINK_PATH_LABELS = {
    "term": LABEL_TERM,
    "table": Labels.TABLE,
    "column": Labels.COLUMN,
    "column_attribute": LABEL_COLUMN_ATTRIBUTE,
}

_KIND_TABLE = {
    "term": "term",
    "table": "catalog_table",
    "column": "catalog_column",
    "column_attribute": "column_attribute",
}


def _edge_legs() -> str:
    """Every undirected edge as ``?src ?dst ?dkind ?rel`` UNION branches."""

    def leg(pattern: str, dkind: str, rel: str) -> str:
        return f"{{ {pattern} BIND({lit(dkind)} AS ?dkind) BIND({lit(rel)} AS ?rel) }}"

    legs = []
    tt = (P.table__term.ref_table_id, P.table__term.ref_term_id)
    legs.append(leg(f"?l {tt[1]} ?src ; {tt[0]} ?dst .", "table", REL_REPRESENTS))
    legs.append(leg(f"?l {tt[0]} ?src ; {tt[1]} ?dst .", "term", REL_REPRESENTS))
    legs.append(leg(f"?dst {P.catalog_column.ref_table_id} ?src .", "column", Edges.CONTAINS))
    legs.append(leg(f"?src {P.catalog_column.ref_table_id} ?dst .", "table", Edges.CONTAINS))
    for link, rel in (("column__has_attribute", REL_HAS_ATTRIBUTE), ("column__semantic_fk", REL_SEMANTIC_FK)):
        c, a = iri(f"urn:ao:{link}#ref-column_id"), iri(f"urn:ao:{link}#ref-attribute_id")
        legs.append(leg(f"?l {c} ?src ; {a} ?dst .", "column_attribute", rel))
        legs.append(leg(f"?l {a} ?src ; {c} ?dst .", "column", rel))
    a, t = P.column_attribute__term.ref_attribute_id, P.column_attribute__term.ref_term_id
    legs.append(leg(f"?l {a} ?src ; {t} ?dst .", "term", REL_PROPERTY_OF))
    legs.append(leg(f"?l {t} ?src ; {a} ?dst .", "column_attribute", REL_PROPERTY_OF))
    return " UNION ".join(legs)


def _link_path_bfs(anchor_id: str, dest_id: str) -> list[dict[str, Any]] | None:
    """Shortest term-to-term path as ``{id, kind, relationship}`` nodes, or ``None``."""
    legs = _edge_legs()
    visited: dict[str, tuple[str, str | None, str | None]] = {anchor_id: ("term", None, None)}
    frontier: list[tuple[str, str]] = [(anchor_id, "term")]

    for _ in range(_MAX_LINK_PATH_DEPTH):
        if not frontier:
            return None
        src_iris = [row_iri_single(_KIND_TABLE[kind], nid) for nid, kind in frontier]
        rows = client().select(
            "SELECT DISTINCT ?src ?dst ?dkind ?rel WHERE { "
            + wrap(f"{values('src', src_iris)} {legs}")
            + " }"
        )
        edges = sorted(
            (row_id(r["src"]), r["dkind"], row_id(r["dst"]), r["rel"]) for r in rows
        )
        next_frontier: list[tuple[str, str]] = []
        for src_id, dkind, node, rel in edges:
            if node in visited:
                continue
            visited[node] = (dkind, src_id, rel)
            next_frontier.append((node, dkind))
            if node == dest_id:
                path: list[dict[str, Any]] = []
                current: str | None = node
                while current is not None:
                    kind, parent, relationship = visited[current]
                    path.append({"id": current, "kind": kind, "relationship": relationship})
                    current = parent
                path.reverse()
                return path
        frontier = next_frontier

    logger.warning(
        "find_term_link_path: gave up after %s levels for %s -> %s",
        _MAX_LINK_PATH_DEPTH, anchor_id, dest_id,
    )
    return None


_NAME_PROP = {
    "term": lambda: P.term.name,
    "table": lambda: P.catalog_table.name,
    "column": lambda: P.catalog_column.name,
    "column_attribute": lambda: P.column_attribute.name,
}


def _name_link_path_nodes(path: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for kind, prop in _NAME_PROP.items():
        ids = [node["id"] for node in path if node["kind"] == kind]
        if not ids:
            continue
        for r in client().select(
            "SELECT ?n ?name WHERE { "
            + wrap(f"{values('n', _iris(_KIND_TABLE[kind], ids))} ?n {prop()} ?name")
            + " }"
        ):
            names[row_id(r["n"])] = r["name"]
    return names


def _enrich_catalog_path_nodes(path_nodes: list[dict[str, Any]]) -> None:
    """Attach catalog ids/names onto each Table/Column node, in place."""
    table_ids = list({n["id"] for n in path_nodes if n.get("label") == Labels.TABLE and n.get("id")})
    if table_ids:
        try:
            rows = client().select(
                "SELECT ?tb ?db ?dbname ?sch ?sname WHERE { "
                + wrap(
                    f"{values('tb', _iris('catalog_table', table_ids))} "
                    f"?tb {P.catalog_table.ref_schema_id} ?sch . "
                    f"?sch {P.catalog_schema.name} ?sname ; {P.catalog_schema.ref_database_id} ?db . "
                    f"?db {P.catalog_database.name} ?dbname"
                )
                + " }"
            )
        except Exception:
            logger.warning(
                "find_term_link_path: table catalog lookup failed for %s", table_ids, exc_info=True
            )
            rows = []
        by_table_id = {row_id(r["tb"]): r for r in rows}
        for node in path_nodes:
            if node.get("label") != Labels.TABLE:
                continue
            info = by_table_id.get(node["id"])
            if info is not None:
                node["database_id"] = row_id(info["db"])
                node["database_name"] = info["dbname"]
                node["schema_id"] = row_id(info["sch"])
                node["schema_name"] = info["sname"]

    column_ids = list({n["id"] for n in path_nodes if n.get("label") == Labels.COLUMN and n.get("id")})
    if column_ids:
        from auto_ontology.dal import datasources

        contexts = datasources.fetch_col_table_contexts(column_ids)
        for node in path_nodes:
            if node.get("label") != Labels.COLUMN:
                continue
            info = contexts.get(node["id"])
            if info is not None:
                node.update(
                    {
                        key: info.get(key)
                        for key in (
                            "table_id", "table_name", "database_id",
                            "database_name", "schema_id", "schema_name",
                        )
                    }
                )


def find_term_link_path(term_a_id: str, term_b_id: str) -> list[dict[str, Any]]:
    """The shortest semantic path connecting two Terms, as ordered hop dicts."""
    if not term_a_id or not term_b_id or term_a_id == term_b_id:
        return []
    try:
        path = _link_path_bfs(term_a_id, term_b_id)
    except Exception:
        logger.warning(
            "find_term_link_path: query failed for %s -> %s", term_a_id, term_b_id, exc_info=True
        )
        return []
    if not path or len(path) < 2:
        return []
    names = _name_link_path_nodes(path)
    nodes = [
        {"id": n["id"], "name": names.get(n["id"], ""), "label": _LINK_PATH_LABELS[n["kind"]]}
        for n in path
    ]
    _enrich_catalog_path_nodes(nodes)
    return [
        {"relationship": path[i + 1]["relationship"], "source": nodes[i], "target": nodes[i + 1]}
        for i in range(len(nodes) - 1)
    ]
