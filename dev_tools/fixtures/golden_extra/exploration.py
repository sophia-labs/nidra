# SPDX-License-Identifier: Apache-2.0
"""Captures for the exploration detail reads the base golden does not cover."""
from __future__ import annotations

from typing import Any

MISSING = "00000000-0000-0000-0000-00000000dead"


def capture(cap, ids: dict[str, Any]) -> None:
    from auto_ontology.dal import exploration

    run = cap.run
    scoped = [ids["zone_film"]]
    modes = (("admin", None), ("scoped", scoped), ("none", []))

    # -- fetch_column_exploration_details: a primary key (referenced by FKs and
    # statements), a foreign key (points at one), a plain column, a missing id.
    columns = {
        "film_film_id": ids["col_film_film_id"],
        "film_title": ids["col_film_title"],
        "inventory_film_id": ids["col_inventory_film_id"],
        "rental_customer_id": ids["col_rental_customer_id"],
        "customer_customer_id": ids["col_customer_customer_id"],
        "missing": MISSING,
    }
    for label, column_id in columns.items():
        for mode, zone_ids in modes:
            run(
                f"exploration.fetch_column_exploration_details__{label}[{mode}]",
                lambda c=column_id, z=zone_ids: exploration.fetch_column_exploration_details(c, z),
            )

    # -- fetch_column_attribute_exploration_details: found, paged, missing.
    attr = ids["attr_film_id"]
    for mode, zone_ids in modes:
        run(
            f"exploration.fetch_column_attribute_exploration_details__film_id[{mode}]",
            lambda z=zone_ids: exploration.fetch_column_attribute_exploration_details(attr, z),
        )
        run(
            f"exploration.fetch_column_attribute_exploration_details__missing[{mode}]",
            lambda z=zone_ids: exploration.fetch_column_attribute_exploration_details(MISSING, z),
        )
    for label, skip, limit in (("limit1", 0, 1), ("skip1", 1, None), ("skip1_limit1", 1, 1), ("skip99", 99, None)):
        run(
            f"exploration.fetch_column_attribute_exploration_details__page_{label}",
            lambda s=skip, n=limit: exploration.fetch_column_attribute_exploration_details(
                attr, None, skip=s, limit=n
            ),
        )

    # -- fetch_sql_attribute_exploration_details: found, missing.
    sqlattr = ids["sqlattr_revenue"]
    for mode, zone_ids in modes:
        run(
            f"exploration.fetch_sql_attribute_exploration_details__revenue[{mode}]",
            lambda z=zone_ids: exploration.fetch_sql_attribute_exploration_details(sqlattr, z),
        )
        run(
            f"exploration.fetch_sql_attribute_exploration_details__missing[{mode}]",
            lambda z=zone_ids: exploration.fetch_sql_attribute_exploration_details(MISSING, z),
        )
