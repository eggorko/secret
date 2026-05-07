from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import registry

if TYPE_CHECKING:
    from textual.app import App


@registry.register("help", "Show available commands")
def cmd_help(app: App, args: str) -> str:
    return registry.help_text()


@registry.register("clear", "Clear the output")
def cmd_clear(app: App, args: str) -> None:
    from textual.widgets import RichLog
    app.query_one("#output", RichLog).clear()


@registry.register("quit", "Quit the app")
def cmd_quit(app: App, args: str) -> None:
    app.exit()
