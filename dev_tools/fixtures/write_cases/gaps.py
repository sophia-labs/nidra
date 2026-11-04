# SPDX-License-Identifier: Apache-2.0
"""write_diff drivers for the gaps lane: owners and the re-ingest diff.

    uv run --no-sync python -m dev_tools.fixtures.write_diff \\
        dev_tools.fixtures.write_cases.gaps:reingest_diff --kwargs '{"drop_table": "category"}'
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import auto_ontology.dal  # noqa: F401  (installs the RDF ports before the store modules are imported)
from auto_ontology.catalog.constants import Labels
from auto_ontology.catalog.diff import update_diff_from_existing_schema
from auto_ontology.catalog.model.node import CatalogNode
from auto_ontology.catalog.model.schema import Schema
from auto_ontology.catalog.store import schemas as store_schemas

STAMP = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)


def _readback(database: str, schema: str) -> dict:
    cols = store_schemas.get_schema_columns(database, schema)
    tabs = store_schemas.get_schema_tables(database, schema)
    return {
        "tables": sorted(tabs[["table_name", "table_type"]].astype(str).values.tolist()),
        "columns": sorted(cols[["table_name", "column_name", "data_type"]].astype(str).values.tolist()),
    }


def reingest_diff(
    database: str = "pagila",
    schema: str = "public",
    drop_table: str | None = None,
    drop_column: str | None = None,
    retype_column: str | None = None,
    add_table: str | None = None,
    retype_table: str | None = None,
) -> dict:
    """Re-ingest ``database.schema`` from the stored shape, edited: ``drop_table``
    (name), ``drop_column`` (``table.column``), ``retype_column`` (``table.column``,
    data_type becomes ``text``), ``add_table`` (a new table with two columns),
    ``retype_table`` (name: table_type becomes ``view``)."""
    existing = store_schemas.load_schema_from_graph(database, schema)
    derived = ["id", "props", "match_props", "table_name_lower", "column_name_lower", "full_name"]
    tables = existing.tables_df.drop(columns=[c for c in derived if c in existing.tables_df.columns]).copy()
    columns = existing.columns_df.drop(columns=[c for c in derived if c in existing.columns_df.columns]).copy()
    # a parsed source carries numpy ints for ordinal positions, as pandas gives them
    columns = columns.sort_values(["table_name", "column_name"]).reset_index(drop=True)
    columns["ordinal_position"] = columns.groupby("table_name").cumcount() + 1
    if drop_table:
        tables = tables[tables.table_name != drop_table]
    if drop_column:
        t, c = drop_column.split(".")
        columns = columns[~((columns.table_name == t) & (columns.column_name == c))]
    if retype_column:
        t, c = retype_column.split(".")
        columns.loc[(columns.table_name == t) & (columns.column_name == c), "data_type"] = "text"
    if retype_table:
        tables.loc[tables.table_name == retype_table, "table_type"] = "view"
    if add_table:
        import pandas as pd

        tables = pd.concat([tables, tables.iloc[:1].assign(table_name=add_table, table_type="base table")], ignore_index=True)
        base = columns.iloc[:1]
        columns = pd.concat(
            [columns, *[base.assign(table_name=add_table, column_name=n, ordinal_position=i + 1) for i, n in enumerate(("alpha", "beta"))]],
            ignore_index=True,
        )
    new_schema = Schema(CatalogNode(name=database, label=Labels.DB, props={"name": database}), tables, columns)
    new_schema.create_schema_node(schema)
    update_diff_from_existing_schema(new_schema, STAMP)
    return _readback(database, schema)


def assign_owner_term(term_id: str, owner_id: str | None = "vera") -> dict:
    """``owners.assign_owner`` on a term, read back through ``current_owner`` and ``edit_subject``."""
    from dataclasses import asdict

    from auto_ontology.dal import owners
    from auto_ontology.dal import schema as s

    owners.assign_owner(s.term, term_id, owner_id)
    after = owners.edit_subject(term_id)
    return {"owner": owners.current_owner(s.term, term_id), "subject": asdict(after) if after else None}


def assign_owner_unknown() -> dict:
    from auto_ontology.dal import owners
    from auto_ontology.dal import schema as s

    owners.assign_owner(s.term, "00000000-0000-0000-0000-00000000dead", "vera")
    return {"owner": owners.current_owner(s.term, "00000000-0000-0000-0000-00000000dead")}


def rule_replay(drop_table_id: str | None = None, search_term: str = "film", objects: list[str] | None = None) -> dict:
    """A rule end to end: create the tag and rule, find its targets through the global
    search, label them, replay the rule (``reapply_rules``: ``matching_id_selects`` feeding
    ``remove_labels_no_longer_matched`` then ``apply_labels_now_matched``), and, when
    ``drop_table_id`` is given, delete that table and replay again so its labels come off."""
    from auto_ontology.dal import rules as rules_dal
    from auto_ontology.dal import tags
    from auto_ontology.server.rules import service as rules_service
    from dev_tools.fixtures.write_cases.writes_gov import _Run

    r = _Run()
    objects = objects or ["Table", "Column", "Term"]
    filters = {"objects": objects, "description": False, "synonyms": True}
    tag = r.step("tag", lambda: r.name("tag", tags.create_tag(name="Filmy")))
    rule_id = r.step("rule", lambda: rules_dal.create_rule(
        name="film things", search_term=search_term, text_match_option="contains",
        filters=filters, tags=[tag["id"]], created_by="u-ada"))
    targets = r.step("targets", lambda: sorted(rules_service.find_targets(
        search_term=search_term, text_match_option="contains", filters=filters)))
    r.step("label", lambda: rules_service.label_targets(rule_id=rule_id, tag_ids=[tag["id"]], targets=targets or []))
    r.step("replay_same", lambda: rules_service.reapply_rules())
    if drop_table_id:
        from auto_ontology.catalog.store.db import delete_table

        r.step("drop_table", lambda: delete_table(drop_table_id))
        r.step("replay_after_drop", lambda: rules_service.reapply_rules())
    r.step("count_tag_targets", lambda: len(tags.fetch_tags_map("table", [i for i, k in (targets or []) if k == "table"])))
    return r.out
