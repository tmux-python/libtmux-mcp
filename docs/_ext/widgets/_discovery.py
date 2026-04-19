"""Autodiscover widget classes from sibling modules in this package."""

from __future__ import annotations

import importlib
import pkgutil

from ._base import BaseWidget


def discover() -> dict[str, type[BaseWidget]]:
    """Import every non-underscore submodule; collect ``BaseWidget`` subclasses.

    Adding a new widget means: drop ``mywidget.py`` next to ``install.py`` with a
    ``MyWidget(BaseWidget)`` that sets ``name = "mywidget"`` — the discovery sweep
    at ``setup()`` time registers it automatically.
    """
    from . import __name__ as pkg_name
    from . import __path__ as pkg_path

    registry: dict[str, type[BaseWidget]] = {}
    for info in pkgutil.iter_modules(pkg_path):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{pkg_name}.{info.name}")
        for obj in vars(module).values():
            if not _is_widget_class(obj):
                continue
            existing = registry.get(obj.name)
            if existing is not None and existing is not obj:
                raise RuntimeError(
                    f"Duplicate widget name {obj.name!r}: "
                    f"{existing.__module__} vs {obj.__module__}"
                )
            registry[obj.name] = obj
    return registry


def _is_widget_class(obj: object) -> bool:
    return (
        isinstance(obj, type)
        and issubclass(obj, BaseWidget)
        and obj is not BaseWidget
        and getattr(obj, "name", None) is not None
    )
