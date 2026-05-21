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
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
)

from secret import storage


SIMPLE_TYPE = "SimpleCredentials"
COMPLEX_TYPE = "ComplexCredentials"


@dataclass(frozen=True)
class Field:
    """A single labelled entry inside a complex credential."""

    label: str
    value: str
    secret: bool = False

    def as_payload(self) -> dict[str, object]:
        return {"label": self.label, "value": self.value, "secret": self.secret}

    @classmethod
    def from_payload(cls, data: dict[str, object]) -> "Field":
        return cls(
            label=str(data.get("label", "")),
            value=str(data.get("value", "")),
            secret=bool(data.get("secret", False)),
        )


@dataclass(frozen=True)
class Record:
    name: str
    secret: str = ""
    type: str = SIMPLE_TYPE
    url: str | None = None
    login: str | None = None
    tag: str = "NULL"
    label: str = "General"
    fields: tuple[Field, ...] = ()

    @property
    def is_complex(self) -> bool:
        return self.type == COMPLEX_TYPE

    def as_payload(self) -> dict[str, object]:
        if self.is_complex:
            return {
                "name": self.name,
                "type": self.type,
                "tag": self.tag,
                "label": self.label,
                "fields": [field.as_payload() for field in self.fields],
            }
        return {
            "name": self.name,
            "type": self.type,
            "tag": self.tag,
            "label": self.label,
            "url": self.url,
            "login": self.login,
            "secret": self.secret,
        }

    @classmethod
    def from_payload(cls, data: dict[str, object]) -> "Record":
        raw_fields = data.get("fields") or ()
        return cls(
            name=str(data["name"]),
            secret=str(data.get("secret") or ""),
            type=str(data.get("type") or SIMPLE_TYPE),
            url=data.get("url") or None,
            login=data.get("login") or None,
            tag=str(data.get("tag") or "NULL"),
            label=str(data.get("label") or "General"),
            fields=tuple(
                Field.from_payload(field)
                for field in raw_fields
                if isinstance(field, dict)
            ),
        )


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
                records = [Record.from_payload(r) for r in storage.load_records(password)]
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


class ComplexFieldRow(Horizontal):
    """A single editable label/value/secret row in the complex builder."""

    def __init__(self, label: str = "", value: str = "", secret: bool = False) -> None:
        super().__init__(classes="complex-field-row")
        self._initial_label = label
        self._initial_value = value
        self._initial_secret = secret

    def compose(self) -> ComposeResult:
        yield Input(
            value=self._initial_label,
            placeholder="Label",
            classes="field-label-input",
        )
        yield Input(
            value=self._initial_value,
            placeholder="Value",
            password=self._initial_secret,
            classes="field-value-input",
        )
        yield Checkbox("Secret", value=self._initial_secret, classes="field-secret-toggle")
        yield Button("✕", classes="remove-field", variant="default")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        self.query_one(".field-value-input", Input).password = bool(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if "remove-field" in event.button.classes:
            event.stop()
            self.remove()

    def to_field(self) -> Field:
        return Field(
            label=self.query_one(".field-label-input", Input).value.strip(),
            value=self.query_one(".field-value-input", Input).value,
            secret=self.query_one(".field-secret-toggle", Checkbox).value,
        )


class AddRecordScreen(ModalScreen[Record | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, record: Record | None = None) -> None:
        super().__init__()
        self._record = record

    def compose(self) -> ComposeResult:
        record = self._record
        is_simple = record is not None and not record.is_complex
        with Vertical(id="add-record-dialog"):
            with TabbedContent(id="record-tabs"):
                with TabPane("Simple", id="tab-simple"):
                    yield Label("Name", classes="field-label")
                    yield Input(
                        id="record-name",
                        placeholder="Record name",
                        value=record.name if is_simple else "",
                    )
                    yield Label("Tag", classes="field-label")
                    yield Input(
                        id="record-tag",
                        placeholder="Tag",
                        value=record.tag if is_simple else "",
                    )
                    yield Label("Label (Optional)", classes="field-label")
                    yield Input(
                        id="record-label",
                        placeholder="General",
                        value=record.label if is_simple else "",
                    )
                    yield Label("URL (Optional)", classes="field-label")
                    yield Input(
                        id="record-url",
                        placeholder="URL",
                        value=(record.url or "") if is_simple else "",
                    )
                    yield Label("Login (Optional)", classes="field-label")
                    yield Input(
                        id="record-login",
                        placeholder="Login",
                        value=(record.login or "") if is_simple else "",
                    )
                    yield Label("Value", classes="field-label")
                    with Horizontal(id="record-value-row"):
                        yield Input(
                            id="record-value",
                            placeholder="Record value",
                            password=True,
                            value=record.secret if is_simple else "",
                        )
                        yield Button("*", id="toggle-record-value", variant="default")
                with TabPane("Complex", id="tab-complex"):
                    yield Label("Name", classes="field-label")
                    yield Input(
                        id="complex-name",
                        placeholder="Record name",
                        value=record.name if record and record.is_complex else "",
                    )
                    yield Label("Tag", classes="field-label")
                    yield Input(
                        id="complex-tag",
                        placeholder="Tag",
                        value=record.tag if record and record.is_complex else "",
                    )
                    yield Label("Label (Optional)", classes="field-label")
                    yield Input(
                        id="complex-label",
                        placeholder="General",
                        value=record.label if record and record.is_complex else "",
                    )
                    yield Vertical(id="complex-fields")
                    yield Button("+ Add field", id="add-field", variant="default")
            yield Static("", id="record-error")
            with Horizontal(id="record-buttons"):
                yield Button("Cancel", id="cancel-record", variant="default")
                yield Button(
                    "Save" if record else "Add",
                    id="save-record",
                    variant="primary",
                )

    def on_mount(self) -> None:
        dialog = self.query_one("#add-record-dialog")
        tabs = self.query_one("#record-tabs", TabbedContent)
        container = self.query_one("#complex-fields")
        record = self._record

        if record is None:
            dialog.border_title = "Add Record"
            container.mount(ComplexFieldRow())
            self.query_one("#record-name", Input).focus()
            return

        dialog.border_title = "Edit Record"
        if record.is_complex:
            for field in record.fields:
                container.mount(
                    ComplexFieldRow(label=field.label, value=field.value, secret=field.secret)
                )
            tabs.active = "tab-complex"
            tabs.disable_tab("tab-simple")
            self.call_after_refresh(lambda: self.query_one("#complex-name", Input).focus())
        else:
            tabs.disable_tab("tab-complex")
            self.query_one("#record-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "record-name":
            self.query_one("#record-tag", Input).focus()
        elif event.input.id == "record-tag":
            self.query_one("#record-label", Input).focus()
        elif event.input.id == "record-label":
            self.query_one("#record-url", Input).focus()
        elif event.input.id == "record-url":
            self.query_one("#record-login", Input).focus()
        elif event.input.id == "record-login":
            self.query_one("#record-value", Input).focus()
        elif event.input.id == "record-value":
            self._save_record()
        elif event.input.id == "complex-name":
            self.query_one("#complex-tag", Input).focus()
        elif event.input.id == "complex-tag":
            self.query_one("#complex-label", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-record":
            self.action_cancel()
        elif event.button.id == "save-record":
            self._save_record()
        elif event.button.id == "toggle-record-value":
            self._toggle_value_visibility()
        elif event.button.id == "add-field":
            self._add_field()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _add_field(self) -> None:
        row = ComplexFieldRow()
        self.query_one("#complex-fields").mount(row)
        self.call_after_refresh(lambda: row.query_one(".field-label-input", Input).focus())

    def _toggle_value_visibility(self) -> None:
        value_input = self.query_one("#record-value", Input)
        toggle_button = self.query_one("#toggle-record-value", Button)
        value_input.password = not value_input.password
        toggle_button.label = "👁"
        value_input.focus()

    def _save_record(self) -> None:
        error = self.query_one("#record-error", Static)
        if self.query_one("#record-tabs", TabbedContent).active == "tab-complex":
            self._save_complex_record(error)
        else:
            self._save_simple_record(error)

    def _save_simple_record(self, error: Static) -> None:
        name_input = self.query_one("#record-name", Input)
        tag_input = self.query_one("#record-tag", Input)
        value_input = self.query_one("#record-value", Input)
        name = name_input.value.strip()
        tag = tag_input.value.strip()
        label = self.query_one("#record-label", Input).value.strip() or "General"
        url = self.query_one("#record-url", Input).value.strip() or None
        login = self.query_one("#record-login", Input).value.strip() or None
        value = value_input.value

        if not name:
            error.update("Name is required.")
            name_input.focus()
            return
        if not tag:
            error.update("Tag is required.")
            tag_input.focus()
            return
        if not value:
            error.update("Value is required.")
            value_input.focus()
            return

        self.dismiss(
            Record(name=name, url=url, login=login, secret=value, tag=tag, label=label)
        )

    def _save_complex_record(self, error: Static) -> None:
        name_input = self.query_one("#complex-name", Input)
        tag_input = self.query_one("#complex-tag", Input)
        name = name_input.value.strip()
        tag = tag_input.value.strip()
        label = self.query_one("#complex-label", Input).value.strip() or "General"
        if not name:
            error.update("Name is required.")
            name_input.focus()
            return
        if not tag:
            error.update("Tag is required.")
            tag_input.focus()
            return

        fields: list[Field] = []
        for row in self.query(ComplexFieldRow):
            field = row.to_field()
            if not field.label and not field.value:
                continue
            if not field.label:
                error.update("Every field needs a label.")
                return
            fields.append(field)

        if not fields:
            error.update("Add at least one field.")
            return
        if not any(field.secret for field in fields):
            error.update("Mark at least one field as secret.")
            return

        self.dismiss(
            Record(name=name, type=COMPLEX_TYPE, fields=tuple(fields), tag=tag, label=label)
        )


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
        ("e", "edit_record", "Edit Record"),
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
        if action in (
            "reveal_value",
            "reveal_record",
            "buffer_secret",
            "delete_record",
            "edit_record",
        ):
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

        if record.is_complex:
            value_widget.update(self._complex_detail_text(record))
            return

        details: list[str] = [
            f"[#8892b5]Tag:[/]  {record.tag}",
            f"[#8892b5]Label:[/]  {record.label}",
        ]
        if record.url:
            details.append(f"[#8892b5]URL:[/]  {record.url}")
        if record.login:
            details.append(f"[#8892b5]Login:[/]  {record.login}")
        if self._value_visible:
            try:
                secret = storage.decrypt_secret(self._master_password, record.secret)
            except InvalidToken:
                self._value_visible = False
                value_widget.update("[#8892b5]Secret:[/]  Could not decrypt secret")
                self.app.notify("Could not decrypt secret.", severity="error")
                return
            details.append(f"[#8892b5]Secret:[/]  {secret}")
        else:
            details.append(f"[#8892b5]Secret:[/]  {'•' * 12}")
        value_widget.update("\n".join(details))

    def _complex_detail_text(self, record: Record) -> str:
        lines: list[str] = [
            f"[#8892b5]Tag:[/]  {record.tag}",
            f"[#8892b5]Label:[/]  {record.label}",
        ]
        for field in record.fields:
            if not field.secret:
                lines.append(f"[#8892b5]{field.label}:[/]  {field.value}")
                continue
            if not self._value_visible:
                lines.append(f"[#8892b5]{field.label}:[/]  {'•' * 12}")
                continue
            try:
                value = storage.decrypt_secret(self._master_password, field.value)
            except InvalidToken:
                self._value_visible = False
                self.app.notify("Could not decrypt secret.", severity="error")
                return self._complex_detail_text(record)
            lines.append(f"[#8892b5]{field.label}:[/]  {value}")
        return "\n".join(lines)

    def action_reveal_value(self) -> None:
        if self._selected_index is not None:
            self._value_visible = not self._value_visible
            self._refresh_detail()

    def action_buffer_secret(self) -> None:
        index = self._selected_index
        if index is None or index >= len(self._records):
            return

        token = self._primary_secret_token(self._records[index])
        if token is None:
            self.app.notify("No secret to copy.", severity="warning")
            return

        try:
            secret = storage.decrypt_secret(self._master_password, token)
        except InvalidToken:
            self.app.notify("Could not decrypt secret.", severity="error")
            return

        if self._copy_to_system_buffer(secret):
            self.app.notify("Secret copied to system buffer.")
        else:
            self.app.notify("Could not copy secret to system buffer.", severity="error")

    def _primary_secret_token(self, record: Record) -> str | None:
        """The encrypted token copied to the buffer: first secret field for
        complex records, the single secret for simple ones."""
        if record.is_complex:
            return next((field.value for field in record.fields if field.secret), None)
        return record.secret or None

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
            [r.as_payload() for r in self._records],
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

    def action_edit_record(self) -> None:
        index = self._selected_index
        if index is None or index >= len(self._records):
            return

        record = self._records[index]
        try:
            editable_record = self._decrypt_record(record)
        except InvalidToken:
            self.app.notify("Could not decrypt secret.", severity="error")
            return

        self.app.push_screen(
            AddRecordScreen(record=editable_record),
            callback=lambda updated: self._record_edited_at(index, updated),
        )

    def _decrypt_record(self, record: Record) -> Record:
        """Return a copy with secret values decrypted, ready for editing."""
        if record.is_complex:
            return Record(
                name=record.name,
                type=record.type,
                tag=record.tag,
                label=record.label,
                fields=tuple(
                    Field(
                        label=field.label,
                        value=(
                            storage.decrypt_secret(self._master_password, field.value)
                            if field.secret
                            else field.value
                        ),
                        secret=field.secret,
                    )
                    for field in record.fields
                ),
            )
        return Record(
            name=record.name,
            type=record.type,
            tag=record.tag,
            label=record.label,
            url=record.url,
            login=record.login,
            secret=storage.decrypt_secret(self._master_password, record.secret),
        )

    def _encrypt_record(self, record: Record) -> Record:
        """Return a copy with secret values encrypted, ready for storage."""
        if record.is_complex:
            return Record(
                name=record.name,
                type=record.type,
                tag=record.tag,
                label=record.label,
                fields=tuple(
                    Field(
                        label=field.label,
                        value=(
                            storage.encrypt_secret(self._master_password, field.value)
                            if field.secret
                            else field.value
                        ),
                        secret=field.secret,
                    )
                    for field in record.fields
                ),
            )
        return Record(
            name=record.name,
            type=record.type,
            tag=record.tag,
            label=record.label,
            url=record.url,
            login=record.login,
            secret=storage.encrypt_secret(self._master_password, record.secret),
        )

    def action_dump_records(self) -> None:
        path = storage.dump_records([
            record.as_payload()
            for record in self._records
        ])
        self.app.notify(f"Records dumped to {path}.")

    def _record_added(self, record: Record | None) -> None:
        if record is None:
            return
        encrypted_record = self._encrypt_record(record)
        self._records.append(encrypted_record)
        list_view = self.query_one("#record-list", ListView)
        list_view.append(ListItem(Label(encrypted_record.name)))
        if self._selected_index is None:
            list_view.index = 0
            self._set_selected_index(list_view.index)
            self._refresh_detail()
        storage.save_records(
            self._master_password,
            [r.as_payload() for r in self._records],
        )

    def _record_edited_at(self, index: int, record: Record | None) -> None:
        if record is None or index >= len(self._records):
            return

        encrypted_record = self._encrypt_record(record)
        self._records[index] = encrypted_record

        list_view = self.query_one("#record-list", ListView)
        list_item = list(list_view.query(ListItem))[index]
        list_item.query_one(Label).update(encrypted_record.name)

        storage.save_records(
            self._master_password,
            [r.as_payload() for r in self._records],
        )
        self._value_visible = False
        self._refresh_detail()

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
                records = [Record.from_payload(r) for r in storage.load_records(cached_password)]
            except InvalidToken:
                storage.clear_session()
            else:
                storage.save_session(cached_password)
                self.push_screen(MainScreen(records=records, master_password=cached_password))
                return

        self.push_screen(UnlockScreen(is_new=False))
