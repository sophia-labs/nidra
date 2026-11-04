# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.terms`` reads."""
from __future__ import annotations

import logging

from typing import Any

from auto_ontology.dal_rdf._common import P, client, iri, lit, values, wrap
from auto_ontology.rdf.vocab import row_id, row_iri_single
from auto_ontology.semantic.constants import (
    REL_HAS_ATTRIBUTE,
    REL_REPRESENTS,
    REL_SEMANTIC_FK,
    SEMANTIC_SOURCE,
)
from auto_ontology.utils.sample_values import stringify_sample_values

logger = logging.getLogger(__name__)

PORTS = [
    "count_terms",
    "fetch_all_terms",
    "fetch_column_attributes_by_term_id",
    "fetch_term_synonyms",
    "fetch_term_table_pairs",
    "fetch_terms_and_attributes_for_table",
    "get_full_term_by_id",
]

_RDF_JSON = "http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON"


def fetch_term_synonyms(attr_ids: list[str]) -> dict[str, list[str]]:
    """``{term_name: [synonym, ...]}`` for the terms behind these attributes.

    Keyed by *name*, not id, because the caller matches synonyms against text.
    Terms with no synonyms are omitted rather than mapped to an empty list.
    """
    if not attr_ids:
        return {}
    attr_iris = [row_iri_single("column_attribute", a) for a in attr_ids]
    body = (
        f"{values('attr', attr_iris)} "
        f"?link {P.column_attribute__term.ref_attribute_id} ?attr ; "
        f"      {P.column_attribute__term.ref_term_id} ?term . "
        f"?term {P.term.name} ?name ; {P.term.synonyms} ?synonyms . "
        f"FILTER(datatype(?synonyms) = <http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON>)"
    )
    try:
        rows = client().select(f"SELECT DISTINCT ?name ?synonyms WHERE {{ {wrap(body)} }}")
    except Exception:
        logger.warning("fetch_term_synonyms: query failed", exc_info=True)
        return {}
    result: dict[str, list[str]] = {}
    for row in rows:
        synonyms = [s for s in (row.get("synonyms") or []) if s]
        if row.get("name") and synonyms:
            result[row["name"]] = synonyms
    return result


# ---------------------------------------------------------------------------
# Shared pieces
#
# Cross-module collaborators (zone resolution, tags, zone maps, attribute
# columns) are looked up on their DAL module at call time, so whichever backend
# is installed for them (Postgres, or another lane's port) is the one used.
# ---------------------------------------------------------------------------


def _scope_table_ids(zone_ids, data_ids_by_zone=None) -> list[str] | None:
    from auto_ontology.dal import users

    resolved = users.resolve_accessible_catalog_ids(zone_ids, data_ids_by_zone)
    return None if resolved is None else list(resolved["table_ids"])


def _table_iri(table_id: str) -> str:
    return iri(row_iri_single("catalog_table", table_id))


def _in_list(var: str, table_ids: list[str], negate: bool = False) -> str:
    items = ", ".join(_table_iri(t) for t in table_ids)
    return f"{var} {'NOT IN' if negate else 'IN'} ({items})"


def _in_scope_filter(term_var: str, table_ids: list[str] | None, tag: str = "") -> str:
    """All-or-nothing visibility: no representing table is outside *table_ids*."""
    if table_ids is None:
        return ""
    if not table_ids:
        # Oxigraph evaluates ``x NOT IN ()`` as false (SPARQL says true): with
        # nothing in scope, any representing table puts the term out of scope.
        return f"FILTER NOT EXISTS {{ ?scopelink{tag} {P.table__term.ref_term_id} {term_var} }}"
    return (
        f"FILTER NOT EXISTS {{ ?scopelink{tag} {P.table__term.ref_term_id} {term_var} ; "
        f"{P.table__term.ref_table_id} ?scopetb{tag} . "
        f"FILTER({_in_list(f'?scopetb{tag}', table_ids, negate=True)}) }}"
    )


def _semantic_terms_body(
    table_ids: list[str] | None,
    search: str | None,
    term_id: str | None = None,
) -> str:
    """Pattern binding ``?t`` (row IRI) and ``?name`` to the visible Term set."""
    body = (
        f"?t a <urn:ao:term> ; {P.term.source} {lit(SEMANTIC_SOURCE)} ; {P.term.name} ?name . "
        f"FILTER EXISTS {{ ?replink {P.table__term.ref_term_id} ?t }} "
        f"{_in_scope_filter('?t', table_ids)} "
    )
    if term_id is not None:
        body += f"{values('t', [row_iri_single('term', term_id)])} "
    if search:
        needle = search.strip()
        body += f"FILTER(CONTAINS(LCASE(?name), LCASE({lit(needle)}))) "
    return body


def _json_optional(subject: str, prop: str, var: str) -> str:
    return (
        f"OPTIONAL {{ {subject} {prop} {var} . "
        f"FILTER(datatype({var}) = <{_RDF_JSON}>) }}"
    )


def _certifications(
    flags: dict[str, int], table_ids: list[str] | None
) -> dict[str, str]:
    """``{term_id: certified|partial|pending}`` for ``flags = {term_id: own_certified_count}``.

    Mirrors ``_certification``: the Term's own two booleans plus one per
    ColumnAttribute / SqlAttribute link, each attribute count restricted to what
    *table_ids* can see (ColumnAttribute by its ``table_id``; SqlAttribute
    all-or-nothing over the tables its SQL touches).
    """
    if not flags:
        return {}
    term_iris = [row_iri_single("term", t) for t in flags]
    scope = None if table_ids is None else set(table_ids)
    certified = dict(flags)
    total = {t: 2 for t in flags}

    col_rows = client().select(
        "SELECT ?t ?cert ?tb WHERE { "
        + wrap(
            f"{values('t', term_iris)} "
            f"?cl {P.column_attribute__term.ref_term_id} ?t ; "
            f"    {P.column_attribute__term.ref_attribute_id} ?a . "
            f"?a {P.column_attribute.certified} ?cert . "
            f"OPTIONAL {{ ?a {P.column_attribute.table_id} ?tb }}"
        )
        + " }"
    )
    for row in col_rows:
        if scope is not None and row.get("tb") not in scope:
            continue
        tid = row_id(row["t"])
        total[tid] += 1
        certified[tid] += 1 if row.get("cert") is True else 0

    touched = (
        f"OPTIONAL {{ ?sx {P.sql_attribute__sql.ref_attribute_id} ?a ; "
        f"{P.sql_attribute__sql.ref_sql_query_id} ?q . "
        f"?sy {P.sql_query__table.ref_sql_query_id} ?q ; "
        f"{P.sql_query__table.ref_table_id} ?stb }}"
        if scope is not None
        else ""
    )
    sql_rows = client().select(
        "SELECT ?t ?a ?cert ?stb WHERE { "
        + wrap(
            f"{values('t', term_iris)} "
            f"?sl {P.sql_attribute__term.ref_term_id} ?t ; "
            f"    {P.sql_attribute__term.ref_attribute_id} ?a . "
            f"?a {P.sql_attribute.certified} ?cert . {touched}"
        )
        + " }"
    )
    attrs: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sql_rows:
        entry = attrs.setdefault(
            (row["t"], row["a"]), {"cert": row.get("cert") is True, "tables": set()}
        )
        if row.get("stb"):
            entry["tables"].add(row_id(row["stb"]))
    for (t, _a), entry in attrs.items():
        if scope is not None and not entry["tables"] <= scope:
            continue
        tid = row_id(t)
        total[tid] += 1
        certified[tid] += 1 if entry["cert"] else 0

    return {
        t: "certified" if certified[t] == total[t] else "pending" if certified[t] == 0 else "partial"
        for t in flags
    }


def _own_flags(row: dict[str, Any]) -> int:
    return (1 if row.get("name_certified") else 0) + (1 if row.get("description_certified") else 0)


def _zones_map(zone_ids, term_ids):
    from auto_ontology.dal import terms as dal_terms

    return dal_terms.fetch_term_zones_map(zone_ids, term_ids=term_ids)


def _tags_map(kind, ids):
    from auto_ontology.dal import tags

    return tags.fetch_tags_map(kind, ids)


# ---------------------------------------------------------------------------
# The Term list
# ---------------------------------------------------------------------------


def fetch_all_terms(
    zone_ids: list[str] | None = None,
    search: str | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """The glossary, paged; see ``auto_ontology.dal.terms.fetch_all_terms``."""
    from auto_ontology.dal.tags import TARGET_TERM

    table_ids = _scope_table_ids(zone_ids, data_ids_by_zone)
    body = (
        _semantic_terms_body(table_ids, search)
        + f"OPTIONAL {{ ?t {P.term.description} ?description }} "
        + _json_optional("?t", P.term.synonyms, "?synonyms")
        + f" OPTIONAL {{ ?t {P.term.name_certified} ?name_certified }} "
        + f"OPTIONAL {{ ?t {P.term.description_certified} ?description_certified }} "
        + f"OPTIONAL {{ ?t {P.term.owner_id} ?owner_id }}"
    )
    sparql = (
        "SELECT ?t ?name ?description ?synonyms ?name_certified ?description_certified ?owner_id "
        f"WHERE {{ {wrap(body)} }} ORDER BY LCASE(?name) ?t"
    )
    if skip:
        sparql += f" OFFSET {int(skip)}"
    if limit is not None:
        sparql += f" LIMIT {int(limit)}"
    rows = client().select(sparql)

    terms: list[dict[str, Any]] = []
    for r in rows:
        terms.append(
            {
                "name": r["name"],
                "description": r.get("description"),
                "synonyms": r.get("synonyms"),
                "id": row_id(r["t"]),
                "name_certified": r.get("name_certified"),
                "description_certified": r.get("description_certified"),
                "owner_id": r.get("owner_id"),
            }
        )
    term_ids = [t["id"] for t in terms]

    schema_names: dict[str, set[str]] = {}
    if term_ids:
        for r in client().select(
            "SELECT ?t ?sname WHERE { "
            + wrap(
                f"{values('t', [row_iri_single('term', i) for i in term_ids])} "
                f"?l {P.table__term.ref_term_id} ?t ; {P.table__term.ref_table_id} ?tb . "
                f"?tb {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.name} ?sname"
            )
            + " }"
        ):
            schema_names.setdefault(row_id(r["t"]), set()).add(r["sname"])
    certs = _certifications({t["id"]: _own_flags(t) for t in terms}, table_ids)

    zones_by_term = _zones_map(zone_ids, term_ids)
    tags_by_term = _tags_map(TARGET_TERM, term_ids)
    for row in terms:
        row["schema_names"] = sorted(schema_names.get(row["id"], ()))
        row["certification"] = certs[row["id"]]
        row["zones"] = zones_by_term.get(row["id"], [])
        row["tags"] = tags_by_term.get(row["id"], [])
    return terms


def count_terms(
    zone_ids: list[str] | None = None,
    search: str | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> int:
    """The unpaged size of :func:`fetch_all_terms`, from the same filter."""
    table_ids = _scope_table_ids(zone_ids, data_ids_by_zone)
    rows = client().select(
        "SELECT (COUNT(DISTINCT ?t) AS ?total) WHERE { "
        + wrap(_semantic_terms_body(table_ids, search))
        + " }"
    )
    return int(rows[0]["total"]) if rows and rows[0].get("total") is not None else 0


def get_full_term_by_id(
    term_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """One Term with its tables, table count, zones and certification."""
    from auto_ontology.dal.tags import TARGET_TERM

    table_ids = _scope_table_ids(zone_ids)
    body = (
        f"{values('t', [row_iri_single('term', term_id)])} "
        f"?t a <urn:ao:term> ; {P.term.name} ?name . "
        f"{_in_scope_filter('?t', table_ids)} "
        f"OPTIONAL {{ ?t {P.term.description} ?description }} "
        + _json_optional("?t", P.term.synonyms, "?synonyms")
        + f" OPTIONAL {{ ?t {P.term.name_certified} ?name_certified }} "
        f"OPTIONAL {{ ?t {P.term.description_certified} ?description_certified }} "
        f"OPTIONAL {{ ?t {P.term.owner_id} ?owner_id }} "
        f"OPTIONAL {{ ?t {P.term.created} ?created }} "
        f"OPTIONAL {{ ?t {P.term.modified} ?modified }} "
        f"OPTIONAL {{ ?t {P.term.created_by} ?created_by }} "
        f"OPTIONAL {{ ?t {P.term.modified_by} ?modified_by }}"
    )
    rows = client().select(
        "SELECT ?name ?description ?synonyms ?name_certified ?description_certified "
        "?owner_id ?created ?modified ?created_by ?modified_by "
        f"WHERE {{ {wrap(body)} }} LIMIT 1"
    )
    if not rows:
        return None
    r = rows[0]
    result: dict[str, Any] = {
        "name": r["name"],
        "description": r.get("description"),
        "synonyms": r.get("synonyms"),
        "id": term_id,
        "name_certified": r.get("name_certified"),
        "description_certified": r.get("description_certified"),
        "owner_id": r.get("owner_id"),
        "created": r.get("created"),
        "modified": r.get("modified"),
        "created_by": r.get("created_by"),
        "modified_by": r.get("modified_by"),
    }
    result["certification"] = _certifications({term_id: _own_flags(result)}, table_ids)[term_id]

    tables = client().select(
        "SELECT ?tb ?tname ?sch ?db WHERE { "
        + wrap(
            f"?l {P.table__term.ref_term_id} {iri(row_iri_single('term', term_id))} ; "
            f"   {P.table__term.ref_table_id} ?tb . "
            f"?tb {P.catalog_table.name} ?tname ; {P.catalog_table.ref_schema_id} ?sch . "
            f"?sch {P.catalog_schema.ref_database_id} ?db"
        )
        + " }"
    )
    result["tables"] = sorted(
        (
            {
                "id": row_id(t["tb"]),
                "name": t["tname"],
                "schema_id": row_id(t["sch"]),
                "db_id": row_id(t["db"]),
            }
            for t in tables
        ),
        key=lambda t: t["id"],
    )
    result["table_count"] = len(result["tables"])
    result["zones"] = _zones_map(zone_ids, [term_id]).get(term_id, [])
    result["tags"] = _tags_map(TARGET_TERM, [term_id]).get(term_id, [])
    return result


def fetch_terms_and_attributes_for_table(
    table_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(terms, attrs)`` written for one table, the inline embedding input."""
    tb = _table_iri(table_id)
    term_rows = client().select(
        "SELECT ?t ?name ?description ?synonyms ?sname WHERE { "
        + wrap(
            f"?l {P.table__term.ref_table_id} {tb} ; {P.table__term.ref_term_id} ?t . "
            f"?t {P.term.source} {lit(SEMANTIC_SOURCE)} ; {P.term.name} ?name . "
            f"{tb} {P.catalog_table.ref_schema_id} ?sch . ?sch {P.catalog_schema.name} ?sname . "
            f"OPTIONAL {{ ?t {P.term.description} ?description }} "
            + _json_optional("?t", P.term.synonyms, "?synonyms")
        )
        + " }"
    )
    terms = sorted(
        (
            {
                "name": r["name"],
                "description": r.get("description"),
                "synonyms": r.get("synonyms"),
                "id": row_id(r["t"]),
                "schema_names": [r["sname"]],
            }
            for r in term_rows
        ),
        key=lambda t: (t["name"].lower(), t["id"]),
    )
    attr_rows = client().select(
        "SELECT ?a ?col ?name ?description ?term_name ?source_column ?sample_values "
        "?is_unique ?tname ?sname WHERE { "
        + wrap(
            f"?col {P.catalog_column.ref_table_id} {tb} . "
            f"?l {P.column__has_attribute.ref_column_id} ?col ; "
            f"   {P.column__has_attribute.ref_attribute_id} ?a . "
            f"?a {P.column_attribute.source} {lit(SEMANTIC_SOURCE)} ; "
            f"   {P.column_attribute.name} ?name ; {P.column_attribute.term_name} ?term_name ; "
            f"   {P.column_attribute.source_column} ?source_column . "
            f"{tb} {P.catalog_table.name} ?tname ; {P.catalog_table.ref_schema_id} ?sch . "
            f"?sch {P.catalog_schema.name} ?sname . "
            f"OPTIONAL {{ ?a {P.column_attribute.description} ?description }} "
            f"OPTIONAL {{ ?col {P.catalog_column.sample_values} ?sample_values }} "
            f"OPTIONAL {{ ?col {P.catalog_column.is_unique} ?is_unique }}"
        )
        + " }"
    )
    attrs = sorted(
        (
            {
                "name": r["name"],
                "description": r.get("description"),
                "term_name": r["term_name"],
                "source_column": r["source_column"],
                "sample_values": r.get("sample_values"),
                "is_unique": r.get("is_unique"),
                "table_id": table_id,
                "table_name": r["tname"],
                "id": row_id(r["a"]),
                "schema_name": r["sname"],
                "_col": row_id(r["col"]),
            }
            for r in attr_rows
        ),
        key=lambda a: (a["id"], a["_col"]),
    )
    for a in attrs:
        del a["_col"]
    return terms, attrs


# ---------------------------------------------------------------------------
# ColumnAttributes of one Term
# ---------------------------------------------------------------------------


def fetch_column_attributes_by_term_id(
    term_id: str,
    zone_ids: list[str] | None = None,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """One Term's ColumnAttributes, with their columns and zones."""
    from auto_ontology.dal import attributes, zones
    from auto_ontology.dal.tags import TARGET_COLUMN_ATTRIBUTE

    table_ids = _scope_table_ids(zone_ids)
    scope = ""
    if table_ids is not None:
        items = ", ".join(lit(t) for t in table_ids)
        scope = f"FILTER(?table_id IN ({items})) "
    body = (
        f"?a a <urn:ao:column_attribute> ; {P.column_attribute.source} {lit(SEMANTIC_SOURCE)} ; "
        f"   {P.column_attribute.name} ?name ; {P.column_attribute.term_name} ?term_name ; "
        f"   {P.column_attribute.source_column} ?source_column ; "
        f"   {P.column_attribute.table_id} ?table_id ; "
        f"   {P.column_attribute.certified} ?certified . "
        f"{iri(row_iri_single('term', term_id))} {P.term.name} ?term_name . "
        f"{scope}"
        f"OPTIONAL {{ ?a {P.column_attribute.description} ?description }} "
        f"OPTIONAL {{ ?a {P.column_attribute.datatype} ?datatype }} "
        f"OPTIONAL {{ ?a {P.column_attribute.created} ?created }} "
        f"OPTIONAL {{ ?a {P.column_attribute.modified} ?modified }} "
        f"OPTIONAL {{ ?a {P.column_attribute.created_by} ?created_by }} "
        f"OPTIONAL {{ ?a {P.column_attribute.modified_by} ?modified_by }}"
    )
    sparql = (
        "SELECT ?a ?name ?description ?term_name ?source_column ?datatype ?table_id "
        "?certified ?created ?modified ?created_by ?modified_by "
        f"WHERE {{ {wrap(body)} }} ORDER BY ?name ?a"
    )
    if skip:
        sparql += f" OFFSET {int(skip)}"
    if limit is not None:
        sparql += f" LIMIT {int(limit)}"
    found = client().select(sparql)

    rows: list[dict[str, Any]] = [
        {
            "id": row_id(r["a"]),
            "name": r["name"],
            "description": r.get("description"),
            "term_name": r["term_name"],
            "source_column": r["source_column"],
            "datatype": r.get("datatype"),
            "table_id": r["table_id"],
            "certified": r["certified"],
            "created": r.get("created"),
            "modified": r.get("modified"),
            "created_by": r.get("created_by"),
            "modified_by": r.get("modified_by"),
        }
        for r in found
    ]

    # sample_values: the lowest-id column carrying the attribute, NULL or not.
    samples: dict[str, tuple[str, Any]] = {}
    if rows:
        for r in client().select(
            "SELECT ?a ?col ?sv WHERE { "
            + wrap(
                f"{values('a', [row_iri_single('column_attribute', x['id']) for x in rows])} "
                f"?l {P.column__has_attribute.ref_attribute_id} ?a ; "
                f"   {P.column__has_attribute.ref_column_id} ?col . "
                f"OPTIONAL {{ ?col {P.catalog_column.sample_values} ?sv }}"
            )
            + " }"
        ):
            aid, cid = row_id(r["a"]), row_id(r["col"])
            if aid not in samples or cid < samples[aid][0]:
                samples[aid] = (cid, r.get("sv"))
    for row in rows:
        row["sample_values"] = samples.get(row["id"], (None, None))[1]

    page_table_ids = [row["table_id"] for row in rows if row["table_id"]]
    zones_by_table = (
        zones.fetch_table_zones_map(zone_ids=zone_ids, table_ids=list(set(page_table_ids)))
        if page_table_ids
        else {}
    )
    columns_by_attr = attributes.fetch_column_attribute_columns_map([row["id"] for row in rows])
    tags_by_attr = _tags_map(TARGET_COLUMN_ATTRIBUTE, [row["id"] for row in rows])
    empty = {"primary_column": None, "referenced_columns": []}
    for row in rows:
        row["sample_values"] = stringify_sample_values(row.get("sample_values"))
    return [
        {
            **row,
            "zones": zones_by_table.get(row["table_id"], []),
            "tags": tags_by_attr.get(row["id"], []),
            **columns_by_attr.get(row["id"], empty),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Term <-> table pairs
# ---------------------------------------------------------------------------


def fetch_term_table_pairs(
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
    database_name: str | None = None,
    *,
    term_ids: list[str] | None = None,
    table_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """``{term_id, table_id, path}``, the one definition of "related"."""
    scope_ids = _scope_table_ids(zone_ids, data_ids_by_zone)
    if scope_ids == [] or term_ids == [] or table_ids == []:
        return []

    def restrict(tb: str, tag: str) -> str:
        parts = [f"?t {P.term.source} {lit(SEMANTIC_SOURCE)} ."]
        if scope_ids is not None:
            parts.append(f"FILTER({_in_list(tb, scope_ids)})")
            parts.append(_in_scope_filter("?t", scope_ids, tag))
        if term_ids is not None:
            parts.append(
                f"FILTER(?t IN ({', '.join(iri(row_iri_single('term', i)) for i in term_ids)}))"
            )
        if table_ids is not None:
            parts.append(f"FILTER({_in_list(tb, list(table_ids))})")
        if database_name is not None:
            parts.append(
                f"{tb} {P.catalog_table.ref_schema_id} ?dbsch{tag} . "
                f"?dbsch{tag} {P.catalog_schema.ref_database_id} ?dbdb{tag} . "
                f"?dbdb{tag} {P.catalog_database.name} {lit(database_name)} ."
            )
        return " ".join(parts)

    pairs: set[tuple[str, str, str]] = set()

    for r in client().select(
        "SELECT DISTINCT ?t ?tb WHERE { "
        + wrap(
            f"?l {P.table__term.ref_term_id} ?t ; {P.table__term.ref_table_id} ?tb . "
            + restrict("?tb", "r")
        )
        + " }"
    ):
        pairs.add((row_id(r["t"]), row_id(r["tb"]), REL_REPRESENTS))

    def leg(link_table: str, rel: str) -> str:
        return (
            f"{{ ?k {iri('urn:ao:' + link_table + '#ref-column_id')} ?col ; "
            f"{iri('urn:ao:' + link_table + '#ref-attribute_id')} ?a . "
            f"BIND({lit(rel)} AS ?path) }}"
        )

    for r in client().select(
        "SELECT DISTINCT ?t ?tb ?path WHERE { "
        + wrap(
            f"{leg('column__has_attribute', REL_HAS_ATTRIBUTE)} UNION "
            f"{leg('column__semantic_fk', REL_SEMANTIC_FK)} "
            f"?col {P.catalog_column.ref_table_id} ?tb . "
            f"?a {P.column_attribute.source} {lit(SEMANTIC_SOURCE)} . "
            f"?al {P.column_attribute__term.ref_attribute_id} ?a ; "
            f"    {P.column_attribute__term.ref_term_id} ?t . "
            + restrict("?tb", "a")
        )
        + " }"
    ):
        pairs.add((row_id(r["t"]), row_id(r["tb"]), r["path"]))

    return [
        {"term_id": t, "table_id": tb, "path": path} for t, tb, path in sorted(pairs)
    ]
