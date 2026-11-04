# SPDX-License-Identifier: Apache-2.0
"""``dal.exploration`` re-exports ``dal.zones.fetch_table_zones_map`` by name
(``from ... import``), so installing the port on ``dal.zones`` alone leaves the
re-export bound to the SQL original. This file installs the same port there."""
from __future__ import annotations

from auto_ontology.dal_rdf.zones import fetch_table_zones_map  # noqa: F401

PORTS = ["fetch_table_zones_map"]
