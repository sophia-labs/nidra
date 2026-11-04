# SPDX-License-Identifier: Apache-2.0
"""Captures for the gaps lane: ``dal.owners`` reads, ``attributes.fetch_semantic_fk_join_groups``,
``dal.search`` (list, count, rule id lists) and ``catalog.store.queries.get_candidate_sql_ids``.
Names carry ``__gp_``."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

MISSING = "00000000-0000-0000-0000-00000000dead"


def _ids(matched: dict[str, Any]) -> dict[str, list[str]]:
    """``matching_id_selects`` answers Selects on Postgres and id lists on the graph:
    both are read down to sorted ids."""
    out = {}
    for label, m in matched.items():
        if hasattr(m, "compile"):
            from auto_ontology.dal.session import store

            out[label] = sorted(r["id"] for r in store().query_read(m))
        else:
            out[label] = sorted(m)
    return out


def capture(cap, ids) -> None:
    from auto_ontology.catalog.constants import Labels
    from auto_ontology.dal import attributes, owners, search
    from auto_ontology.dal import schema as s

    run = cap.run
    film, term_film, term_cust = ids["table_film"], ids["term_Film"], ids["term_Customer"]

    # -- owners ----------------------------------------------------------
    subjects = {
        "database": ids["db_pagila"], "schema": ids["schema_public"], "table": film,
        "term": term_film, "column": ids["col_film_title"], "attr": ids["attr_film_id"],
        "sqlattr": ids["sqlattr_revenue"], "analysis": ids["analysis_top_films"],
        "zone": ids["zone_film"], "unknown": MISSING, "empty": "",
    }
    for n, e in subjects.items():
        run(f"owners.edit_subject__gp_{n}", lambda e=e: (lambda r: None if r is None else asdict(r))(owners.edit_subject(e)))
    for n, table in (("database", s.catalog_database), ("schema", s.catalog_schema), ("table", s.catalog_table), ("term", s.term)):
        for m, e in (("known", subjects[n]), ("unknown", MISSING)):
            run(f"owners.current_owner__gp_{n}_{m}", lambda t=table, e=e: owners.current_owner(t, e))

    # -- semantic fk join groups ----------------------------------------
    for db in ("pagila", "chinook", "no-such-db", ""):
        run(f"attributes.fetch_semantic_fk_join_groups__gp_{db or 'blank'}",
            lambda d=db: attributes.fetch_semantic_fk_join_groups(d))

    # -- search ------------------------------------------------------------
    every = set(search.SEARCH_OBJECT_TYPES)
    subsets = {
        "all": every, "table": {Labels.TABLE}, "view": {search.SEARCH_TYPE_VIEW},
        "tableview": {Labels.TABLE, search.SEARCH_TYPE_VIEW}, "column": {Labels.COLUMN},
        "term": {"Term"}, "attrs": {"ColumnAttribute", "SqlAttribute"}, "tag": {search.LABEL_TAG},
        "dbschema": {Labels.DB, Labels.SCHEMA}, "none": set(),
    }
    terms_ = ("film", "customer id", "amount", "pagila", "public", "rental", "revenue", "title", "zzqqxx", "", "  ", "created-at", "a")
    for term in terms_:
        toks = search.search_tokens(term)
        syn = search.synonym_word_tokens(term)
        for desc in (True, False):
            tag = f"{term.replace(' ', '_') or 'empty'}_{'desc' if desc else 'name'}"
            run(f"search.fetch_global_search__gp_{tag}", lambda t=toks, d=desc, sy=syn: search.fetch_global_search(
                t, every, include_description=d, synonym_tokens=sy))
            run(f"search.count_global_search__gp_{tag}", lambda t=toks, d=desc, sy=syn: search.count_global_search(
                t, every, include_description=d, synonym_tokens=sy))
            run(f"search.matching_id_selects__gp_{tag}", lambda t=toks, d=desc, sy=syn: _ids(search.matching_id_selects(
                t, every, include_description=d, synonym_tokens=sy)))
    for name, types in subsets.items():
        for term in ("film", "id"):
            toks = search.search_tokens(term)
            run(f"search.fetch_global_search__gp_types_{name}_{term}", lambda t=toks, ty=types: search.fetch_global_search(
                t, ty, include_description=True, synonym_tokens=search.synonym_word_tokens("movie")))
            run(f"search.count_global_search__gp_types_{name}_{term}", lambda t=toks, ty=types: search.count_global_search(
                t, ty, include_description=True, synonym_tokens=search.synonym_word_tokens("movie")))
            run(f"search.matching_id_selects__gp_types_{name}_{term}", lambda t=toks, ty=types: _ids(search.matching_id_selects(
                t, ty, include_description=True, synonym_tokens=search.synonym_word_tokens("movie"))))
    # synonyms: whole words only ("movie" is a synonym of Film; "movi" is not)
    for syn_term in ("movie", "movi", "picture title", "title"):
        toks = search.search_tokens("zzqqxx")
        sy = search.synonym_word_tokens(syn_term)
        run(f"search.fetch_global_search__gp_syn_{syn_term.replace(' ', '_')}", lambda t=toks, sy=sy: search.fetch_global_search(
            t, every, include_description=True, synonym_tokens=sy, synonym_limit=5))
        run(f"search.count_global_search__gp_syn_{syn_term.replace(' ', '_')}", lambda t=toks, sy=sy: search.count_global_search(
            t, every, include_description=True, synonym_tokens=sy))
        run(f"search.matching_id_selects__gp_syn_{syn_term.replace(' ', '_')}", lambda t=toks, sy=sy: _ids(search.matching_id_selects(
            t, every, include_description=True, synonym_tokens=sy)))
    # caps: cut only at rank boundaries (ties are ordered by id, and ids differ per store)
    toks = search.search_tokens("film")
    for lim in (0, 2, 3, 9, 100):
        run(f"search.fetch_global_search__gp_limit_{lim}", lambda l=lim: search.fetch_global_search(
            toks, every, include_description=True, limit=l))
    # facets
    scope = {"db": [ids["db_pagila"]], "schema": [ids["schema_public"]], "both": [ids["db_pagila"], ids["schema_public"]], "unknown": [MISSING]}
    for n, d in scope.items():
        for term in ("film", "id"):
            t2 = search.search_tokens(term)
            run(f"search.fetch_global_search__gp_data_{n}_{term}", lambda t=t2, d=d: search.fetch_global_search(
                t, every, include_description=True, data_ids=d))
            run(f"search.count_global_search__gp_data_{n}_{term}", lambda t=t2, d=d: search.count_global_search(
                t, every, include_description=True, data_ids=d))
            run(f"search.matching_id_selects__gp_data_{n}_{term}", lambda t=t2, d=d: _ids(search.matching_id_selects(
                t, every, include_description=True, data_ids=d)))
    for n, kw in (("missing_tag", {"tag_ids": [MISSING]}), ("untagged", {"include_untagged": True}),
                  ("both", {"tag_ids": [MISSING], "include_untagged": True})):
        t2 = search.search_tokens("film")
        run(f"search.fetch_global_search__gp_tags_{n}", lambda k=kw, t=t2: search.fetch_global_search(
            t, every, include_description=True, synonym_tokens=["movie"], **k))
        run(f"search.count_global_search__gp_tags_{n}", lambda k=kw, t=t2: search.count_global_search(
            t, every, include_description=True, synonym_tokens=["movie"], **k))
        run(f"search.matching_id_selects__gp_tags_{n}", lambda k=kw, t=t2: _ids(search.matching_id_selects(
            t, every, include_description=True, synonym_tokens=["movie"], **k)))

    # -- catalog.store.queries.get_candidate_sql_ids -------------------------
    _candidates(cap, ids)


def _candidates(cap, ids) -> None:
    """Each stored statement's own shape against the whole stored set, then wrong shapes."""
    from auto_ontology.catalog.store import queries as sq

    run = cap.run
    frame = sq.load_sqls_to_tables()
    ordered = frame.sort_values("sql_full_query").reset_index(drop=True)
    for i, row in ordered.iterrows():
        def own(row=row):
            got = sq.get_candidate_sql_ids(
                list(row["tbls"]), list(row["cols"]), int(row["nodes_count"]), frame,
                int(row["join_count"]), int(row["union_count"]))
            return sorted(got["sql_id"].tolist())

        def other_nodes(row=row):
            return sorted(sq.get_candidate_sql_ids(
                list(row["tbls"]), list(row["cols"]), int(row["nodes_count"]) + 1, frame,
                int(row["join_count"]), int(row["union_count"]))["sql_id"].tolist())

        def fewer_cols(row=row):
            return sorted(sq.get_candidate_sql_ids(
                list(row["tbls"]), list(row["cols"])[:-1], int(row["nodes_count"]), frame,
                int(row["join_count"]), int(row["union_count"]))["sql_id"].tolist())

        run(f"queries.get_candidate_sql_ids__gp_own_{i}", own)
        run(f"queries.get_candidate_sql_ids__gp_nodes_plus1_{i}", other_nodes)
        run(f"queries.get_candidate_sql_ids__gp_fewer_cols_{i}", fewer_cols)
    run("queries.get_candidate_sql_ids__gp_none", lambda: sq.get_candidate_sql_ids([], [], 0, frame))
    run("queries.get_candidate_sql_ids__gp_shape", lambda: [len(frame), sorted(frame.columns.tolist())])
