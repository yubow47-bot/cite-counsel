"""The plugin interface. The harness knows nothing about law; plugins do.

A plugin is a Python object named ``PLUGIN`` in a package under ``plugins/``,
or one advertised by an installed distribution under the ``citecounsel.plugins``
entry-point group. It declares:

- ``tools``: what the model may call once it has loaded the plugin;
- ``actions``: deterministic operations the plugin's own UI triggers directly
  (a button click), which never go through the model;
- ``ui``: an optional directory with ``ui.js`` / ``ui.css`` that renders the
  plugin's own result blocks and, if it wants one, a panel;
- ``settings``: options shown in the settings bar, owned by the plugin;
- ``requires``: other plugins whose public ``api`` it uses;
- ``reply_guard``: an optional, plugin-specific check on the model's prose
  once the plugin is loaded. Unsourced facts are already hidden paragraph by
  paragraph by the harness (``harness.grounding``) using every plugin's
  ``fact_patterns``; a guard is only for something stricter than that.

Plugins are trusted, installed code. Discovery happens at startup; nothing is
hot-reloaded.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


class UserText(str):
    """A tool parameter that must be copied from the user's own words (§3.4).

    The harness checks it against the session's user messages before the
    handler runs and substitutes the exact slice it found, so the plugin
    never sees the model's paraphrase of the user.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.no_info_after_validator_function(
            cls, core_schema.str_schema(max_length=2000))


@dataclass
class Result:
    """What a tool or action hands back.

    ``content`` goes to the model (keep it small); ``blocks`` go to the user,
    rendered by the plugin's UI. ``final`` says the blocks already answer the
    user, so the turn ends without another model call.
    """

    content: Any = None
    blocks: list[dict] = field(default_factory=list)
    final: bool = False


@dataclass
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Callable[..., Result]  # handler(ctx, params_instance) -> Result


@dataclass
class Setting:
    name: str
    label: str
    kind: str = "text"            # "text" | "secret" | "choice" | "bool"
    choices: tuple[str, ...] = ()
    default: Any = ""
    help: str = ""
    labels: dict = field(default_factory=dict)   # choice value -> label shown


@dataclass
class Plugin:
    name: str
    title: str
    description: str               # one line; this is all the model sees before loading
    instructions: str = ""         # added to the system prompt once loaded
    tools: list[Tool] = field(default_factory=list)
    actions: dict[str, Callable[..., Result]] = field(default_factory=dict)  # name -> fn(ctx, payload)
    ui: Path | None = None
    settings: list[Setting] = field(default_factory=list)
    requires: tuple[str, ...] = ()
    default_enabled: bool = False
    api: Any = None
    reply_guard: Callable[[str, Any], str] | None = None
    category: str = "function"     # "source" | "extract" | "function": what origins it may produce
    fact_patterns: tuple = ()      # regexes for fact-shaped strings this domain uses (grounding)

    def validate(self) -> None:
        if not NAME_RE.match(self.name):
            raise ValueError(f"invalid plugin name {self.name!r}")
        if self.category not in {"source", "extract", "function"}:
            raise ValueError(f"plugin {self.name}: unknown category {self.category!r}")
        if not self.description.strip() or len(self.description) > 300:
            raise ValueError(f"plugin {self.name}: description must be one short line")
        seen = set()
        for tool in self.tools:
            if not NAME_RE.match(tool.name) or tool.name in seen:
                raise ValueError(f"plugin {self.name}: invalid or duplicate tool {tool.name!r}")
            if not (isinstance(tool.params, type) and issubclass(tool.params, BaseModel)):
                raise ValueError(f"plugin {self.name}: tool {tool.name} needs a pydantic params model")
            seen.add(tool.name)
        if self.ui is not None and not (Path(self.ui) / "ui.js").is_file():
            raise ValueError(f"plugin {self.name}: ui directory has no ui.js")


def discover(root: Path | None = None) -> dict[str, Plugin]:
    """Built-in plugins under ``plugins/`` plus installed entry points."""
    root = root or Path(__file__).resolve().parent.parent / "plugins"
    found: dict[str, Plugin] = {}

    def add(plugin):
        if not isinstance(plugin, Plugin):
            raise ValueError("PLUGIN must be a harness.plugin.Plugin")
        plugin.validate()
        if plugin.name in found:
            raise ValueError(f"duplicate plugin {plugin.name}")
        found[plugin.name] = plugin

    for package in sorted(p for p in root.iterdir() if (p / "__init__.py").is_file()):
        add(importlib.import_module(f"plugins.{package.name}").PLUGIN)
    for point in metadata.entry_points(group="citecounsel.plugins"):
        add(point.load())
    for plugin in found.values():
        missing = [name for name in plugin.requires if name not in found]
        if missing:
            raise ValueError(f"plugin {plugin.name} requires missing plugins: {missing}")
    return found
