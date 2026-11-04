# SPDX-License-Identifier: Apache-2.0
"""RDF ports of the rest of ``auto_ontology.dal.exploration``: the graphs, edges
and detail pages the Exploration UI shows.

A second port file for the same DAL module (``exploration__more`` targets
``auto_ontology.dal.exploration``); it imports, and never edits, the private
helpers of ``dal_rdf.exploration``.

Reads that this module composes from other DAL modules (``fetch_related_term_ids``,
``fetch_terms_by_ids``, ``fetch_related_terms_counts``, ``fetch_column_attribute_counts``,
``fetch_sql_attribute_counts``, ``fetch_table_zones_map``, ``find_term_link_path``,
``term_is_in_scope``) are looked up on their DAL module at call time, so they
answer from whichever backend is installed for that module.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from auto_ontology.catalog.constants import Edges, Labels
from auto_ontology.dal_rdf._common import P, iri, lit, wrap, client
from auto_ontology.dal_rdf.exploration import (
    _column_ref_pattern,
    _column_ref_row,
    _resolve,
    _select,
    _sql_in_scope,
    _visible,
)
from auto_ontology.rdf.vocab import class_iri, row_id, row_iri_single
from auto_ontology.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_TERM,
    REL_HAS_ATTRIBUTE,
    REL_REPRESENTS,
    REL_SEMANTIC_FK,
    SEMANTIC_SOURCE,
)
from auto_ontology.utils.sample_values import stringify_sample_values

PORTS = [
    "fetch_data_exploration_edges",
    "fetch_data_exploration_graph",
    "fetch_exploration_related_nodes",
    "fetch_semantic_exploration_graph",
    "fetch_table_exploration_details",
    "fetch_term_exploration_details",
    "fetch_sql_exploration_details",
    "fetch_semantic_link_path",
]

MAX_EXPLORATION_GRAPH_NODES = 500


def _nk(name: Any, ident: str = "") -> tuple:
    """Sort key approximating Postgres ``ORDER BY name`` under ``en_US.utf8``:
    letters and digits first (case-folded, punctuation ignored), then lower before
    upper case, then the id."""
    text = name or ""
    return ("".join(c for c in text.casefold() if c.isalnum()), text.swapcase(), ident)


def _blank(text: Any) -> bool:
    """``nullif(trim(text), '') IS NULL`` (Postgres ``trim`` strips spaces only)."""
    return text is None or str(text).strip(" ") == ""


# ---------------------------------------------------------------------------
# What "related" means: shared statements and foreign keys
# ---------------------------------------------------------------------------


def _table_filter(resolved) -> list[str] | None:
    return None if resolved is None else list(resolved["table_ids"])


def _shared_sql_rows(table_filter: list[str] | None) -> list[tuple[str, str, str]]:
    """``(source_table, target_table, sql)`` for table pairs of one non-empty statement."""
    if table_filter == []:
        return []
    rows = _select(
        f"?a {P.sql_query__table.ref_sql_query_id} ?q ; {P.sql_query__table.ref_table_id} ?s . "
        f"?b {P.sql_query__table.ref_sql_query_id} ?q ; {P.sql_query__table.ref_table_id} ?t . "
        f"?q {P.sql_query.sql_full_query} ?sql .",
        "DISTINCT ?s ?t ?sql",
    )
    allowed = None if table_filter is None else set(table_filter)
    out = []
    for r in rows:
        s, t = row_id(r["s"]), row_id(r["t"])
        if _blank(r["sql"]):
            continue
        if allowed is not None and (s not in allowed or t not in allowed):
            continue
        out.append((s, t, r["sql"]))
    return out


def _fk_rows(table_filter: list[str] | None) -> list[dict[str, Any]]:
    """Foreign-key column pairs on different tables (distinct), in the SQL's key names."""
    if table_filter == []:
        return []
    rows = _select(
        f"?fk {P.column__foreign_key.ref_source_column_id} ?sc ; "
        f"{P.column__foreign_key.ref_target_column_id} ?tc . "
        f"?sc {P.catalog_column.ref_table_id} ?st ; {P.catalog_column.name} ?sname . "
        f"?tc {P.catalog_column.ref_table_id} ?tt ; {P.catalog_column.name} ?tname . "
        f"OPTIONAL {{ ?sc {P.catalog_column.sample_values} ?ssv }} "
        f"OPTIONAL {{ ?tc {P.catalog_column.sample_values} ?tsv }} "
        f"FILTER(?st != ?tt)",
        "DISTINCT ?st ?tt ?sname ?tname ?ssv ?tsv",
    )
    allowed = None if table_filter is None else set(table_filter)
    seen: set[tuple] = set()
    out = []
    for r in rows:
        row = {
            "source": row_id(r["st"]),
            "target": row_id(r["tt"]),
            "source_column": r["sname"],
            "target_column": r["tname"],
            "source_sample_values": r.get("ssv"),
            "target_sample_values": r.get("tsv"),
        }
        if allowed is not None and (row["source"] not in allowed or row["target"] not in allowed):
            continue
        key = tuple(str(v) for v in row.values())
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _adjacency(table_filter: list[str] | None) -> dict[str, set[str]]:
    """``{table: neighbours}`` -- the one definition of a data-layer relationship."""
    adj: dict[str, set[str]] = {}
    for s, t, _ in _shared_sql_rows(table_filter):
        if s != t:
            adj.setdefault(s, set()).add(t)
    for r in _fk_rows(table_filter):
        a, b = r["source"], r["target"]
        if a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    return adj


def fetch_data_exploration_edges(
    zone_ids: list[str] | None = None,
    data_ids_by_zone: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """Table pairs connected by a shared statement or a foreign key."""
    from auto_ontology.dal import users

    resolved = users.resolve_accessible_catalog_ids(zone_ids, data_ids_by_zone)
    table_filter = None
    if resolved is not None:
        table_filter = list(resolved["table_ids"])
        if not table_filter:
            return []

    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for source, target, sql in sorted(set(_shared_sql_rows(table_filter))):
        if not source < target:
            continue
        key = (source, target)
        edge = edges.setdefault(
            key,
            {
                "source": key[0],
                "target": key[1],
                "queries": [],
                "via_foreign_key": False,
                "foreign_keys": [],
                "relationship_types": [Edges.SQL],
            },
        )
        if sql not in edge["queries"]:
            edge["queries"].append(sql)

    for row in _fk_rows(table_filter):
        a, b = row["source"], row["target"]
        flipped = a > b
        key = (b, a) if flipped else (a, b)
        detail = {
            "source_column": row["target_column" if flipped else "source_column"],
            "target_column": row["source_column" if flipped else "target_column"],
            "source_sample_values": stringify_sample_values(
                row["target_sample_values" if flipped else "source_sample_values"]
            ),
            "target_sample_values": stringify_sample_values(
                row["source_sample_values" if flipped else "target_sample_values"]
            ),
        }
        edge = edges.get(key)
        if edge is None:
            edge = {
                "source": key[0],
                "target": key[1],
                "queries": [],
                "via_foreign_key": True,
                "foreign_keys": [],
                "relationship_types": [Edges.FOREIGN_KEY],
            }
            edges[key] = edge
        else:
            edge["via_foreign_key"] = True
            if Edges.FOREIGN_KEY not in edge["relationship_types"]:
                edge["relationship_types"].append(Edges.FOREIGN_KEY)
        if detail not in edge["foreign_keys"]:
            edge["foreign_keys"].append(detail)

    return sorted(edges.values(), key=lambda edge: (edge["source"], edge["target"]))


# ---------------------------------------------------------------------------
# Term <-> table links (REPRESENTS, or a column's attribute by either link)
# ---------------------------------------------------------------------------


def _term_table_links(
    *, term_id: str | None = None, table_id: str | None = None, table_ids: list[str] | None = None
) -> list[tuple[str, str, str]]:
    """``(term_id, table_id, rel_type)`` -- ``fetch_term_table_pairs`` unscoped, restricted."""
    from auto_ontology.dal_rdf import terms as port

    pairs = port.fetch_term_table_pairs(
        None,
        None,
        None,
        term_ids=[term_id] if term_id is not None else None,
        table_ids=[table_id] if table_id is not None else (table_ids if table_ids is not None else None),
    )
    return [(p["term_id"], p["table_id"], p["path"]) for p in pairs if p.get("path")]


def _rel_types(items) -> list[str]:
    return sorted({v for v in items if v})


# ---------------------------------------------------------------------------
# Table / term / statement detail pages
# ---------------------------------------------------------------------------


def fetch_table_exploration_details(
    table_id: str,
    zone_ids: list[str] | None = None,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """The statements referencing a table, and one page of its Terms."""
    resolved = _resolve(zone_ids)
    if resolved is not None and table_id not in resolved["table_ids"]:
        return {"queries": [], "terms": [], "terms_total": 0}

    t_iri = iri(row_iri_single("catalog_table", table_id))
    q_rows = _select(
        f"?l {P.sql_query__table.ref_table_id} {t_iri} ; {P.sql_query__table.ref_sql_query_id} ?q . "
        f"?q a {iri(class_iri('sql_query'))} . OPTIONAL {{ ?q {P.sql_query.sql_full_query} ?sql }}",
        "DISTINCT ?q ?sql",
        "ORDER BY ?q",
    )
    queries = [{"id": row_id(r["q"]), "sql": r["sql"]} for r in q_rows if r.get("sql")]

    by_term: dict[str, list[str]] = {}
    for term, _tb, rel in _term_table_links(table_id=table_id):
        by_term.setdefault(term, []).append(rel)
    meta = _term_meta(list(by_term))
    # The SQL joins `term`: a link to a term row that does not exist is no link.
    linked = [t for t in by_term if t in meta]
    ordered = sorted(linked, key=lambda t: _nk(meta[t]["name"], t))
    page = ordered[int(skip):]
    if limit is not None:
        page = page[: int(limit)]
    return {
        "queries": queries,
        "terms": [
            {
                "id": t,
                "name": meta[t]["name"],
                "description": meta[t]["description"],
                "relationship_types": _rel_types(by_term[t]),
            }
            for t in page
        ],
        "terms_total": len(linked),
    }


def _term_meta(term_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not term_ids:
        return {}
    from auto_ontology.dal_rdf._common import values

    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(term_ids), 2000):
        chunk = [row_iri_single("term", t) for t in term_ids[i : i + 2000]]
        rows = _select(
            f"{values('term', chunk)} ?term {P.term.name} ?name . "
            f"OPTIONAL {{ ?term {P.term.description} ?description }}",
            "?term ?name ?description",
        )
        for r in rows:
            out[row_id(r["term"])] = {"name": r.get("name"), "description": r.get("description")}
    return out


def _table_ref_rows(table_ids: list[str]) -> dict[str, dict[str, Any]]:
    """``_TABLE_REF_COLUMNS`` rows (inner-joined up to the database) by table id."""
    if not table_ids:
        return {}
    from auto_ontology.dal_rdf._common import values

    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(table_ids), 2000):
        chunk = [row_iri_single("catalog_table", t) for t in table_ids[i : i + 2000]]
        rows = _select(
            f"{values('table', chunk)} {_table_ref_pattern('?table')}",
            "?table ?name ?table_type ?schema ?schema_name ?database ?database_name",
        )
        for r in rows:
            out[row_id(r["table"])] = _table_ref_row(r)
    return out


def _table_ref_pattern(tb: str) -> str:
    return (
        f"{tb} {P.catalog_table.name} ?name ; {P.catalog_table.ref_schema_id} ?schema . "
        f"OPTIONAL {{ {tb} {P.catalog_table.table_type} ?table_type }} "
        f"?schema {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?database . "
        f"?database {P.catalog_database.name} ?database_name ."
    )


def _table_ref_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row_id(r["table"]),
        "name": r.get("name"),
        "table_type": r.get("table_type"),
        "database_id": row_id(r["database"]),
        "database_name": r.get("database_name"),
        "schema_id": row_id(r["schema"]),
        "schema_name": r.get("schema_name"),
    }


def fetch_term_exploration_details(
    term_id: str,
    zone_ids: list[str] | None = None,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """One ordered page of the Tables linked to a visible Term."""
    from auto_ontology.dal import terms

    resolved = _resolve(zone_ids)
    if not terms.term_is_in_scope(term_id, zone_ids, resolved):
        return {"tables": [], "tables_total": 0}

    allowed = None if resolved is None else set(resolved["table_ids"])
    by_table: dict[str, list[str]] = {}
    for _t, tb, rel in _term_table_links(term_id=term_id):
        if allowed is not None and tb not in allowed:
            continue
        by_table.setdefault(tb, []).append(rel)
    refs = _table_ref_rows(list(by_table))
    linked = [t for t in by_table if t in refs]
    total = len(set(by_table))  # the SQL counts distinct linked tables before the catalog join
    if not total:
        return {"tables": [], "tables_total": 0}
    ordered = sorted(linked, key=lambda t: _nk(refs[t]["name"], t))
    page = ordered[int(skip):]
    if limit is not None:
        page = page[: int(limit)]
    return {
        "tables": [{**refs[t], "relationship_types": _rel_types(by_table[t])} for t in page],
        "tables_total": total,
    }


def fetch_sql_exploration_details(
    sql_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The custom analyses, columns, tables and SqlAttributes hanging off a statement."""
    from auto_ontology.dal import terms

    resolved = _resolve(zone_ids)
    table_filter = _table_filter(resolved)
    q_iri = iri(row_iri_single("sql_query", sql_id))

    analysis_rows = _select(
        f"?l {P.custom_analysis__sql.ref_sql_query_id} {q_iri} ; "
        f"{P.custom_analysis__sql.ref_analysis_id} ?a . {q_iri} a {iri(class_iri('sql_query'))} . "
        f"?a {P.custom_analysis.name} ?name . OPTIONAL {{ ?a {P.custom_analysis.description} ?description }} "
        f"{_sql_in_scope(q_iri, table_filter)}",
        "DISTINCT ?a ?name ?description",
    )
    analyses = sorted(
        ({"id": row_id(r["a"]), "name": r["name"], "description": r.get("description")} for r in analysis_rows),
        key=lambda r: _nk(r["name"], r["id"]),
    )

    col_rows = _select(
        f"?l {P.sql_query__column.ref_sql_query_id} {q_iri} ; {P.sql_query__column.ref_column_id} ?col . "
        f"{_column_ref_pattern('?col')} {_visible('?table', table_filter)}",
        "DISTINCT ?col ?name ?description ?data_type ?table ?table_name ?schema ?schema_name ?database ?database_name",
    )
    columns = sorted(
        (_column_ref_row(r) for r in col_rows),
        key=lambda r: (_nk(r["table_name"]), _nk(r["name"], r["id"])),
    )

    tbl_rows = _select(
        f"?l {P.sql_query__table.ref_sql_query_id} {q_iri} ; {P.sql_query__table.ref_table_id} ?table . "
        f"{_table_ref_pattern('?table')} {_visible('?table', table_filter)}",
        "DISTINCT ?table ?name ?table_type ?schema ?schema_name ?database ?database_name",
    )
    tables = sorted((_table_ref_row(r) for r in tbl_rows), key=lambda r: _nk(r["name"], r["id"]))

    attr_rows = _select(
        f"?l {P.sql_attribute__sql.ref_sql_query_id} {q_iri} ; {P.sql_attribute__sql.ref_attribute_id} ?attr . "
        f"?attr {P.sql_attribute.name} ?name . OPTIONAL {{ ?attr {P.sql_attribute.description} ?description }} "
        f"OPTIONAL {{ ?tl {P.sql_attribute__term.ref_attribute_id} ?attr ; "
        f"{P.sql_attribute__term.ref_term_id} ?term . ?term {P.term.name} ?term_name }}",
        "DISTINCT ?attr ?name ?description ?term ?term_name",
    )
    attributes = []
    for r in sorted(attr_rows, key=lambda r: _nk(r["name"], row_id(r["attr"]))):
        term_id = row_id(r["term"]) if r.get("term") else None
        if term_id is not None and not terms.term_is_in_scope(term_id, zone_ids, resolved):
            continue
        attributes.append(
            {
                "id": row_id(r["attr"]),
                "name": r["name"],
                "description": r.get("description"),
                "term_id": term_id,
                "term_name": r.get("term_name"),
            }
        )

    return {
        "custom_analyses": analyses,
        "columns": columns,
        "tables": tables,
        "sql_attributes": attributes,
    }


# ---------------------------------------------------------------------------
# Related-nodes pages
# ---------------------------------------------------------------------------


def _fetch_data_exploration_related_nodes(node_id, zone_ids, *, skip, limit) -> dict[str, Any]:
    resolved = _resolve(zone_ids)
    if resolved is not None and node_id not in resolved["table_ids"]:
        return {"nodes": [], "total": 0}
    adj = _adjacency(_table_filter(resolved))
    related = adj.get(node_id, set())
    if not related:
        return {"nodes": [], "total": 0}

    from auto_ontology.dal_rdf._common import values

    rows: list[dict[str, Any]] = []
    rel_list = sorted(related)
    for i in range(0, len(rel_list), 2000):
        chunk = [row_iri_single("catalog_table", t) for t in rel_list[i : i + 2000]]
        found = _select(
            f"{values('t', chunk)} ?t {P.catalog_table.name} ?name . "
            f"OPTIONAL {{ ?t {P.catalog_table.table_type} ?table_type }} "
            f"OPTIONAL {{ ?t {P.catalog_table.ref_schema_id} ?schema . "
            f"OPTIONAL {{ ?schema {P.catalog_schema.ref_database_id} ?database }} }}",
            "?t ?name ?table_type ?schema ?database",
        )
        for r in found:
            rows.append(
                {
                    "id": row_id(r["t"]),
                    "name": r["name"],
                    "table_type": r.get("table_type"),
                    "database_id": row_id(r["database"]) if r.get("database") else None,
                    "schema_id": row_id(r["schema"]) if r.get("schema") else None,
                }
            )
    rows.sort(key=lambda r: _nk(r["name"], r["id"]))
    rows = rows[int(skip):]
    if limit is not None:
        rows = rows[: int(limit)]
    return {
        "nodes": [{**row, "relationship_count": len(adj.get(row["id"], ()))} for row in rows],
        "total": len(related),
    }


def _fetch_semantic_exploration_related_nodes(node_id, zone_ids, *, skip, limit) -> dict[str, Any]:
    from auto_ontology.dal import terms

    resolved = _resolve(zone_ids)
    if not terms.term_is_in_scope(node_id, zone_ids, resolved):
        return {"nodes": [], "total": 0}
    related_ids = terms.fetch_related_term_ids(node_id, zone_ids, resolved)
    if not related_ids:
        return {"nodes": [], "total": 0}
    page = terms.fetch_terms_by_ids(related_ids, skip=skip, limit=limit)
    counts = {
        row["term_id"]: row["count"]
        for row in terms.fetch_related_terms_counts(
            zone_ids, term_ids=[t["id"] for t in page], data_ids_by_zone=resolved
        )
    }
    return {
        "nodes": [
            {"id": t["id"], "name": t.get("name") or "", "relationship_count": counts.get(t["id"], 0)}
            for t in page
        ],
        "total": len(related_ids),
    }


def fetch_exploration_related_nodes(
    node_id: str,
    layer: str,
    zone_ids: list[str] | None = None,
    *,
    skip: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """One page of nodes related to an Exploration node."""
    if layer == "data":
        return _fetch_data_exploration_related_nodes(node_id, zone_ids, skip=skip, limit=limit)
    if layer == "semantic":
        return _fetch_semantic_exploration_related_nodes(node_id, zone_ids, skip=skip, limit=limit)
    raise ValueError(f"Unsupported Exploration layer: {layer}")


# ---------------------------------------------------------------------------
# Whole-graph payloads
# ---------------------------------------------------------------------------


def fetch_data_exploration_graph(
    zone_ids: list[str] | None = None,
    limit: int = MAX_EXPLORATION_GRAPH_NODES,
    database_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """The whole data-layer graph in one payload."""
    from auto_ontology.dal import zones
    from auto_ontology.dal_rdf import datasources as ds

    resolved = _resolve(zone_ids)
    extra = ""
    if database_id is not None:
        extra = f"FILTER(?database = {iri(row_iri_single('catalog_database', database_id))})"
    rows = _select(
        f"?t {P.catalog_table.name} ?name ; {P.catalog_table.ref_schema_id} ?schema . "
        f"OPTIONAL {{ ?t {P.catalog_table.table_type} ?table_type }} "
        f"OPTIONAL {{ ?t {P.catalog_table.description} ?description }} "
        f"?schema {P.catalog_schema.name} ?schema_name ; {P.catalog_schema.ref_database_id} ?database . "
        f"?database {P.catalog_database.name} ?database_name . {extra} "
        f"{_visible('?t', _table_filter(resolved))}",
        "?t ?name ?table_type ?description ?schema ?schema_name ?database ?database_name",
    )
    rows.sort(key=lambda r: _nk(r["name"], row_id(r["t"])))
    if database_id is None:
        rows = rows[: max(1, min(limit, MAX_EXPLORATION_GRAPH_NODES))]

    ids = [row_id(r["t"]) for r in rows]
    iris = [row_iri_single("catalog_table", t) for t in ids]
    descs = ds._table_descriptions([{"id": row_id(r["t"]), "description": r.get("description")} for r in rows])
    columns = ds._count_by_table("catalog_column", "ref_table_id", iris)
    sqls = ds._count_by_table("sql_query__table", "ref_table_id", iris)
    term_counts = ds._terms_counts(iris)

    node_ids = set(ids)
    zones_by_table = zones.fetch_table_zones_map(zone_ids, data_ids_by_zone=resolved)
    edges = fetch_data_exploration_edges(zone_ids=zone_ids, data_ids_by_zone=resolved)
    degree: Counter[str] = Counter()
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1

    nodes = []
    for r, tid in zip(rows, ids):
        nodes.append(
            {
                "id": tid,
                "name": r["name"],
                "table_type": r.get("table_type"),
                "database_id": row_id(r["database"]),
                "database_name": r["database_name"],
                "schema_id": row_id(r["schema"]),
                "schema_name": r["schema_name"],
                "description": descs.get(tid),
                "columns_count": columns.get(tid, 0),
                "sql_count": sqls.get(tid, 0),
                "terms_count": term_counts.get(tid, 0),
                "zones": zones_by_table.get(tid, []),
                "relationship_count": degree[tid],
            }
        )
    return {
        "nodes": nodes,
        "links": [e for e in edges if e["source"] in node_ids and e["target"] in node_ids],
    }


def _table_ids_for_database(database_id: str) -> set[str]:
    rows = _select(
        f"?t {P.catalog_table.ref_schema_id} ?schema . "
        f"?schema {P.catalog_schema.ref_database_id} {iri(row_iri_single('catalog_database', database_id))} .",
        "DISTINCT ?t",
    )
    return {row_id(r["t"]) for r in rows}


def fetch_semantic_exploration_graph(
    zone_ids: list[str] | None = None,
    limit: int = MAX_EXPLORATION_GRAPH_NODES,
    database_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """The whole semantic-layer graph in one payload."""
    from auto_ontology.dal import sql_attributes, terms

    resolved = _resolve(zone_ids)
    all_terms = terms.fetch_all_terms(zone_ids=zone_ids, data_ids_by_zone=resolved)
    column_counts = {
        r["term_id"]: r["count"]
        for r in terms.fetch_column_attribute_counts(zone_ids=zone_ids, data_ids_by_zone=resolved)
    }
    sql_counts = {
        r["term_id"]: r["count"]
        for r in sql_attributes.fetch_sql_attribute_counts(zone_ids=zone_ids, data_ids_by_zone=resolved)
    }
    pairs = terms.fetch_term_table_pairs(zone_ids, data_ids_by_zone=resolved)
    term_tables, table_terms = terms.build_term_table_maps(pairs)
    paths_by_pair: dict[tuple[str, str], set[str]] = {}
    for row in pairs:
        term_id, table_id, path = row.get("term_id"), row.get("table_id"), row.get("path")
        if term_id and table_id and path:
            paths_by_pair.setdefault((term_id, table_id), set()).add(path)

    if database_id is not None:
        table_ids = _table_ids_for_database(database_id)
        in_db = {r["term_id"] for r in pairs if r.get("term_id") and r.get("table_id") in table_ids}
        all_terms = [t for t in all_terms if t["id"] in in_db]

    visible = {t["id"] for t in all_terms}
    degree: dict[str, int] = {}
    link_keys: set[tuple[str, str]] = set()
    link_types: dict[tuple[str, str], set[str]] = {}
    for term_id, tables in term_tables.items():
        related: set[str] = set()
        for table_id in tables:
            related.update(table_terms.get(table_id, set()))
        related.discard(term_id)
        degree[term_id] = len(related)
        if term_id not in visible:
            continue
        for other_id in related:
            if other_id not in visible:
                continue
            key = tuple(sorted((term_id, other_id)))
            link_keys.add(key)
            types = link_types.setdefault(key, set())
            for table_id in tables & term_tables.get(other_id, set()):
                types.update(paths_by_pair.get((term_id, table_id), ()))
                types.update(paths_by_pair.get((other_id, table_id), ()))

    nodes = sorted(
        (
            {
                "id": t["id"],
                "name": t["name"],
                "description": t.get("description"),
                "synonyms": t.get("synonyms") or [],
                "zones": t.get("zones") or [],
                "relationship_count": degree.get(t["id"], 0),
                "column_attributes_count": column_counts.get(t["id"], 0),
                "sql_attributes_count": sql_counts.get(t["id"], 0),
            }
            for t in all_terms
        ),
        key=lambda node: node["name"],
    )
    if database_id is None:
        nodes = nodes[: max(1, min(limit, MAX_EXPLORATION_GRAPH_NODES))]
    kept = {n["id"] for n in nodes}
    return {
        "nodes": nodes,
        "links": [
            {
                "source": a,
                "target": b,
                "relationship_types": sorted(link_types.get((a, b), set())),
            }
            for a, b in sorted(link_keys)
            if a in kept and b in kept
        ],
    }


_LINK_PATH_NODE_TYPE = {
    LABEL_TERM: "term",
    Labels.TABLE: "table",
    Labels.COLUMN: "column",
    LABEL_COLUMN_ATTRIBUTE: "columnAttribute",
}


def _link_path_node(node: dict[str, Any]) -> dict[str, Any]:
    label = node.get("label")
    result = {
        "id": node.get("id"),
        "name": node.get("name"),
        "type": _LINK_PATH_NODE_TYPE.get(label, (label or "").lower()),
    }
    if label in (Labels.TABLE, Labels.COLUMN):
        result["database_id"] = node.get("database_id")
        result["database_name"] = node.get("database_name")
        result["schema_id"] = node.get("schema_id")
        result["schema_name"] = node.get("schema_name")
    if label == Labels.COLUMN:
        result["table_id"] = node.get("table_id")
        result["table_name"] = node.get("table_name")
    return result


def fetch_semantic_link_path(
    source_term_id: str,
    target_term_id: str,
    zone_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The real hop chain behind one term<->term edge of the semantic graph."""
    from auto_ontology.dal import terms

    hops = terms.find_term_link_path(source_term_id, target_term_id)
    if not hops:
        return {"hops": []}
    resolved = _resolve(zone_ids)
    if resolved is not None:
        table_ids = resolved["table_ids"]
        for hop in hops:
            for side in (hop["source"], hop["target"]):
                label = side.get("label")
                if label == Labels.TABLE and side.get("id") not in table_ids:
                    return {"hops": []}
                if label == Labels.COLUMN and side.get("table_id") not in table_ids:
                    return {"hops": []}
    return {
        "hops": [
            {
                "relationship": hop["relationship"],
                "source": _link_path_node(hop["source"]),
                "target": _link_path_node(hop["target"]),
            }
            for hop in hops
        ]
    }
