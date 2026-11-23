"""Lazy command registration for thin interface adapters."""
from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Callable, Dict, Optional


@dataclass
class CommandSpec:
    name: str
    module: str
    attribute: str
    configure: Callable
    _handler: Optional[Callable] = None

    def resolve(self):
        if self._handler is None:
            self._handler = getattr(importlib.import_module(self.module), self.attribute)
        return self._handler


class CommandRegistry:
    def __init__(self):
        self.commands: Dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec):
        if spec.name in self.commands:
            raise ValueError("重复命令：%s" % spec.name)
        self.commands[spec.name] = spec

    def resolve(self, name: str):
        return self.commands[name].resolve()

    def attach(self, subparsers):
        for name, spec in self.commands.items():
            parser = subparsers.add_parser(name)
            spec.configure(parser)
            parser.set_defaults(fn=lambda args, _name=name: self.resolve(_name)(args))
