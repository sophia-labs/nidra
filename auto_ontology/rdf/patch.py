# SPDX-License-Identifier: Apache-2.0
"""Install the RDF ports over the DAL when ``CATALOG_READ_BACKEND=rdf``.

Each module ``auto_ontology.dal_rdf.<name>`` ports functions of a target
module with identical signatures and return shapes: ``TARGET = "<dotted>"``
names it, else the file name does (``terms.py`` → ``auto_ontology.dal.terms``;
``terms__writes.py`` → the same target, several files for one module). The
names it lists in ``PORTS`` (or every public function it defines) replace the
originals on the target, so an existing ``from auto_ontology.dal.<name> import f``
anywhere binds the port.

Import order does not matter: ``install()`` registers an import hook that
applies a target's ports right after the target module finishes executing,
and applies them directly to targets already imported. It runs from
``auto_ontology/__init__.py``, before any target can be mid-import.

With ``AO_RDF_STRICT=1`` every read-shaped DAL function that has no port raises,
so a run cannot quietly fall back to Postgres.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import inspect
import logging
import os
import pkgutil
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_READ_PREFIXES = ("fetch_", "find_", "count_", "get_", "list_", "expand_", "column_", "custom_analysis_exists")
INSTALLED: dict[str, list[str]] = {}
_PORTS_BY_TARGET: dict[str, list[str]] = {}  # target module → port module names
_APPLIED: set[tuple[str, str, str]] = set()  # (target, port module, name)
_finder: "_PortFinder | None" = None


def enabled() -> bool:
    return os.environ.get("CATALOG_READ_BACKEND", "postgres").lower() == "rdf"


def _port_modules() -> list[tuple[str, str]]:
    """``(port module name, target module name)`` without importing the ports."""
    import auto_ontology.dal_rdf as ports_pkg

    out = []
    for info in pkgutil.iter_modules(ports_pkg.__path__):
        if info.name.startswith("_"):
            continue
        source = Path(ports_pkg.__path__[0], info.name + ".py").read_text()
        m = re.search(r'^TARGET\s*=\s*["\']([\w.]+)["\']', source, re.M)
        target = m.group(1) if m else f"auto_ontology.dal.{info.name.split('__')[0]}"
        out.append((f"auto_ontology.dal_rdf.{info.name}", target))
    return out


def _names(port) -> list[str]:
    declared = getattr(port, "PORTS", None)
    if declared:
        return list(declared)
    return [
        n for n, f in vars(port).items()
        if inspect.isfunction(f) and f.__module__ == port.__name__ and not n.startswith("_")
    ]


def _apply(target_name: str) -> None:
    """Put every available port function onto an initialized target."""
    target = sys.modules.get(target_name)
    if target is None or getattr(getattr(target, "__spec__", None), "_initializing", False):
        return
    for port_name in _PORTS_BY_TARGET.get(target_name, []):
        port = sys.modules.get(port_name)
        if port is None or getattr(getattr(port, "__spec__", None), "_initializing", False):
            continue
        for name in _names(port):
            if (target_name, port_name, name) in _APPLIED:
                continue
            if not hasattr(target, name):
                raise AttributeError(f"{port_name}.{name} ports a function {target_name} does not have")
            if not hasattr(port, name):
                continue
            setattr(target, name, getattr(port, name))
            _APPLIED.add((target_name, port_name, name))
            key = target_name.rsplit(".", 1)[1]
            if name not in INSTALLED.setdefault(key, []):
                INSTALLED[key].append(name)


class _PortFinder(importlib.abc.MetaPathFinder):
    """Applies ports to a target module the moment its import completes."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _PORTS_BY_TARGET:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                break
        else:
            return None
        real = spec.loader

        class _Loader(importlib.abc.Loader):
            def create_module(self, spec_):
                return real.create_module(spec_) if hasattr(real, "create_module") else None

            def exec_module(self, module):
                real.exec_module(module)
                _apply(fullname)

        spec.loader = _Loader()
        return spec


def install() -> dict[str, list[str]]:
    global _finder
    if not enabled():
        return INSTALLED
    if _finder is None:
        for port_name, target_name in _port_modules():
            _PORTS_BY_TARGET.setdefault(target_name, []).append(port_name)
        _finder = _PortFinder()
        sys.meta_path.insert(0, _finder)
        for port_name, _ in _port_modules():
            importlib.import_module(port_name)
        for target_name in list(_PORTS_BY_TARGET):
            importlib.import_module(target_name)
            _apply(target_name)
        if os.environ.get("AO_RDF_STRICT", "") == "1":
            _strict()
        logger.info("RDF catalog backend installed: %s", {k: len(v) for k, v in INSTALLED.items()})
    else:
        for target_name in list(_PORTS_BY_TARGET):
            _apply(target_name)
    return INSTALLED


def _unported(module: str, name: str):
    def raise_unported(*args, **kwargs):
        raise NotImplementedError(f"auto_ontology.dal.{module}.{name} has no RDF port (AO_RDF_STRICT=1)")

    raise_unported.__name__ = name
    return raise_unported


def _strict() -> None:
    import auto_ontology.dal as dal_pkg

    for info in pkgutil.iter_modules(dal_pkg.__path__):
        if info.name.startswith("_") or info.name in ("session", "schema", "tests"):
            continue
        target = importlib.import_module(f"auto_ontology.dal.{info.name}")
        ported = set(INSTALLED.get(info.name, []))
        for name, f in list(vars(target).items()):
            if (
                inspect.isfunction(f)
                and f.__module__ == target.__name__
                and name.startswith(_READ_PREFIXES)
                and name not in ported
            ):
                setattr(target, name, _unported(info.name, name))
