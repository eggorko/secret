from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import time
from dataclasses import dataclass

from cryptography.fernet import InvalidToken
from textual.app import App, ComposeResult
from textual.command import Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Static

from secret import storage


@dataclass(frozen=True)
class Record:
    name: str
    secret: str


class DetailPanel(Vertical):
    can_focus = True

class UnlockScreen(Screen):
    BINDINGS = [("escape", "quit", "Quit")]

    GRACE_ATTEMPTS = 2
    COOLDOWN_BASE_S = 5
    COOLDOWN_MAX_S = 300

    def __init__(self, is_new: bool) -> None:
        super().__init__()
        self._is_new = is_new
        self._wrong_attempts = 0
        self._locked_until = 0.0
        self._tick_timer = None

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
        if self._cooldown_remaining() > 0:
            return

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
                self._handle_wrong_password()
                return
            self._wrong_attempts = 0

        storage.save_session(password)
        self.app.switch_screen(MainScreen(records=records, master_password=password))

    def _handle_wrong_password(self) -> None:
        self._wrong_attempts += 1
        password_input = self.query_one("#unlock-password", Input)
        password_input.clear()

        if self._wrong_attempts > self.GRACE_ATTEMPTS:
            self._start_cooldown()
        else:
            error = self.query_one("#unlock-error", Static)
            remaining = self.GRACE_ATTEMPTS - self._wrong_attempts + 1
            error.update(f"Wrong password. {remaining} attempt(s) before cooldown.")
            password_input.focus()

    def _start_cooldown(self) -> None:
        excess = self._wrong_attempts - self.GRACE_ATTEMPTS
        delay = min(self.COOLDOWN_MAX_S, self.COOLDOWN_BASE_S * (2 ** (excess - 1)))
        self._locked_until = time.monotonic() + delay

        self.query_one("#unlock-password", Input).disabled = True
        self.query_one("#submit-unlock", Button).disabled = True
        self._tick_cooldown()
        if self._tick_timer is None:
            self._tick_timer = self.set_interval(1.0, self._tick_cooldown)

    def _tick_cooldown(self) -> None:
        remaining = self._cooldown_remaining()
        error = self.query_one("#unlock-error", Static)
        if remaining > 0:
            error.update(f"Locked. Try again in {remaining}s.")
            return

        if self._tick_timer is not None:
            self._tick_timer.stop()
            self._tick_timer = None
        self.query_one("#unlock-password", Input).disabled = False
        self.query_one("#submit-unlock", Button).disabled = False
        self.query_one("#unlock-password", Input).focus()
        error.update("")

    def _cooldown_remaining(self) -> int:
        return max(0, math.ceil(self._locked_until - time.monotonic()))


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

        self.dismiss(Record(name=name, secret=value))


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("left", "focus_cancel", "Cancel"),
        ("right", "focus_confirm", "Confirm"),
        ("enter", "apply_selected", "Apply"),
    ]

    def __init__(self, title: str, message: str, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="cancel-confirm", variant="default")
                yield Button(self._confirm_label, id="accept-confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#confirm-dialog").border_title = self._title
        self.query_one("#cancel-confirm", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-confirm":
            self.action_cancel()
        elif event.button.id == "accept-confirm":
            self.dismiss(True)

    def action_focus_cancel(self) -> None:
        self.query_one("#cancel-confirm", Button).focus()

    def action_focus_confirm(self) -> None:
        self.query_one("#accept-confirm", Button).focus()

    def action_apply_selected(self) -> None:
        focused_id = getattr(self.focused, "id", None)
        if focused_id == "accept-confirm":
            self.dismiss(True)
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(False)


class MainScreen(Screen):
    BINDINGS = [
        ("n", "add_record", "Add Record"),
        ("c", "buffer_secret", "Copy Value"),
        ("r", "reveal_value", "Reveal Value"),
        ("d", "delete_record", "Delete Record"),
        ("f12", "dump_records", "Dump Records"),
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
                yield ListView(
                    *(ListItem(Label(record.name)) for record in self._records),
                    id="record-list",
                    initial_index=0 if self._records else None,
                )
            with DetailPanel(id="detail-panel"):
                yield Static("Select a record", id="detail-name")
                yield Static("", id="detail-value")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#record-panel").border_title = "Records"
        self.query_one("#detail-panel").border_title = "Details"
        list_view = self.query_one("#record-list", ListView)
        self._set_selected_index(list_view.index)
        self._refresh_detail()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "add_record":
            return isinstance(self.focused, ListView)
        if action in ("reveal_value", "reveal_record", "buffer_secret", "delete_record"):
            return self._selected_index is not None
        return True

    def _set_selected_index(self, index: int | None) -> None:
        if self._selected_index == index:
            return
        self._selected_index = index
        self.refresh_bindings()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._set_selected_index(self.query_one("#record-list", ListView).index)
        self._value_visible = False
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        name_widget = self.query_one("#detail-name", Static)
        value_widget = self.query_one("#detail-value", Static)

        index = self._selected_index
        if index is None or index >= len(self._records):
            name_widget.update("Select a record")
            value_widget.update("")
            return

        record = self._records[index]
        name_widget.update(f"[#8892b5]Name: [/]  {record.name}")
        if self._value_visible:
            try:
                secret = storage.decrypt_secret(self._master_password, record.secret)
            except InvalidToken:
                self._value_visible = False
                value_widget.update("[#8892b5]Secret:[/]  Could not decrypt secret")
                self.app.notify("Could not decrypt secret.", severity="error")
                return
            value_widget.update(f"[#8892b5]Secret:[/]  {secret}")
        else:
            value_widget.update(f"[#8892b5]Secret:[/]  {'•' * 12}")

    def action_reveal_value(self) -> None:
        if self._selected_index is not None:
            self._value_visible = not self._value_visible
            self._refresh_detail()

    def action_buffer_secret(self) -> None:
        index = self._selected_index
        if index is None or index >= len(self._records):
            return

        try:
            secret = storage.decrypt_secret(self._master_password, self._records[index].secret)
        except InvalidToken:
            self.app.notify("Could not decrypt secret.", severity="error")
            return

        if self._copy_to_system_buffer(secret):
            self.app.notify("Secret copied to system buffer.")
        else:
            self.app.notify("Could not copy secret to system buffer.", severity="error")

    def _copy_to_system_buffer(self, value: str) -> bool:
        self.app.copy_to_clipboard(value)

        commands = [
            ("pbcopy", []),
            ("wl-copy", []),
            ("xclip", ["-selection", "clipboard"]),
            ("xsel", ["--clipboard", "--input"]),
            ("clip.exe", []),
        ]
        for command, args in commands:
            executable = shutil.which(command)
            if executable is None:
                continue
            try:
                subprocess.run(
                    [executable, *args],
                    input=value,
                    text=True,
                    check=True,
                )
            except (OSError, subprocess.CalledProcessError):
                continue
            return True

        return False

    def action_delete_record(self) -> None:
        index = self._selected_index
        if index is None or index >= len(self._records):
            return

        record = self._records[index]
        self.app.push_screen(
            ConfirmScreen(
                title="Delete Record",
                message=f'Delete "{record.name}"? This cannot be undone.',
                confirm_label="Delete",
            ),
            callback=lambda confirmed: self._delete_record_at(index) if confirmed else None,
        )

    def _delete_record_at(self, index: int) -> None:
        if index >= len(self._records):
            return

        list_view = self.query_one("#record-list", ListView)
        list(list_view.query(ListItem))[index].remove()
        self._records.pop(index)
        storage.save_records(
            self._master_password,
            [{"name": r.name, "secret": r.secret} for r in self._records],
        )

        if not self._records:
            self._set_selected_index(None)
        elif index >= len(self._records):
            self._set_selected_index(len(self._records) - 1)
            list_view.index = self._selected_index

        self._value_visible = False
        self._refresh_detail()

    def action_add_record(self) -> None:
        self.app.push_screen(AddRecordScreen(), callback=self._record_added)

    def action_dump_records(self) -> None:
        path = storage.dump_records([
            {"name": record.name, "secret": record.secret}
            for record in self._records
        ])
        self.app.notify(f"Records dumped to {path}.")

    def _record_added(self, record: Record | None) -> None:
        if record is None:
            return
        encrypted_record = Record(
            name=record.name,
            secret=storage.encrypt_secret(self._master_password, record.secret),
        )
        self._records.append(encrypted_record)
        list_view = self.query_one("#record-list", ListView)
        list_view.append(ListItem(Label(encrypted_record.name)))
        if self._selected_index is None:
            list_view.index = 0
            self._set_selected_index(list_view.index)
            self._refresh_detail()
        storage.save_records(
            self._master_password,
            [{"name": r.name, "secret": r.secret} for r in self._records],
        )

    def action_quit(self) -> None:
        self.app.exit()


class SecretApp(App):
    CSS_PATH = "app.tcss"

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args

    def on_mount(self) -> None:
        if not storage.vault_exists():
            storage.clear_session()
            self.push_screen(UnlockScreen(is_new=True))
            return

        cached_password = storage.load_session()
        if cached_password is not None:
            try:
                records = [Record(**r) for r in storage.load_records(cached_password)]
            except InvalidToken:
                storage.clear_session()
            else:
                storage.save_session(cached_password)
                self.push_screen(MainScreen(records=records, master_password=cached_password))
                return

        self.push_screen(UnlockScreen(is_new=False))
