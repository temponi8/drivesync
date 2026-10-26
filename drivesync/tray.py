"""GNOME tray icon (AppIndicator)."""

import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator, Gtk  # noqa: E402

from . import config as cfgmod  # noqa: E402
from .config import Config  # noqa: E402
from .daemon import Coordinator, State, Status  # noqa: E402

ICONS = {
    State.STARTING: "emblem-synchronizing-symbolic",
    State.SYNCING: "emblem-synchronizing-symbolic",
    State.IDLE: "emblem-ok-symbolic",
    State.PAUSED: "media-playback-pause-symbolic",
    State.OFFLINE: "network-offline-symbolic",
    State.CONFLICTS: "dialog-warning-symbolic",
    State.NEEDS_RESYNC: "dialog-error-symbolic",
    State.AUTH_EXPIRED: "dialog-password-symbolic",
    State.ERROR: "dialog-error-symbolic",
}


class Tray:
    def __init__(self, cfg: Config, coord: Coordinator, quit_cb):
        Gtk.init([])
        self.cfg = cfg
        self.coord = coord
        self.ind = AppIndicator.Indicator.new(
            "drivesync", ICONS[State.STARTING], AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_title("DriveSync")

        menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="Starting…")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)
        menu.append(Gtk.SeparatorMenuItem())
        self._item(menu, "Sync now", lambda: coord.request("tray"))
        self._item(menu, "Open folder", lambda: subprocess.Popen(["xdg-open", str(cfg.local)]))
        self.pause_item = self._item(menu, "Pause syncing", self._toggle_pause)
        self.conflicts_item = self._item(menu, "Dismiss conflict warning", coord.clear_conflicts)
        self._item(menu, "View log", lambda: subprocess.Popen(["xdg-open", str(cfgmod.RCLONE_LOG)]))
        menu.append(Gtk.SeparatorMenuItem())
        self._item(menu, "Quit", quit_cb)
        menu.show_all()
        self.conflicts_item.hide()
        self.ind.set_menu(menu)

    @staticmethod
    def _item(menu, label, fn):
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda _i: fn())
        menu.append(item)
        return item

    def _toggle_pause(self):
        self.coord.set_paused(not self.coord.paused)
        self.pause_item.set_label("Resume syncing" if self.coord.paused else "Pause syncing")

    def update(self, status: Status):
        self.ind.set_icon_full(ICONS[status.state], status.state.value)
        text = status.message
        if status.state == State.IDLE and status.last_success:
            text = f"Up to date ({status.last_success[11:16]})"
        self.status_item.set_label(text[:80])
        self.conflicts_item.set_visible(bool(status.conflicts))
