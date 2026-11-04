# SPDX-License-Identifier: Apache-2.0
"""``catalog.store.queries.get_candidate_sql_ids`` on the RDF backend.

It is pure pandas over the frame ``load_sqls_to_tables`` returns (the shared
implementation lives in ``catalog.query_stats``), so the port is the same
function; it is listed here so the statement-matching path of
``catalog.sql_parse`` is explicitly on this backend, and covered by a test
against the graph's own ``load_sqls_to_tables``."""
from __future__ import annotations

import pandas as pd

from auto_ontology.catalog.query_stats import get_candidate_sql_ids as _shared

TARGET = "auto_ontology.catalog.store.queries"
PORTS = ["get_candidate_sql_ids"]


def get_candidate_sql_ids(
    tbl_ids: list[str],
    col_ids: list[str],
    nodes_count: int,
    sqls_tbls_df: pd.DataFrame,
    join_count: int = 0,
    union_count: int = 0,
) -> pd.DataFrame:
    return _shared(tbl_ids, col_ids, nodes_count, sqls_tbls_df, join_count, union_count)
