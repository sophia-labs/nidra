# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``update_diff_from_existing_schema`` (the re-ingest diff), gaps lane.

The diff itself (``catalog.diff``) is shared pandas code and stays the
algorithm. What breaks on this backend is the values it hands the store: a
freshly parsed schema carries numpy scalars (``numpy.int64`` ordinal
positions, ``numpy.bool_`` flags, ``pd.NA``), which psycopg adapts and the
RDF literal serializer does not recognise, so they are written as the plain
strings ``"3"`` / ``"True"``. A re-ingest would then retype a column's
``ordinal_position`` into a string and the column would drop out of every
ordered read. The port turns the new schema's frames into plain Python values
and runs the shared diff.
"""
from __future__ import annotations

import numbers

import pandas as pd

from auto_ontology.catalog.diff import update_diff_from_existing_schema as _shared_diff

TARGET = "auto_ontology.catalog.store.db"
PORTS = ["update_diff_from_existing_schema"]


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)) and getattr(value, "shape", None) == ():
        return value.item()  # numpy scalar
    try:
        if value is pd.NA or (isinstance(value, numbers.Real) and pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _scrub(frame: pd.DataFrame | None) -> None:
    if frame is None:
        return
    for column in frame.columns:
        if str(frame[column].dtype) == "category":
            continue
        frame[column] = pd.Series([_plain(v) for v in frame[column]], index=frame.index, dtype=object)


def update_diff_from_existing_schema(new_schema, latest_timestamp):
    _scrub(new_schema.tables_df)
    _scrub(new_schema.columns_df)
    return _shared_diff(new_schema, latest_timestamp)
