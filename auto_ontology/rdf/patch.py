# SPDX-License-Identifier: Apache-2.0
"""Install the RDF ports over the DAL when ``CATALOG_READ_BACKEND=rdf``.

Each module ``auto_ontology.dal_rdf.<name>`` ports functions of
``auto_ontology.dal.<name>`` with identical signatures and return shapes. The
names it lists in ``PORTS`` (or every public function it defines, when it has no
``PORTS``) replace the originals on the DAL module, so an existing
``from auto_ontology.dal.<name> import f`` in retrieval binds the port.

With ``AO_RDF_STRICT=1`` every read-shaped DAL function that has no port raises,
so a run cannot quietly fall back to Postgres.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import os
import pkgutil

logger = logging.getLogger(__name__)

_READ_PREFIXES = ("fetch_", "find_", "count_", "get_", "list_", "expand_", "column_", "custom_analysis_exists")
INSTALLED: dict[str, list[str]] = {}


def enabled() -> bool:
    return os.environ.get("CATALOG_READ_BACKEND", "postgres").lower() == "rdf"


def _unported(module: str, name: str):
    def raise_unported(*args, **kwargs):
        raise NotImplementedError(f"auto_ontology.dal.{module}.{name} has no RDF port (AO_RDF_STRICT=1)")

    raise_unported.__name__ = name
    return raise_unported


def install() -> dict[str, list[str]]:
    if not enabled() or INSTALLED:
        return INSTALLED
    import auto_ontology.dal_rdf as ports_pkg

    strict = os.environ.get("AO_RDF_STRICT", "") == "1"
    ported_by_module: dict[str, set[str]] = {}
    for info in pkgutil.iter_modules(ports_pkg.__path__):
        if info.name.startswith("_"):
            continue
        port = importlib.import_module(f"auto_ontology.dal_rdf.{info.name}")
        target = importlib.import_module(f"auto_ontology.dal.{info.name}")
        names = getattr(port, "PORTS", None) or [
            n for n, f in vars(port).items()
            if inspect.isfunction(f) and f.__module__ == port.__name__ and not n.startswith("_")
        ]
        for name in names:
            if not hasattr(target, name):
                raise AttributeError(f"dal_rdf.{info.name}.{name} ports a function the DAL does not have")
            setattr(target, name, getattr(port, name))
        ported_by_module[info.name] = set(names)
        INSTALLED[info.name] = sorted(names)
    if strict:
        import auto_ontology.dal as dal_pkg

        for info in pkgutil.iter_modules(dal_pkg.__path__):
            if info.name.startswith("_") or info.name in ("session", "schema", "tests"):
                continue
            target = importlib.import_module(f"auto_ontology.dal.{info.name}")
            for name, f in list(vars(target).items()):
                if (
                    inspect.isfunction(f)
                    and f.__module__ == target.__name__
                    and name.startswith(_READ_PREFIXES)
                    and name not in ported_by_module.get(info.name, set())
                ):
                    setattr(target, name, _unported(info.name, name))
    logger.info("RDF catalog backend installed: %s", {k: len(v) for k, v in INSTALLED.items()})
    return INSTALLED
