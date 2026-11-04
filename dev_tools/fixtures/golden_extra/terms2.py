# SPDX-License-Identifier: Apache-2.0
"""Extra golden captures for the terms2 lane: the remaining golden-covered term
reads (certification, scope, zones, embedding inputs, related terms, link paths)
across zone modes, other fixture entities, empty-input and not-found paths.
Every name carries the ``__t2_`` suffix so none duplicates another golden file."""
from __future__ import annotations


def capture(cap, ids) -> None:
    from auto_ontology.dal import terms, users

    run = cap.run
    zone, cross, retired = ids["zone_film"], ids["zone_cross"], ids["zone_retired"]
    film, rental, customer, track = (
        ids["table_film"], ids["table_rental"], ids["table_customer"], ids["table_track"],
    )
    named = (
        ("Film", ids["term_Film"]), ("Customer", ids["term_Customer"]),
        ("Payment", ids["term_Payment"]), ("Track", ids["term_Track"]),
    )
    t_film, t_cust, t_pay, t_track = (t for _, t in named)
    modes = (("admin", None), ("scoped", [zone]), ("cross", [cross]),
             ("retired", [retired]), ("none", []))
    data_ids = users.get_accessible_catalog_ids_for_zones([zone])

    # -- get_slim_term_by_id / get_term_record_for_table -----------------
    for n, t in named:
        run(f"terms.get_slim_term_by_id__t2_{n}", lambda t=t: terms.get_slim_term_by_id(t))
    run("terms.get_slim_term_by_id__t2_unknown", lambda: terms.get_slim_term_by_id("no-such-id"))
    run("terms.get_slim_term_by_id__t2_empty", lambda: terms.get_slim_term_by_id(""))
    for n, tb in (("film", film), ("rental", rental), ("customer", customer), ("track", track),
                  ("unknown", "no-such-id")):
        run(f"terms.get_term_record_for_table__t2_{n}",
            lambda tb=tb: terms.get_term_record_for_table(tb))

    # -- get_term_certification / term_is_in_scope ------------------------
    for n, t in named + (("unknown", "no-such-id"),):
        for m, zs in modes:
            run(f"terms.get_term_certification__t2_{n}[{m}]",
                lambda t=t, z=zs: terms.get_term_certification(t, zone_ids=z))
            run(f"terms.term_is_in_scope__t2_{n}[{m}]",
                lambda t=t, z=zs: terms.term_is_in_scope(t, zone_ids=z))
        run(f"terms.term_is_in_scope__t2_{n}_data_ids",
            lambda t=t: terms.term_is_in_scope(t, zone_ids=[zone], data_ids_by_zone=data_ids))
    run("terms.semantic_layer_calculated__t2_again", lambda: terms.semantic_layer_calculated())

    # -- fetch_terms_by_ids ------------------------------------------------
    run("terms.fetch_terms_by_ids__t2_empty", lambda: terms.fetch_terms_by_ids([]))
    run("terms.fetch_terms_by_ids__t2_unknown", lambda: terms.fetch_terms_by_ids(["no-such-id"]))
    run("terms.fetch_terms_by_ids__t2_all", lambda: terms.fetch_terms_by_ids([t for _, t in named]))
    run("terms.fetch_terms_by_ids__t2_dups", lambda: terms.fetch_terms_by_ids([t_film, t_film, t_cust]))
    run("terms.fetch_terms_by_ids__t2_mixed", lambda: terms.fetch_terms_by_ids([t_film, "no-such-id"]))
    for label, kw in (("skip1", {"skip": 1}), ("limit2", {"limit": 2}), ("skip1_limit2", {"skip": 1, "limit": 2}),
                      ("limit0", {"limit": 0}), ("skip_past_end", {"skip": 99})):
        run(f"terms.fetch_terms_by_ids__t2_page_{label}",
            lambda k=kw: terms.fetch_terms_by_ids([t for _, t in named], **k))

    # -- fetch_table_schema_map / fetch_terms_with_sqls ---------------------
    for db in ("pagila", "chinook", "no-such-db", ""):
        run(f"terms.fetch_table_schema_map__t2_{db or 'blank'}", lambda d=db: terms.fetch_table_schema_map(d))
    for db in (None, "pagila", "chinook", "no-such-db"):
        run(f"terms.fetch_terms_with_sqls__t2_db_{db}", lambda d=db: terms.fetch_terms_with_sqls(database_name=d))
    run("terms.fetch_terms_with_sqls__t2_other_source", lambda: terms.fetch_terms_with_sqls(source="manual"))

    # -- embedding inputs ----------------------------------------------------
    for n, t in named + (("unknown", "no-such-id"),):
        run(f"terms.fetch_term_and_column_attributes_for_embedding__t2_{n}",
            lambda t=t: terms.fetch_term_and_column_attributes_for_embedding(t))
    for n in ("film_title", "rental_customer_id", "customer_customer_id", "inventory_film_id"):
        run(f"terms.fetch_column_attribute_embedding_contexts_by_column_id__t2_{n}",
            lambda c=ids[f"col_{n}"]: terms.fetch_column_attribute_embedding_contexts_by_column_id(c))
        run(f"terms.find_column_attribute_by_column_id__t2_{n}",
            lambda c=ids[f"col_{n}"]: terms.find_column_attribute_by_column_id(c))
    run("terms.fetch_column_attribute_embedding_contexts_by_column_id__t2_unknown",
        lambda: terms.fetch_column_attribute_embedding_contexts_by_column_id("no-such-id"))
    run("terms.find_column_attribute_by_column_id__t2_unknown",
        lambda: terms.find_column_attribute_by_column_id("no-such-id"))

    # -- zones of terms -------------------------------------------------------
    for m, zs in modes:
        run(f"terms.fetch_term_zones_map__t2_term_ids[{m}]",
            lambda z=zs: terms.fetch_term_zones_map(zone_ids=z, term_ids=[t_film, t_track]))
        run(f"terms.fetch_term_zones_map__t2_one_term[{m}]",
            lambda z=zs: terms.fetch_term_zones_map(zone_ids=z, term_ids=[t_cust]))
        run(f"terms.fetch_term_zones_map__t2_unknown_term[{m}]",
            lambda z=zs: terms.fetch_term_zones_map(zone_ids=z, term_ids=["no-such-id"]))
        run(f"terms.fetch_term_zones_map__t2_all[{m}]", lambda z=zs: terms.fetch_term_zones_map(zone_ids=z))
    run("terms.fetch_term_zones_map__t2_empty_term_ids", lambda: terms.fetch_term_zones_map(term_ids=[]))
    run("terms.fetch_term_zones_map__t2_two_zones",
        lambda: terms.fetch_term_zones_map(zone_ids=[zone, cross]))

    # -- column attribute counts ---------------------------------------------
    for m, zs in modes:
        run(f"terms.fetch_column_attribute_counts__t2_term_ids[{m}]",
            lambda z=zs: terms.fetch_column_attribute_counts(zone_ids=z, term_ids=[t_film, t_cust]))
        for n, t in named + (("unknown", "no-such-id"),):
            run(f"terms.count_column_attributes_by_term_id__t2_{n}[{m}]",
                lambda t=t, z=zs: terms.count_column_attributes_by_term_id(t, zone_ids=z))
    run("terms.fetch_column_attribute_counts__t2_empty_term_ids",
        lambda: terms.fetch_column_attribute_counts(term_ids=[]))
    run("terms.fetch_column_attribute_counts__t2_unknown_term",
        lambda: terms.fetch_column_attribute_counts(term_ids=["no-such-id"]))
    run("terms.fetch_column_attribute_counts__t2_data_ids",
        lambda: terms.fetch_column_attribute_counts(zone_ids=[zone], data_ids_by_zone=data_ids))

    # -- related terms ---------------------------------------------------------
    for n, t in named + (("unknown", "no-such-id"),):
        for m, zs in modes:
            run(f"terms.fetch_related_term_ids__t2_{n}[{m}]",
                lambda t=t, z=zs: terms.fetch_related_term_ids(t, zone_ids=z))
            run(f"terms.fetch_related_terms__t2_{n}[{m}]",
                lambda t=t, z=zs: terms.fetch_related_terms(t, zone_ids=z))
    for label, kw in (("skip1", {"skip": 1}), ("limit1", {"limit": 1}), ("limit0", {"limit": 0}),
                      ("skip_past_end", {"skip": 99})):
        run(f"terms.fetch_related_terms__t2_page_{label}",
            lambda k=kw: terms.fetch_related_terms(t_cust, **k))
    run("terms.fetch_related_terms__t2_data_ids",
        lambda: terms.fetch_related_terms(t_film, zone_ids=[zone], data_ids_by_zone=data_ids))
    for m, zs in modes:
        run(f"terms.fetch_related_terms_counts__t2_term_ids[{m}]",
            lambda z=zs: terms.fetch_related_terms_counts(zone_ids=z, term_ids=[t_film, t_cust, t_track]))
        run(f"terms.fetch_related_terms_counts__t2_unknown[{m}]",
            lambda z=zs: terms.fetch_related_terms_counts(zone_ids=z, term_ids=["no-such-id"]))
    run("terms.fetch_related_terms_counts__t2_empty_term_ids",
        lambda: terms.fetch_related_terms_counts(term_ids=[]))
    run("terms.fetch_related_terms_counts__t2_data_ids",
        lambda: terms.fetch_related_terms_counts(zone_ids=[zone], data_ids_by_zone=data_ids))

    # -- link paths ----------------------------------------------------------------
    for (na, a) in named:
        for (nb, b) in named:
            run(f"terms.find_term_link_path__t2_{na}_{nb}", lambda a=a, b=b: terms.find_term_link_path(a, b))
    every = {row["name"]: row["id"] for row in terms.fetch_all_terms()}
    for na, a in every.items():
        for nb, b in every.items():
            if na in dict(named) or nb in dict(named):
                if na in dict(named) and nb in dict(named):
                    continue
            run(f"terms.find_term_link_path__t2_all_{na}_{nb}", lambda a=a, b=b: terms.find_term_link_path(a, b))
    run("terms.find_term_link_path__t2_unknown_a", lambda: terms.find_term_link_path("no-such-id", t_film))
    run("terms.find_term_link_path__t2_unknown_b", lambda: terms.find_term_link_path(t_film, "no-such-id"))
    run("terms.find_term_link_path__t2_empty_a", lambda: terms.find_term_link_path("", t_film))
    run("terms.find_term_link_path__t2_empty_b", lambda: terms.find_term_link_path(t_film, ""))
