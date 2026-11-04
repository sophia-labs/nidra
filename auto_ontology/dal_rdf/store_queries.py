# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.catalog.store.queries``: statements and their links."""
from __future__ import annotations

import pandas as pd

from auto_ontology.catalog.store import registry
from auto_ontology.dal import schema as s
from auto_ontology.dal_rdf._common import P, class_iri, client, iri, lit, prop_iri, row_id, row_iri_single, select_rows, values, wrap
from auto_ontology.dal_rdf.store_rows import Upserter, commit, existing_subjects, new_row
from auto_ontology.rdf.writes import row_subject

TARGET = "auto_ontology.catalog.store.queries"
PORTS = [
    "add_query",
    "get_sql_by_full_query",
    "update_counters_and_timestamps_for_query_and_affected_data",
    "load_sqls_to_tables",
]


_ACCUMULATING = frozenset({"refs"})
_EMPTY_SQLS_COLUMNS = ["sql_id", "tbls", "cols", "nodes_count", "join_count", "union_count", "sql_full_query"]


def _existing_lists(table: str, subjects: list[str], column: str) -> dict[str, list[str]]:
    """The stored array ``column`` of each existing subject."""
    out: dict[str, list[str]] = {}
    if not subjects:
        return out
    rows = client().select(
        "SELECT ?s ?v WHERE { " + wrap(f"{values('s', subjects)} ?s {iri(prop_iri(table, column))} ?v .") + " }"
    )
    for r in rows:
        if isinstance(r["v"], list):  # the JSON literal; the member triples are plain strings
            out[r["s"]] = r["v"]
    return out


def add_query(edges) -> None:
    """Write parsed statements, the rows they reference, and the links (one request)."""
    from auto_ontology.catalog.store.queries import _link_kind, _reject_nested

    up = Upserter()
    links: list[tuple[registry.LinkSpec, dict, dict]] = []
    edges = list(edges)
    by_label: dict[str, list[dict]] = {}
    for source, target, _ in edges:
        by_label.setdefault(source.get_label(), []).append(source.get_match_props())
        by_label.setdefault(target.get_label(), []).append(target.get_match_props())
    try:
        for label, matches in by_label.items():
            up.prefetch(label, matches)
        for source, target, properties in edges:
            for endpoint in (source, target):
                _reject_nested(endpoint.get_properties())
                if endpoint.get_override_existing_props():
                    _reject_nested(endpoint.get_override_existing_props())
            _reject_nested(properties)
            spec = registry.link_spec(_link_kind(properties), source.get_label(), target.get_label())

            source_id = up.upsert(
                source.get_label(),
                source.get_match_props(),
                source.get_properties(),
                on_match=source.get_override_existing_props() or {},
            )
            target_id = up.upsert(
                target.get_label(),
                target.get_match_props(),
                target.get_properties(),
                parent_id=source_id if spec.is_parent_link else None,
                on_match=target.get_override_existing_props() or {},
            )
            if spec.is_parent_link:
                continue
            link = {spec.source_column: source_id, spec.target_column: target_id}
            payload = {k: v for k, v in properties.items() if k in spec.property_columns}
            links.append((spec, link, payload))
    except BaseException:
        _write_links(up, links)  # the edges before the failing one stay written
        raise
    _write_links(up, links)


def _write_links(up: Upserter, links) -> None:
    """INSERT .. ON CONFLICT of the association rows, resolved against the store."""
    state: dict[tuple[str, str], dict | None] = {}  # (table, subject) -> current row
    inserted: dict[str, list[dict]] = {}
    changed: dict[tuple[str, str], dict] = {}
    new_keys: set[tuple[str, str]] = set()
    by_table: dict[str, list[str]] = {}
    subjects = []
    for spec, link, payload in links:
        subject = row_subject(spec.table.name, link)
        subjects.append((spec, subject))
        by_table.setdefault(spec.table.name, []).append(subject)
    alive = {t: existing_subjects(t, subs) for t, subs in by_table.items()}
    stored_refs = {
        (t, subj): vals
        for t, subs in by_table.items()
        for subj, vals in _existing_lists(t, [x for x in subs if x in alive[t]], "refs").items()
    } if any("refs" in spec.property_columns for spec, _, _ in links) else {}

    for spec, link, payload in links:
        table = spec.table.name
        subject = row_subject(table, link)
        key = (table, subject)
        if key not in state:
            state[key] = {"refs": stored_refs.get(key)} if subject in alive[table] else None
        current = state[key]
        if current is None:
            row = new_row(table, {**link, **payload})
            inserted.setdefault(table, []).append(row)
            new_keys.add(key)
            state[key] = row
            continue
        if not payload:
            continue  # ON CONFLICT DO NOTHING
        updates = {}
        for column, value in payload.items():
            if column in _ACCUMULATING:
                merged = sorted(set(list(current.get(column) or []) + [str(v) for v in value]))
                updates[column] = merged
            else:
                updates[column] = value
        current.update(updates)
        if key not in new_keys:  # a row inserted by this batch is updated in place
            changed.setdefault(key, {}).update(updates)

    for table, rows in inserted.items():
        up.extra_inserts.setdefault(table, []).extend(rows)
    up.extra_updates.extend((t, subj, ch) for (t, subj), ch in changed.items())
    up.flush()


def get_sql_by_full_query(sql_full_query: str):
    """The statement's id, or None."""
    rows = client().select(
        "SELECT ?s WHERE { "
        + wrap(f"?s a {iri(class_iri('sql_query'))} ; {P.sql_query.sql_full_query} {lit(sql_full_query)} .")
        + " } ORDER BY ?s LIMIT 1"
    )
    return row_id(rows[0]["s"]) if rows else None


def update_counters_and_timestamps_for_query_and_affected_data(
    identical_sql_id: str,
    sql_node,
    update_data_last_query_timestamp: bool = True,
):
    """Add this sighting's count to the stored statement, and stamp the time."""
    from auto_ontology.catalog.query_stats import get_sql_counters

    latest_timestamp = sql_node.get_properties()["last_query_timestamp"]
    total_counter, _unreadable_month_counters = get_sql_counters(sql_node)

    subject = row_iri_single("sql_query", identical_sql_id)
    rows = client().select(
        "SELECT ?c WHERE { " + wrap(f"{iri(subject)} {P.sql_query.total_counter} ?c .") + " }"
    )
    current = rows[0]["c"] if rows else 0
    updates = [
        (
            "sql_query",
            subject,
            {"last_query_timestamp": latest_timestamp, "total_counter": current + total_counter},
        )
    ]
    if update_data_last_query_timestamp:
        for association, target, key in (
            (s.sql_query__table, s.catalog_table, "table_id"),
            (s.sql_query__column, s.catalog_column, "column_id"),
        ):
            if "last_query_timestamp" not in target.c:
                continue
            linked = client().select(
                "SELECT DISTINCT ?id WHERE { "
                + wrap(
                    f"?a a {iri(class_iri(association.name))} ; "
                    f"{iri(prop_iri(association.name, 'sql_query_id'))} {lit(identical_sql_id)} ; "
                    f"{iri(prop_iri(association.name, key))} ?id ."
                )
                + " }"
            )
            updates.extend(
                (target.name, row_iri_single(target.name, r["id"]), {"last_query_timestamp": latest_timestamp})
                for r in linked
            )
    commit(updates=updates)


def load_sqls_to_tables() -> pd.DataFrame:
    """Every stored statement with the table and column ids it references."""
    rows = select_rows("sql_query", ["nodes_count", "join_count", "union_count", "sql_full_query"])
    if not rows:
        return pd.DataFrame(columns=_EMPTY_SQLS_COLUMNS)

    def links(association: str, key: str) -> dict[str, list[str]]:
        found = client().select(
            "SELECT ?q ?k WHERE { "
            + wrap(
                f"?a a {iri(class_iri(association))} ; "
                f"{iri(prop_iri(association, 'sql_query_id'))} ?q ; {iri(prop_iri(association, key))} ?k ."
            )
            + " } ORDER BY ?q ?k"
        )
        out: dict[str, list[str]] = {}
        for r in found:
            out.setdefault(r["q"], []).append(r["k"])
        return out

    tables = links("sql_query__table", "table_id")
    columns = links("sql_query__column", "column_id")
    return pd.DataFrame(
        [
            {
                "sql_id": row["id"],
                "tbls": tables.get(row["id"], []),
                "cols": columns.get(row["id"], []),
                "nodes_count": row.get("nodes_count"),
                "join_count": row.get("join_count") or 0,
                "union_count": row.get("union_count") or 0,
                "sql_full_query": row["sql_full_query"],
            }
            for row in sorted(rows, key=lambda r: r["id"])
        ]
    )
