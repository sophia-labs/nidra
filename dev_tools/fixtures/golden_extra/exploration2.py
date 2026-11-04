# SPDX-License-Identifier: Apache-2.0
"""Captures for the exploration graph / edge / detail reads the base golden does not cover.

Names carry an ``__e2_`` variant so they never collide with the base captures.
"""
from __future__ import annotations

from typing import Any

MISSING = "00000000-0000-0000-0000-00000000dead"


def capture(cap, ids: dict[str, Any]) -> None:
    from auto_ontology.dal import exploration

    run = cap.run
    scoped = [ids["zone_film"]]
    modes = (("admin", None), ("scoped", scoped), ("none", []))
    film, rental, customer = ids["table_film"], ids["table_rental"], ids["table_customer"]
    term_film, term_customer, term_payment, term_track = (
        ids["term_Film"], ids["term_Customer"], ids["term_Payment"], ids["term_Track"],
    )

    # -- fetch_data_exploration_edges / the two graphs: the base golden has the
    # zone modes; add the database filter and the node cap.
    for mode, zone_ids in modes:
        run(
            f"exploration.fetch_data_exploration_graph__e2_pagila[{mode}]",
            lambda z=zone_ids: exploration.fetch_data_exploration_graph(z, database_id=ids["db_pagila"]),
        )
        run(
            f"exploration.fetch_data_exploration_graph__e2_limit3[{mode}]",
            lambda z=zone_ids: exploration.fetch_data_exploration_graph(z, limit=3),
        )
        run(
            f"exploration.fetch_data_exploration_graph__e2_missing_db[{mode}]",
            lambda z=zone_ids: exploration.fetch_data_exploration_graph(z, database_id=MISSING),
        )
        run(
            f"exploration.fetch_semantic_exploration_graph__e2_pagila[{mode}]",
            lambda z=zone_ids: exploration.fetch_semantic_exploration_graph(z, database_id=ids["db_pagila"]),
        )
        run(
            f"exploration.fetch_semantic_exploration_graph__e2_limit2[{mode}]",
            lambda z=zone_ids: exploration.fetch_semantic_exploration_graph(z, limit=2),
        )

    # -- fetch_table_exploration_details: tables with statements and terms, paging, missing.
    for label, table_id in (("film", film), ("rental", rental), ("customer", customer), ("missing", MISSING)):
        for mode, zone_ids in modes:
            run(
                f"exploration.fetch_table_exploration_details__e2_{label}[{mode}]",
                lambda t=table_id, z=zone_ids: exploration.fetch_table_exploration_details(t, z),
            )
    for label, skip, limit in (("limit1", 0, 1), ("skip1", 1, None), ("skip1_limit1", 1, 1), ("skip99", 99, None)):
        run(
            f"exploration.fetch_table_exploration_details__e2_page_{label}",
            lambda s=skip, n=limit: exploration.fetch_table_exploration_details(film, None, skip=s, limit=n),
        )

    # -- fetch_term_exploration_details
    for label, term_id in (
        ("film", term_film), ("customer", term_customer), ("payment", term_payment),
        ("track", term_track), ("missing", MISSING),
    ):
        for mode, zone_ids in modes:
            run(
                f"exploration.fetch_term_exploration_details__e2_{label}[{mode}]",
                lambda t=term_id, z=zone_ids: exploration.fetch_term_exploration_details(t, z),
            )
    for label, skip, limit in (("limit1", 0, 1), ("skip1", 1, None), ("skip1_limit1", 1, 1), ("skip99", 99, None)):
        run(
            f"exploration.fetch_term_exploration_details__e2_page_{label}",
            lambda s=skip, n=limit: exploration.fetch_term_exploration_details(term_film, None, skip=s, limit=n),
        )

    # -- fetch_sql_exploration_details: every statement reachable from the fixture's tables.
    statements: dict[str, Any] = {}
    for table_id in (film, rental, customer, ids["table_payment"], ids["table_inventory"]):
        for q in exploration.fetch_table_exploration_details(table_id).get("queries", []):
            statements.setdefault(q["id"], q)
    for n, sql_id in enumerate(sorted(statements, key=lambda i: statements[i]["sql"])):
        for mode, zone_ids in modes:
            run(
                f"exploration.fetch_sql_exploration_details__e2_stmt{n}[{mode}]",
                lambda i=sql_id, z=zone_ids: exploration.fetch_sql_exploration_details(i, z),
            )
    for mode, zone_ids in modes:
        run(
            f"exploration.fetch_sql_exploration_details__e2_missing[{mode}]",
            lambda z=zone_ids: exploration.fetch_sql_exploration_details(MISSING, z),
        )

    # -- fetch_exploration_related_nodes: both layers, zones, paging, missing (an unknown layer raises, so it has no capture).
    for label, node_id in (("film", film), ("rental", rental), ("customer", customer), ("missing", MISSING)):
        for mode, zone_ids in modes:
            run(
                f"exploration.fetch_exploration_related_nodes__e2_data_{label}[{mode}]",
                lambda n=node_id, z=zone_ids: exploration.fetch_exploration_related_nodes(n, "data", z),
            )
    for label, node_id in (
        ("film", term_film), ("customer", term_customer), ("payment", term_payment), ("missing", MISSING),
    ):
        for mode, zone_ids in modes:
            run(
                f"exploration.fetch_exploration_related_nodes__e2_semantic_{label}[{mode}]",
                lambda n=node_id, z=zone_ids: exploration.fetch_exploration_related_nodes(n, "semantic", z),
            )
    for layer, node_id in (("data", rental), ("semantic", term_customer)):
        for label, skip, limit in (("limit1", 0, 1), ("skip1", 1, None), ("skip1_limit1", 1, 1), ("skip99", 99, None)):
            run(
                f"exploration.fetch_exploration_related_nodes__e2_page_{layer}_{label}",
                lambda ly=layer, n=node_id, s=skip, m=limit: exploration.fetch_exploration_related_nodes(
                    n, ly, None, skip=s, limit=m
                ),
            )

    # -- fetch_semantic_link_path
    pairs = (
        ("film_customer", term_film, term_customer),
        ("customer_film", term_customer, term_film),
        ("customer_payment", term_customer, term_payment),
        ("film_track", term_film, term_track),
        ("film_film", term_film, term_film),
        ("missing", MISSING, term_film),
    )
    for label, a, b in pairs:
        for mode, zone_ids in modes:
            run(
                f"exploration.fetch_semantic_link_path__e2_{label}[{mode}]",
                lambda x=a, y=b, z=zone_ids: exploration.fetch_semantic_link_path(x, y, z),
            )
