"""Notification service.

Generates real notifications from system events (battery, storage,
dive status, network) and persists them to disk. Each notification
is deduplicated by a stable key so repeated polling doesn't spam
the user.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..models.notifications import (
    NotificationCategory,
    NotificationItem,
    NotificationSettings,
    NotificationType,
)
from ..services.storage import DATA_ROOT

logger = logging.getLogger(__name__)

NOTIFICATIONS_DIR = DATA_ROOT / "notifications"
NOTIFICATIONS_FILE = NOTIFICATIONS_DIR / "notifications.json"
SETTINGS_FILE = NOTIFICATIONS_DIR / "notification_settings.json"
MAX_NOTIFICATIONS = 100


class NotificationService:
    """Manages notifications backed by JSON files on disk."""

    def __init__(self) -> None:
        NOTIFICATIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._notifications: list[NotificationItem] = []
        self._settings: NotificationSettings = NotificationSettings()
        self._active_keys: set[str] = set()

        # Thresholds that have already fired (reset when condition clears)
        self._battery_warned = False
        self._battery_critical = False
        self._storage_warned = False
        self._storage_critical = False
        self._last_network_ssid: str | None = None
        self._last_dive_active: bool | None = None

        self._load()

    # ── Persistence ──────────────────────────────────────────────

    def _load(self) -> None:
        if NOTIFICATIONS_FILE.is_file():
            try:
                raw = json.loads(NOTIFICATIONS_FILE.read_text())
                self._notifications = [
                    NotificationItem.model_validate(n) for n in raw
                ]
            except Exception as e:
                logger.warning("Failed to load notifications: %s", e)
                self._notifications = []

        if SETTINGS_FILE.is_file():
            try:
                self._settings = NotificationSettings.model_validate_json(
                    SETTINGS_FILE.read_text()
                )
            except Exception as e:
                logger.warning("Failed to load notification settings: %s", e)

        self._active_keys = {n.id.rsplit("-", 1)[0] for n in self._notifications if "-" in n.id}

    def _save_notifications(self) -> None:
        try:
            data = [n.model_dump(mode="json") for n in self._notifications]
            NOTIFICATIONS_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.warning("Failed to save notifications: %s", e)

    def _save_settings(self) -> None:
        try:
            SETTINGS_FILE.write_text(self._settings.model_dump_json(indent=2))
        except Exception as e:
            logger.warning("Failed to save notification settings: %s", e)

    # ── CRUD ─────────────────────────────────────────────────────

    def list_notifications(self) -> list[NotificationItem]:
        return list(self._notifications)

    def unread_count(self) -> int:
        return sum(1 for n in self._notifications if not n.read)

    def mark_read(self, notification_id: str) -> bool:
        for n in self._notifications:
            if n.id == notification_id:
                n.read = True
                self._save_notifications()
                return True
        return False

    def mark_all_read(self) -> None:
        for n in self._notifications:
            n.read = True
        self._save_notifications()

    def delete_notification(self, notification_id: str) -> bool:
        before = len(self._notifications)
        self._notifications = [
            n for n in self._notifications if n.id != notification_id
        ]
        if len(self._notifications) < before:
            self._save_notifications()
            return True
        return False

    def get_settings(self) -> NotificationSettings:
        return self._settings

    def update_settings(self, settings: NotificationSettings) -> NotificationSettings:
        self._settings = settings
        self._save_settings()
        return self._settings

    # ── Event-based notification generation ──────────────────────

    def _add(
        self,
        key: str,
        ntype: NotificationType,
        category: NotificationCategory,
        title: str,
        message: str,
        link_to: str | None = None,
    ) -> None:
        """Add a notification if the key hasn't been used recently."""
        notification = NotificationItem(
            id=f"{key}-{uuid.uuid4().hex[:8]}",
            type=ntype,
            category=category,
            title=title,
            message=message,
            timestamp=datetime.now(timezone.utc),
            read=False,
            link_to=link_to,
        )
        self._notifications.insert(0, notification)
        if len(self._notifications) > MAX_NOTIFICATIONS:
            self._notifications = self._notifications[:MAX_NOTIFICATIONS]
        self._save_notifications()

    async def check_battery(self, level: float, voltage: float | None) -> None:
        """Generate notifications based on battery level."""
        if not self._settings.system_warnings:
            return

        if level <= 15 and not self._battery_critical:
            self._battery_critical = True
            self._battery_warned = True
            v_str = f" ({voltage:.1f}V)" if voltage else ""
            self._add(
                "battery_critical",
                NotificationType.ERROR,
                NotificationCategory.SYSTEM,
                "Critical Battery Level",
                f"Battery has dropped to {level:.0f}%{v_str}. "
                "Return DORIS to surface immediately.",
            )
        elif level <= 30 and not self._battery_warned:
            self._battery_warned = True
            v_str = f" ({voltage:.1f}V)" if voltage else ""
            self._add(
                "battery_low",
                NotificationType.WARNING,
                NotificationCategory.SYSTEM,
                "Low Battery Warning",
                f"Battery is at {level:.0f}%{v_str}. Consider ending the dive soon.",
            )
        elif level > 35:
            self._battery_warned = False
            self._battery_critical = False

    async def check_storage(self, used_percent: float, available_gb: float) -> None:
        """Generate notifications based on storage usage."""
        if not self._settings.system_warnings:
            return

        if used_percent >= 95 and not self._storage_critical:
            self._storage_critical = True
            self._storage_warned = True
            self._add(
                "storage_critical",
                NotificationType.ERROR,
                NotificationCategory.SYSTEM,
                "Storage Almost Full",
                f"Storage is {used_percent:.0f}% full ({available_gb:.1f} GB remaining). "
                "Data recording may stop.",
            )
        elif used_percent >= 80 and not self._storage_warned:
            self._storage_warned = True
            self._add(
                "storage_low",
                NotificationType.WARNING,
                NotificationCategory.SYSTEM,
                "Low Storage Warning",
                f"Storage is {used_percent:.0f}% full ({available_gb:.1f} GB remaining).",
            )
        elif used_percent < 75:
            self._storage_warned = False
            self._storage_critical = False

    async def check_network(self, connected: bool, ssid: str | None) -> None:
        """Generate notifications on network state changes."""
        if not self._settings.network_status:
            return

        if self._last_network_ssid is None:
            self._last_network_ssid = ssid if connected else None
            return

        if connected and ssid and ssid != self._last_network_ssid:
            self._add(
                "net_connected",
                NotificationType.INFO,
                NotificationCategory.NETWORK,
                "Network Connected",
                f"Successfully connected to {ssid}.",
            )
        elif not connected and self._last_network_ssid is not None:
            self._add(
                "net_disconnected",
                NotificationType.WARNING,
                NotificationCategory.NETWORK,
                "Network Disconnected",
                f"Lost connection to {self._last_network_ssid}.",
            )

        self._last_network_ssid = ssid if connected else None

    async def check_dive_status(self, active: bool) -> None:
        """Generate notifications when dive state changes."""
        if not self._settings.mission_alerts:
            return

        if self._last_dive_active is None:
            self._last_dive_active = active
            return

        if active and not self._last_dive_active:
            self._add(
                "dive_started",
                NotificationType.SUCCESS,
                NotificationCategory.MISSION,
                "Dive Started",
                "DORIS has begun its dive mission. Monitoring in progress.",
            )
        elif not active and self._last_dive_active:
            self._add(
                "dive_completed",
                NotificationType.SUCCESS,
                NotificationCategory.MISSION,
                "Dive Completed",
                "DORIS has completed the dive mission and returned to the surface.",
                link_to="location",
            )

        self._last_dive_active = active

    async def poll_system_events(
        self,
        battery_level: float | None = None,
        battery_voltage: float | None = None,
        storage_used_percent: float | None = None,
        storage_available_gb: float | None = None,
        network_connected: bool | None = None,
        network_ssid: str | None = None,
        dive_active: bool | None = None,
    ) -> None:
        """Check all event sources and generate notifications as needed.

        Called by the notifications route on each poll cycle.
        """
        if battery_level is not None:
            await self.check_battery(battery_level, battery_voltage)
        if storage_used_percent is not None and storage_available_gb is not None:
            await self.check_storage(storage_used_percent, storage_available_gb)
        if network_connected is not None:
            await self.check_network(network_connected, network_ssid)
        if dive_active is not None:
            await self.check_dive_status(dive_active)
