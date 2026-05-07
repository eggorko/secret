from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from textual.app import App


@dataclass
class Command:
    name: str
    description: str
    handler: Callable[[App, str], str | None]


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, name: str, description: str) -> Callable:
        def decorator(fn: Callable[[App, str], str | None]) -> Callable:
            self._commands[name] = Command(name, description, fn)
            return fn
        return decorator

    def execute(self, app: App, raw: str) -> str | None:
        parts = raw[1:].split(maxsplit=1)
        name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        cmd = self._commands.get(name)
        if cmd is None:
            return f"Unknown command: /{name}  (type /help for a list)"
        return cmd.handler(app, args)

    def help_text(self) -> str:
        return "\n".join(
            f"/{cmd.name}  —  {cmd.description}"
            for cmd in self._commands.values()
        )


registry = CommandRegistry()
