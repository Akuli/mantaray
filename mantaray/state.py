"""Shared application state for Mantaray.

This module defines a minimal state model for messages, views, and servers.
The goal is to provide a frontend/backend boundary that can be used by both
GUI widgets and backend event handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ViewType = Literal["server", "channel", "pm"]


@dataclass
class MessagePartState:
    text: str
    tags: list[str] = field(default_factory=list)


@dataclass
class MessageState:
    sender: str | None
    parts: list[MessagePartState] = field(default_factory=list)
    tag: str = "info"
    pinged: bool = False
    history_id: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now().astimezone())

    def __post_init__(self) -> None:
        assert self.timestamp.tzinfo is not None


@dataclass
class ViewState:
    view_id: str
    view_type: ViewType
    name: str
    parent_id: str | None = None
    messages: list[MessageState] = field(default_factory=list)
    notification_count: int = 0
    selector_tags: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    def add_message(self, message: MessageState) -> None:
        self.messages.append(message)

    def increment_notification_count(self) -> None:
        self.notification_count += 1

    def clear_notifications(self) -> None:
        self.notification_count = 0
        self.selector_tags = [tag for tag in self.selector_tags if tag not in ("new_message", "pinged")]

    def add_selector_tag(self, tag: str) -> None:
        if tag not in self.selector_tags:
            self.selector_tags.append(tag)


@dataclass
class ServerState:
    server_id: str
    name: str
    host: str
    nick: str
    connected: bool = False
    view_ids: list[str] = field(default_factory=list)
    joined_channels: list[str] = field(default_factory=list)
    away_status: str | None = None
    userlist: dict[str, list[str]] = field(default_factory=dict)

    def add_view(self, view_id: str) -> None:
        if view_id not in self.view_ids:
            self.view_ids.append(view_id)

    def remove_view(self, view_id: str) -> None:
        if view_id in self.view_ids:
            self.view_ids.remove(view_id)


@dataclass
class AppState:
    server_ids: list[str] = field(default_factory=list)
    servers: dict[str, ServerState] = field(default_factory=dict)
    views: dict[str, ViewState] = field(default_factory=dict)

    def add_server(self, server_state: ServerState) -> None:
        if server_state.server_id not in self.servers:
            self.server_ids.append(server_state.server_id)
            self.servers[server_state.server_id] = server_state

    def remove_server(self, server_id: str) -> None:
        if server_id in self.servers:
            self.server_ids.remove(server_id)
            del self.servers[server_id]

    def add_view(self, view_state: ViewState) -> None:
        if view_state.view_id not in self.views:
            self.views[view_state.view_id] = view_state

    def remove_view(self, view_id: str) -> None:
        if view_id in self.views:
            del self.views[view_id]


class UserAction:
    pass


@dataclass
class SendMessageAction(UserAction):
    view_id: str
    text: str
    history_id: int | None = None


@dataclass
class ExecuteCommandAction(UserAction):
    view_id: str
    command: str
    args: list[str] = field(default_factory=list)


@dataclass
class ChangeNickAction(UserAction):
    server_id: str
    new_nick: str


@dataclass
class SelectViewAction(UserAction):
    view_id: str


@dataclass
class ReconnectAction(UserAction):
    server_id: str
