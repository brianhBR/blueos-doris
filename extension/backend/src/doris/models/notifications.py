"""Notification models.

Defines the data structures for the real-time notification system.
Notifications are generated from system events (battery, storage,
dive status, network changes) and persisted to disk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationType(StrEnum):
    INFO = "info"
    WARNING = "warning"
    SUCCESS = "success"
    ERROR = "error"


class NotificationCategory(StrEnum):
    MISSION = "mission"
    SYSTEM = "system"
    NETWORK = "network"
    SOFTWARE = "software"


class NotificationItem(BaseModel):
    """A single notification entry."""

    id: str
    type: NotificationType = NotificationType.INFO
    category: NotificationCategory = NotificationCategory.SYSTEM
    title: str
    message: str
    timestamp: datetime = Field(default_factory=_utc_now)
    read: bool = False
    link_to: str | None = None


class NotificationSettings(BaseModel):
    """User preferences for which notification categories are enabled."""

    mission_alerts: bool = True
    system_warnings: bool = True
    network_status: bool = True
    software_updates: bool = False
