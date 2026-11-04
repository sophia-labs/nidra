"""Captures for the zone-scope lane: users, zones, tags, connections, and the
attribute / node reads the base golden covers thinly or not at all."""
from __future__ import annotations

from typing import Any

MISSING = "00000000-0000-0000-0000-00000000dead"
TAG_KINDS = ("term", "table", "column", "column_attribute", "sql_attribute")


def _raises(fn):
    """The exception type and message as a value: a raising read is a fact, but
    ``Capture.errors`` must stay empty for the suite to pass."""
    try:
        return {"returned": fn()}
    except Exception as exc:  # noqa: BLE001
        return {"raised": type(exc).__name__, "message": str(exc)}


def capture(cap, ids: dict[str, Any]) -> None:
    from auto_ontology.dal import attributes, datasources, exploration, tags, users, zones

    run = cap.run
    film, rental = ids["table_film"], ids["table_rental"]
    zone_film, zone_cross, zone_retired = ids["zone_film"], ids["zone_cross"], ids["zone_retired"]

    # -- users ---------------------------------------------------------------
    for label, zone_ids in (
        ("film", [zone_film]),
        ("cross", [zone_cross]),
        ("retired", [zone_retired]),
        ("film+cross", [zone_film, zone_cross]),
        ("all", [zone_film, zone_cross, zone_retired]),
        ("missing", [MISSING]),
    ):
        run(
            f"users.get_accessible_catalog_ids_for_zones__{label}",
            lambda z=zone_ids: users.get_accessible_catalog_ids_for_zones(z),
        )
    run("users.get_accessible_catalog_ids_for_zones__empty", lambda: users.get_accessible_catalog_ids_for_zones([]))
    run("users.resolve_accessible_catalog_ids__none", lambda: users.resolve_accessible_catalog_ids(None))
    run(
        "users.resolve_accessible_catalog_ids__reuse",
        lambda: users.resolve_accessible_catalog_ids([zone_film], {"db_ids": {"x"}, "schema_ids": set(), "table_ids": {film}}),
    )
    run("users.resolve_accessible_catalog_ids__reuse_unscoped", lambda: users.resolve_accessible_catalog_ids(None, {"db_ids": set()}))
    from auto_ontology.dal import schema as s

    def table_filter(zone_ids, reuse=None):
        pred = users.resolve_table_filter(zone_ids, s.catalog_table.c.id, data_ids_by_zone=reuse)
        if pred is None:
            return None
        return sorted(pred.right.value) if hasattr(pred.right, "value") else sorted(c.value for c in pred.right.clauses)

    run("users.resolve_table_filter__none", lambda: table_filter(None))
    run("users.resolve_table_filter__film", lambda: table_filter([zone_film]))
    run("users.resolve_table_filter__empty", lambda: table_filter([]))
    run(
        "users.resolve_table_filter__reuse",
        lambda: table_filter([zone_film], {"db_ids": set(), "schema_ids": set(), "table_ids": {rental}}),
    )

    # -- zones ---------------------------------------------------------------
    run("zones.get_zone_by_id__cross", lambda: zones.get_zone_by_id(zone_cross))
    run("zones.get_zone_by_id__missing", lambda: zones.get_zone_by_id(MISSING))

    # fetch_table_zones_map lives on zones; the golden names it under exploration.
    for label, kwargs in (
        ("zones_film", {"zone_ids": [zone_film]}),
        ("zones_cross", {"zone_ids": [zone_cross]}),
        ("zones_retired", {"zone_ids": [zone_retired]}),
        ("zones_all", {"zone_ids": [zone_film, zone_cross, zone_retired]}),
        ("zones_empty", {"zone_ids": []}),
        ("admin_table_ids_film", {"table_ids": [film]}),
        ("admin_table_ids_empty", {"table_ids": []}),
        ("admin_table_ids_missing", {"table_ids": [MISSING]}),
        ("scoped_table_ids_intersect", {"zone_ids": [zone_film], "table_ids": [film, rental]}),
        ("scoped_table_ids_outside", {"zone_ids": [zone_film], "table_ids": [ids["table_track"]]}),
        ("reuse_resolved", {"zone_ids": [zone_film], "data_ids_by_zone": {"db_ids": set(), "schema_ids": set(), "table_ids": {film}}}),
    ):
        run(f"exploration.fetch_table_zones_map__{label}", lambda k=kwargs: zones.fetch_table_zones_map(**k))
        run(f"exploration.fetch_table_zones_map__reexport_{label}", lambda k=kwargs: exploration.fetch_table_zones_map(**k))

    # -- tags: every kind the DAL accepts, the empty input, an unknown kind ---
    kind_ids = {
        "term": ids["term_Film"],
        "table": film,
        "column": ids["col_film_title"],
        "column_attribute": ids["attr_film_id"],
        "sql_attribute": ids["sqlattr_revenue"],
    }
    from auto_ontology.dal.tags import _TARGET_COLUMNS

    for kind in _TARGET_COLUMNS:
        item = kind_ids[kind]
        run(f"tags.fetch_tags_map__{kind}", lambda k=kind, i=item: tags.fetch_tags_map(k, [i]))
        run(f"tags.fetch_tags_map__{kind}_missing", lambda k=kind: tags.fetch_tags_map(k, [MISSING]))
        run(f"tags.fetch_tags_map__{kind}_empty", lambda k=kind: tags.fetch_tags_map(k, []))
    run("tags.fetch_tags_map__unknown_kind", lambda: _raises(lambda: tags.fetch_tags_map("nope", [film])))
    run("tags.fetch_tags_map__unknown_kind_empty", lambda: _raises(lambda: tags.fetch_tags_map("nope", [])))

    # -- attributes ----------------------------------------------------------
    attr = ids["attr_film_id"]
    run("attributes.fetch_column_attribute_columns_map__film_id", lambda: attributes.fetch_column_attribute_columns_map([attr]))
    run("attributes.fetch_column_attribute_columns_map__missing", lambda: attributes.fetch_column_attribute_columns_map([MISSING]))
    run("attributes.fetch_column_attribute_columns_map__mixed", lambda: attributes.fetch_column_attribute_columns_map([attr, MISSING, attr]))
    run("attributes.fetch_column_attribute_columns_map__empty", lambda: attributes.fetch_column_attribute_columns_map([]))
    for label in ("film_film_id", "film_title", "inventory_film_id", "customer_customer_id"):
        run(
            f"attributes.find_column_attribute_by_column_id__{label}",
            lambda c=ids[f"col_{label}"]: attributes.find_column_attribute_by_column_id(c),
        )
    run("attributes.find_column_attribute_by_column_id__missing", lambda: attributes.find_column_attribute_by_column_id(MISSING))
    for label, name in (("pagila", "pagila"), ("chinook", "chinook"), ("missing", "no_such_db")):
        run(f"attributes.find_unlinked_fk_columns__{label}", lambda n=name: attributes.find_unlinked_fk_columns(n))

    # -- datasources.fetch_node_properties_by_id -------------------------------
    for label, (item, kind) in {
        "database": (ids["db_pagila"], "Database"),
        "schema": (ids["schema_public"], "Schema"),
        "table": (rental, "Table"),
        "column": (ids["col_film_title"], "Column"),
    }.items():
        run(f"datasources.fetch_node_properties_by_id__{label}", lambda i=item, k=kind: datasources.fetch_node_properties_by_id(i, k))
    run("datasources.fetch_node_properties_by_id__missing", lambda: datasources.fetch_node_properties_by_id(MISSING, "Table"))
    run("datasources.fetch_node_properties_by_id__unknown_label", lambda: datasources.fetch_node_properties_by_id(film, "Term"))
    run("datasources.fetch_node_properties_by_id__label_list", lambda: datasources.fetch_node_properties_by_id(film, ["Column", "Table"]))
    run("datasources.fetch_node_properties_by_id__label_list_unknown", lambda: datasources.fetch_node_properties_by_id(film, ["Table", "Term"]))
    run("datasources.fetch_node_properties_by_id__wrong_label", lambda: datasources.fetch_node_properties_by_id(film, "Column"))

