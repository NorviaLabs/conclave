"""Trusted host extensions. Browser definitions never select Python modules."""
from dataclasses import dataclass
import importlib
import re

from conclave import Capability, Tool


def load_provider_factory(value):
    """Resolve a trusted deployment setting, never browser-supplied code."""
    if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*", value):
        raise ValueError("STUDIO_PROVIDER_FACTORY must be module:callable")
    module, attribute = value.split(':')
    factory = getattr(importlib.import_module(module), attribute)
    if not callable(factory):
        raise ValueError("STUDIO_PROVIDER_FACTORY must resolve to a callable")
    return factory


@dataclass
class ConnectorAdapter:
    id: str
    name: str
    group: str
    tools: tuple[str, ...]
    check: object
    description: str = "Host-configured connection"
    mode: str = "read"

    async def describe(self, registry):
        import inspect
        result = self.check()
        if inspect.isawaitable(result):
            result = await result
        # A failed check is deliberately generic; backend exceptions can contain secrets.
        if type(result) is not bool:
            raise ValueError("Connector checks must return a boolean")
        available = result and all(registry.tool(name) is not None for name in self.tools)
        return {"id": self.id, "name": self.name, "group": self.group, "category": "Host connections",
                "description": self.description, "default_mode": self.mode,
                "status": "connected" if available else "unavailable", "available": available,
                "tools": list(self.tools), "origin": "host"}


def load_extensions(names, tools):
    adapters = []
    for name in filter(None, (item.strip() for item in names.split(','))):
        if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*", name):
            raise ValueError("STUDIO_EXTENSIONS must contain Python module names")
        result = importlib.import_module(name).register(tools)
        if not isinstance(result, (list, tuple)) or any(not isinstance(adapter, ConnectorAdapter) for adapter in result):
            raise ValueError("Host extensions must return connector adapters")
        adapters.extend(result)
    for tool in tools.tools.values():
        if not isinstance(tool, Tool) or not tool.capabilities <= frozenset(Capability):
            raise ValueError("Host tools must declare supported capabilities or no capabilities")
    seen = set()
    for adapter in adapters:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", adapter.id) or adapter.id in seen:
            raise ValueError("Host connector identifiers must be unique and valid")
        if adapter.mode not in {"read", "draft", "approved", "model"}:
            raise ValueError("Unsupported host connector mode")
        seen.add(adapter.id)
    return adapters
