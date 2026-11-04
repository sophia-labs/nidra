# SPDX-License-Identifier: Apache-2.0
"""Extra golden captures for the terms lane: search, paging, zone modes, filters,
empty-input and not-found paths of the six ported term reads."""
from __future__ import annotations


def capture(cap, ids) -> None:
    from auto_ontology.dal import terms, users

    run = cap.run
    zone = ids["zone_film"]
    film, rental, customer, track = (
        ids["table_film"], ids["table_rental"], ids["table_customer"], ids["table_track"],
    )
    t_film, t_cust, t_pay, t_track = (
        ids["term_Film"], ids["term_Customer"], ids["term_Payment"], ids["term_Track"],
    )
    modes = (("admin", None), ("scoped", [zone]), ("none", []))
    data_ids = users.get_accessible_catalog_ids_for_zones([zone])

    # -- count_terms / fetch_all_terms: search, paging, zone modes ---------
    for label, search in (
        ("search_ci", "FILM"),
        ("search_substr", "ust"),
        ("search_padded", "  film "),
        ("search_percent", "%"),
        ("search_underscore", "_"),
        ("search_nomatch", "zzzz"),
        ("search_empty", ""),
        ("search_blank", "   "),
    ):
        for mlabel, zs in modes:
            run(f"terms.count_terms__{label}[{mlabel}]",
                lambda s=search, z=zs: terms.count_terms(zone_ids=z, search=s))
            run(f"terms.fetch_all_terms__{label}[{mlabel}]",
                lambda s=search, z=zs: terms.fetch_all_terms(zone_ids=z, search=s))
    run("terms.count_terms__data_ids",
        lambda: terms.count_terms(zone_ids=[zone], data_ids_by_zone=data_ids))
    run("terms.fetch_all_terms__data_ids",
        lambda: terms.fetch_all_terms(zone_ids=[zone], data_ids_by_zone=data_ids))
    for label, kw in (
        ("skip1", {"skip": 1}),
        ("limit2", {"limit": 2}),
        ("skip1_limit2", {"skip": 1, "limit": 2}),
        ("skip2_limit1", {"skip": 2, "limit": 1}),
        ("limit0", {"limit": 0}),
        ("skip_past_end", {"skip": 100}),
        ("search_limit1", {"search": "e", "limit": 1}),
        ("search_skip1", {"search": "e", "skip": 1}),
    ):
        run(f"terms.fetch_all_terms__page_{label}", lambda k=kw: terms.fetch_all_terms(**k))
        run(f"terms.fetch_all_terms__page_{label}[scoped]",
            lambda k=kw: terms.fetch_all_terms(zone_ids=[zone], **k))

    # -- get_full_term_by_id ----------------------------------------------
    for tname, tid in (("Film", t_film), ("Customer", t_cust), ("Payment", t_pay), ("Track", t_track)):
        for mlabel, zs in modes:
            run(f"terms.get_full_term_by_id__{tname}[{mlabel}]",
                lambda t=tid, z=zs: terms.get_full_term_by_id(t, zone_ids=z))
    run("terms.get_full_term_by_id__unknown[scoped]",
        lambda: terms.get_full_term_by_id("no-such-id", zone_ids=[zone]))
    run("terms.get_full_term_by_id__unknown[none]",
        lambda: terms.get_full_term_by_id("no-such-id", zone_ids=[]))

    # -- fetch_column_attributes_by_term_id -------------------------------
    for tname, tid in (("Film", t_film), ("Customer", t_cust), ("Payment", t_pay), ("Track", t_track)):
        for mlabel, zs in modes:
            run(f"terms.fetch_column_attributes_by_term_id__{tname}[{mlabel}]",
                lambda t=tid, z=zs: terms.fetch_column_attributes_by_term_id(t, zone_ids=z))
    for label, kw in (
        ("skip1", {"skip": 1}),
        ("limit1", {"limit": 1}),
        ("skip1_limit1", {"skip": 1, "limit": 1}),
        ("limit0", {"limit": 0}),
        ("skip_past_end", {"skip": 50}),
    ):
        run(f"terms.fetch_column_attributes_by_term_id__page_{label}",
            lambda k=kw: terms.fetch_column_attributes_by_term_id(t_film, **k))
        run(f"terms.fetch_column_attributes_by_term_id__page_{label}[Customer]",
            lambda k=kw: terms.fetch_column_attributes_by_term_id(t_cust, **k))
    run("terms.fetch_column_attributes_by_term_id__unknown",
        lambda: terms.fetch_column_attributes_by_term_id("no-such-id"))
    run("terms.fetch_column_attributes_by_term_id__unknown[scoped]",
        lambda: terms.fetch_column_attributes_by_term_id("no-such-id", zone_ids=[zone]))

    # -- fetch_terms_and_attributes_for_table ------------------------------
    for tname, tid in (("film", film), ("rental", rental), ("customer", customer), ("track", track)):
        run(f"terms.fetch_terms_and_attributes_for_table__{tname}",
            lambda t=tid: terms.fetch_terms_and_attributes_for_table(t))
    run("terms.fetch_terms_and_attributes_for_table__unknown",
        lambda: terms.fetch_terms_and_attributes_for_table("no-such-id"))

    # -- fetch_term_table_pairs --------------------------------------------
    for mlabel, zs in modes:
        run(f"terms.fetch_term_table_pairs__term_ids[{mlabel}]",
            lambda z=zs: terms.fetch_term_table_pairs(zone_ids=z, term_ids=[t_film, t_cust]))
        run(f"terms.fetch_term_table_pairs__table_ids[{mlabel}]",
            lambda z=zs: terms.fetch_term_table_pairs(zone_ids=z, table_ids=[film, rental]))
        run(f"terms.fetch_term_table_pairs__both[{mlabel}]",
            lambda z=zs: terms.fetch_term_table_pairs(zone_ids=z, term_ids=[t_film], table_ids=[film]))
        run(f"terms.fetch_term_table_pairs__db_pagila[{mlabel}]",
            lambda z=zs: terms.fetch_term_table_pairs(zone_ids=z, database_name="pagila"))
        run(f"terms.fetch_term_table_pairs__db_chinook[{mlabel}]",
            lambda z=zs: terms.fetch_term_table_pairs(zone_ids=z, database_name="chinook"))
    run("terms.fetch_term_table_pairs__db_unknown",
        lambda: terms.fetch_term_table_pairs(database_name="no-such-db"))
    run("terms.fetch_term_table_pairs__empty_term_ids",
        lambda: terms.fetch_term_table_pairs(term_ids=[]))
    run("terms.fetch_term_table_pairs__empty_table_ids",
        lambda: terms.fetch_term_table_pairs(table_ids=[]))
    run("terms.fetch_term_table_pairs__unknown_term",
        lambda: terms.fetch_term_table_pairs(term_ids=["no-such-id"]))
    run("terms.fetch_term_table_pairs__unknown_table",
        lambda: terms.fetch_term_table_pairs(table_ids=["no-such-id"]))
    run("terms.fetch_term_table_pairs__db_term_table",
        lambda: terms.fetch_term_table_pairs(database_name="pagila", term_ids=[t_film, t_track], table_ids=[film, track, rental]))
    run("terms.fetch_term_table_pairs__data_ids",
        lambda: terms.fetch_term_table_pairs(zone_ids=[zone], data_ids_by_zone=data_ids))
