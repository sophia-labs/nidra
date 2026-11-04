# SPDX-License-Identifier: Apache-2.0
"""Extra golden captures for the graph-traversal reads of ``dal.attributes``.

Recorded from Postgres into ``dal_reads.attributes.json``. Each read gets found,
not-found and empty-input cases. The fixture holds three SEMANTIC_FK rows
(rental.customer_id and payment.customer_id into customer's id, inventory.film_id
into film's id) and no junction tables, so the junction read is captured on its
empty-but-valid paths.
"""
from __future__ import annotations

from typing import Any


def _columns() -> dict[tuple[str, str, str], str]:
    """``{(database, table, column): column_id}`` through the DAL."""
    from auto_ontology.dal.datasources import fetch_schemas_by_ids

    return {
        (r["database_name"], r["table_name"], r["column_name"]): r["column_id"]
        for r in fetch_schemas_by_ids()
    }


def capture(cap, ids: dict[str, Any]) -> None:
    from auto_ontology.dal import attributes

    run = cap.run
    cols = _columns()
    rental_cust = ids["col_rental_customer_id"]
    cust_cust = ids["col_customer_customer_id"]
    pay_cust = cols[("pagila", "payment", "customer_id")]
    inv_film = ids["col_inventory_film_id"]
    film_film = ids["col_film_film_id"]
    film_title = ids["col_film_title"]
    attr = ids["attr_film_id"]

    # -- column_participates_in_semantic_fk -------------------------------
    for label, col in (
        ("fk_holder", rental_cust),
        ("owner_side", film_film),
        ("plain_column", film_title),
        ("unknown", "no-such-id"),
        ("empty", ""),
    ):
        run(
            f"attributes.column_participates_in_semantic_fk__{label}",
            lambda col=col: attributes.column_participates_in_semantic_fk(col),
        )

    # -- fetch_attr_column_contexts ---------------------------------------
    run(
        "attributes.fetch_attr_column_contexts__any_database",
        lambda: attributes.fetch_attr_column_contexts([attr], database_name=None),
    )
    run(
        "attributes.fetch_attr_column_contexts__other_database",
        lambda: attributes.fetch_attr_column_contexts([attr], database_name="chinook"),
    )
    run(
        "attributes.fetch_attr_column_contexts__unknown_id",
        lambda: attributes.fetch_attr_column_contexts(
            ["no-such-id", attr], database_name="pagila"
        ),
    )
    run(
        "attributes.fetch_attr_column_contexts__empty",
        lambda: attributes.fetch_attr_column_contexts([], database_name="pagila"),
    )

    # -- find_join_path (beyond the four in capture_dal_golden) -----------
    run(
        "attributes.find_join_path__two_hops_via_hub",
        lambda: attributes.find_join_path(rental_cust, pay_cust),
    )
    run(
        "attributes.find_join_path__owner_to_fk_side",
        lambda: attributes.find_join_path(cust_cust, rental_cust),
    )
    run(
        "attributes.find_join_path__no_route",
        lambda: attributes.find_join_path(film_title, cust_cust),
    )
    run(
        "attributes.find_join_path__cross_database",
        lambda: attributes.find_join_path(
            film_film, cols[("chinook", "Track", "TrackId")]
        ),
    )
    run(
        "attributes.find_join_path__unknown_dest",
        lambda: attributes.find_join_path(film_film, "no-such-id"),
    )

    # -- find_shared_hub_bridge -------------------------------------------
    run(
        "attributes.find_shared_hub_bridge__found",
        lambda: attributes.find_shared_hub_bridge(rental_cust, pay_cust),
    )
    run(
        "attributes.find_shared_hub_bridge__found_reversed",
        lambda: attributes.find_shared_hub_bridge(pay_cust, rental_cust),
    )
    run(
        "attributes.find_shared_hub_bridge__different_hubs",
        lambda: attributes.find_shared_hub_bridge(rental_cust, inv_film),
    )
    run(
        "attributes.find_shared_hub_bridge__owner_side",
        lambda: attributes.find_shared_hub_bridge(cust_cust, rental_cust),
    )
    run(
        "attributes.find_shared_hub_bridge__same_column",
        lambda: attributes.find_shared_hub_bridge(rental_cust, rental_cust),
    )
    run(
        "attributes.find_shared_hub_bridge__unknown",
        lambda: attributes.find_shared_hub_bridge("no-such-id", rental_cust),
    )

    # -- find_anchor_hub_siblings -----------------------------------------
    from auto_ontology.dal.datasources import fetch_schemas_by_ids

    tables = {
        (r["database_name"], r["table_name"]): r["table_id"]
        for r in fetch_schemas_by_ids()
    }
    t_payment = tables[("pagila", "payment")]
    t_rental = tables[("pagila", "rental")]
    t_inventory = tables[("pagila", "inventory")]
    for label, table, cap_n in (
        ("payment_default", t_payment, 6),
        ("rental_uncapped", t_rental, None),
        ("payment_cap_zero", t_payment, 0),
        ("inventory_hub_without_siblings", t_inventory, 6),
        ("film_not_an_anchor", ids["table_film"], 6),
        ("unknown", "no-such-id", 6),
    ):
        run(
            f"attributes.find_anchor_hub_siblings__{label}",
            lambda table=table, cap_n=cap_n: attributes.find_anchor_hub_siblings(
                table, cap_n
            ),
        )

    # -- find_connected_junction_tables -----------------------------------
    run(
        "attributes.find_connected_junction_tables__no_junctions",
        lambda: attributes.find_connected_junction_tables(
            [ids["table_film"], ids["table_rental"], ids["table_customer"]]
        ),
    )
    run(
        "attributes.find_connected_junction_tables__unknown",
        lambda: attributes.find_connected_junction_tables(["no-such-id"]),
    )
    run(
        "attributes.find_connected_junction_tables__empty",
        lambda: attributes.find_connected_junction_tables([]),
    )
    run(
        "attributes.find_connected_junction_tables__blank_ids",
        lambda: attributes.find_connected_junction_tables(["", ""]),
    )

    # -- find_kept_table_bridges ------------------------------------------
    run(
        "attributes.find_kept_table_bridges__bridge_found",
        lambda: attributes.find_kept_table_bridges([t_rental, t_payment]),
    )
    run(
        "attributes.find_kept_table_bridges__bridge_already_kept",
        lambda: attributes.find_kept_table_bridges(
            [t_rental, t_payment, ids["table_customer"]]
        ),
    )
    run(
        "attributes.find_kept_table_bridges__not_allowed",
        lambda: attributes.find_kept_table_bridges(
            [t_rental, t_payment], allowed_table_ids={t_rental}
        ),
    )
    run(
        "attributes.find_kept_table_bridges__no_bridge",
        lambda: attributes.find_kept_table_bridges(
            [ids["table_film"], ids["table_category"]]
        ),
    )
    run(
        "attributes.find_kept_table_bridges__cap_skips_pairs",
        lambda: attributes.find_kept_table_bridges(
            [t_rental, t_payment, ids["table_film"], ids["table_category"]],
            max_bridge_tables=1,
        ),
    )
    run(
        "attributes.find_kept_table_bridges__single_table",
        lambda: attributes.find_kept_table_bridges([t_rental]),
    )
    run(
        "attributes.find_kept_table_bridges__empty",
        lambda: attributes.find_kept_table_bridges([]),
    )
