import argparse

from textual.app import App, ComposeResult
from textual.widgets import Header, Input, Label, RichLog, Static
from textual.screen import Screen
from textual.containers import Horizontal

from secret.commands import registry


class MainScreen(Screen):
    BINDINGS = [("ctrl+q", "quit", "Quit")]
    CSS_PATH = "app.tcss"

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args

    def compose(self) -> ComposeResult:
        yield RichLog(id="output", wrap=True, markup=True)
        yield Input(id="user-input")

    def on_mount(self) -> None:
        self.query_one("#user-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        event.input.clear()
        log = self.query_one("#output", RichLog)
        if value.startswith("/"):
            result = registry.execute(self.app, value)
            if result is not None:
                log.write(result)
        else:
            log.write(value)


class SecretApp(App):
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args

    def on_mount(self) -> None:
        self.push_screen(MainScreen(self.args))
