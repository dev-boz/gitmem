from __future__ import annotations

import importlib
import inspect


PUBLIC_API_MODULES = ("umx.config", "umx.models", "umx.mcp_server")


def _iter_public_class_members(cls: type[object]):
    for name, value in vars(cls).items():
        if name.startswith("_"):
            continue
        if isinstance(value, property):
            yield f"{cls.__name__}.{name}", value.fget
            continue
        if isinstance(value, classmethod):
            yield f"{cls.__name__}.{name}", value.__func__
            continue
        if isinstance(value, staticmethod):
            yield f"{cls.__name__}.{name}", value.__func__
            continue
        if inspect.isfunction(value):
            yield f"{cls.__name__}.{name}", value


def test_public_api_symbols_have_docstrings() -> None:
    missing: list[str] = []

    for module_name in PUBLIC_API_MODULES:
        module = importlib.import_module(module_name)
        if not inspect.getdoc(module):
            missing.append(f"{module_name} (module)")

        exported = getattr(module, "__all__", ())
        assert exported, f"{module_name} must define __all__ for the documented public API"

        for name in exported:
            assert not name.startswith("_"), f"{module_name} exports private name {name}"
            obj = getattr(module, name)
            if not inspect.getdoc(obj):
                missing.append(f"{module_name}.{name}")
            if inspect.isclass(obj):
                for member_name, member in _iter_public_class_members(obj):
                    if member is not None and not inspect.getdoc(member):
                        missing.append(f"{module_name}.{member_name}")

    assert not missing, "Missing docstrings:\n" + "\n".join(sorted(missing))
