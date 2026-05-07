from __future__ import annotations

import argparse
from dataclasses import dataclass

from cryptography.fernet import InvalidToken
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Static

from secret import storage


@dataclass(frozen=True)
class Record:
    name: str
    value: str


class UnlockScreen(Screen):
    BINDINGS = [("escape", "quit", "Quit")]

    def __init__(self, is_new: bool) -> None:
        super().__init__()
        self._is_new = is_new

    def compose(self) -> ComposeResult:
        button_label = "Create" if self._is_new else "Unlock"
        with Vertical(id="unlock-dialog"):
            yield Label("Master password", classes="field-label")
            yield Input(id="unlock-password", placeholder="Password", password=True)
            if self._is_new:
                yield Label("Confirm password", classes="field-label")
                yield Input(id="unlock-confirm", placeholder="Confirm password", password=True)
            yield Static("", id="unlock-error")
            with Horizontal(id="unlock-buttons"):
                yield Button("Quit", id="quit-unlock", variant="default")
                yield Button(button_label, id="submit-unlock", variant="primary")

    def on_mount(self) -> None:
        title = "Create Vault" if self._is_new else "Unlock Vault"
        self.query_one("#unlock-dialog").border_title = title
        self.query_one("#unlock-password", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._is_new and event.input.id == "unlock-password":
            self.query_one("#unlock-confirm", Input).focus()
        else:
            self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit-unlock":
            self.action_quit()
        elif event.button.id == "submit-unlock":
            self._submit()

    def action_quit(self) -> None:
        self.app.exit()

    def _submit(self) -> None:
        password = self.query_one("#unlock-password", Input).value
        error = self.query_one("#unlock-error", Static)

        if not password:
            error.update("Password is required.")
            self.query_one("#unlock-password", Input).focus()
            return

        if self._is_new:
            confirm = self.query_one("#unlock-confirm", Input).value
            if password != confirm:
                error.update("Passwords do not match.")
                self.query_one("#unlock-confirm", Input).focus()
                return
            storage.save_records(password, [])
            records: list[Record] = []
        else:
            try:
                records = [Record(**r) for r in storage.load_records(password)]
            except InvalidToken:
                error.update("Wrong password.")
                self.query_one("#unlock-password", Input).clear()
                self.query_one("#unlock-password", Input).focus()
                return

        self.app.switch_screen(MainScreen(records=records, master_password=password))


class AddRecordScreen(ModalScreen[Record | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="add-record-dialog"):
            yield Label("Name", classes="field-label")
            yield Input(id="record-name", placeholder="Record name")
            yield Label("Value", classes="field-label")
            yield Input(id="record-value", placeholder="Record value", password=True)
            yield Static("", id="record-error")
            with Horizontal(id="record-buttons"):
                yield Button("Cancel", id="cancel-record", variant="default")
                yield Button("Add", id="save-record", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#add-record-dialog").border_title = "Add Record"
        self.query_one("#record-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "record-name":
            self.query_one("#record-value", Input).focus()
        else:
            self._save_record()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-record":
            self.action_cancel()
        elif event.button.id == "save-record":
            self._save_record()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save_record(self) -> None:
        name_input = self.query_one("#record-name", Input)
        value_input = self.query_one("#record-value", Input)
        name = name_input.value.strip()
        value = value_input.value
        error = self.query_one("#record-error", Static)

        if not name:
            error.update("Name is required.")
            name_input.focus()
            return
        if not value:
            error.update("Value is required.")
            value_input.focus()
            return

        self.dismiss(Record(name=name, value=value))


class MainScreen(Screen):
    BINDINGS = [
        ("ctrl+n", "add_record", "Add Record"),
        ("ctrl+d", "delete_record", "Delete Record"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, records: list[Record], master_password: str) -> None:
        super().__init__()
        self._records = list(records)
        self._master_password = master_password
        self._selected_index: int | None = None
        self._value_visible = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="main-layout"):
            with Vertical(id="record-panel"):
                yield ListView(id="record-list")
            with Vertical(id="detail-panel"):
                yield Static("Select a record", id="detail-name")
                yield Static("", id="detail-value")
                yield Button("Show", id="toggle-value")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#record-panel").border_title = "Records"
        self.query_one("#detail-panel").border_title = "Details"
        list_view = self.query_one("#record-list", ListView)
        for record in self._records:
            list_view.append(ListItem(Label(record.name)))
        self.query_one("#toggle-value", Button).display = False

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._selected_index = self.query_one("#record-list", ListView).index
        self._value_visible = False
        self._refresh_detail()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "toggle-value":
            self._value_visible = not self._value_visible
            self._refresh_detail()

    def _refresh_detail(self) -> None:
        name_widget = self.query_one("#detail-name", Static)
        value_widget = self.query_one("#detail-value", Static)
        toggle_btn = self.query_one("#toggle-value", Button)

        index = self._selected_index
        if index is None or index >= len(self._records):
            name_widget.update("Select a record to view details")
            value_widget.update("")
            toggle_btn.display = False
            return

        record = self._records[index]
        name_widget.update(f"[#8892b5]name [/]  {record.name}")
        if self._value_visible:
            value_widget.update(f"[#8892b5]value[/]  {record.value}")
            toggle_btn.label = "hide"
        else:
            value_widget.update(f"[#8892b5]value[/]  {'•' * len(record.value)}")
            toggle_btn.label = "show"
        toggle_btn.display = True

    def action_delete_record(self) -> None:
        index = self._selected_index
        if index is None or index >= len(self._records):
            return

        list_view = self.query_one("#record-list", ListView)
        list(list_view.query(ListItem))[index].remove()
        self._records.pop(index)
        storage.save_records(
            self._master_password,
            [{"name": r.name, "value": r.value} for r in self._records],
        )

        if not self._records:
            self._selected_index = None
        elif index >= len(self._records):
            self._selected_index = len(self._records) - 1
            list_view.index = self._selected_index

        self._value_visible = False
        self._refresh_detail()

    def action_add_record(self) -> None:
        self.app.push_screen(AddRecordScreen(), callback=self._record_added)

    def _record_added(self, record: Record | None) -> None:
        if record is None:
            return
        self._records.append(record)
        self.query_one("#record-list", ListView).append(ListItem(Label(record.name)))
        storage.save_records(
            self._master_password,
            [{"name": r.name, "value": r.value} for r in self._records],
        )

    def action_quit(self) -> None:
        self.app.exit()


class SecretApp(App):
    CSS_PATH = "app.tcss"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args

    def on_mount(self) -> None:
        self.push_screen(UnlockScreen(is_new=not storage.vault_exists()))
