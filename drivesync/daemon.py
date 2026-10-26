"""The background daemon: decides when to sync and keeps status up to date."""

import json
import logging
import os
import signal
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable

from . import config as cfgmod
from . import notify, syncer
from .config import Config
from .syncer import Outcome, SyncResult

log = logging.getLogger(__name__)


class State(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    SYNCING = "syncing"
    PAUSED = "paused"
    OFFLINE = "offline"
    CONFLICTS = "conflicts"
    NEEDS_RESYNC = "needs_resync"
    AUTH_EXPIRED = "auth_expired"
    ERROR = "error"


@dataclass
class Status:
    state: State = State.STARTING
    message: str = ""
    last_sync: str | None = None
    last_success: str | None = None
    last_error: str | None = None
    conflicts: list[str] = field(default_factory=list)
    pid: int = field(default_factory=os.getpid)


class Coordinator:
    """Serialises sync requests: one sync at a time, requests during a sync merge into one follow-up.

    `post(fn)` must run fn on the main thread; `start_worker(fn)` runs fn in the background.
    Both are injectable so the logic can be tested without GLib or threads.
    """

    def __init__(self, cfg: Config, post: Callable, start_worker: Callable, watcher=None,
                 run: Callable[[Config], SyncResult] = syncer.run_bisync):
        self.cfg = cfg
        self.post = post
        self.start_worker = start_worker
        self.watcher = watcher
        self.run = run
        self.status = Status()
        self.running = False
        self.pending = False
        self.paused = False
        self.online = True
        self.listeners: list[Callable[[Status], None]] = []

    # --- called from any thread -------------------------------------------------
    def request(self, reason: str = "") -> None:
        self.post(lambda: self._request(reason))

    # --- main thread ------------------------------------------------------------
    def _request(self, reason: str) -> None:
        if self.running or self.paused or not self.online:
            self.pending = True
            return
        log.info("sync starting (%s)", reason)
        self.running = True
        self.pending = False
        self._set(State.SYNCING, "Syncing…")
        if self.watcher:
            self.watcher.begin_sync()
        self.start_worker(self._work)

    def _work(self) -> None:
        try:
            result = self.run(self.cfg)
        except Exception as e:  # never let the worker die silently
            log.exception("sync crashed")
            result = SyncResult(Outcome.ERROR, -1, message=str(e))
        self.post(lambda: self._finished(result))

    def _finished(self, result: SyncResult) -> None:
        self.running = False
        changed = self.watcher.end_sync() if self.watcher else False
        now = datetime.now().isoformat(timespec="seconds")
        prev = self.status.state
        self.status.last_sync = now
        log.info("sync finished: %s %s", result.outcome.value, result.message)

        if result.outcome in (Outcome.OK, Outcome.CONFLICTS):
            self.status.last_success = now
            self.status.last_error = None
            if result.conflicts:
                self.status.conflicts = sorted(set(self.status.conflicts) | set(result.conflicts))
                notify.send("DriveSync: conflicting edits",
                            "Both versions kept:\n" + "\n".join(result.conflicts[:5]), "dialog-warning-symbolic")
            state = State.CONFLICTS if self.status.conflicts else State.IDLE
            self._set(state, result.message if result.conflicts else "Up to date")
        elif result.outcome in (Outcome.NEEDS_RESYNC, Outcome.AUTH_EXPIRED):
            state = State(result.outcome.value)
            self.status.last_error = result.message
            if prev != state:
                notify.send("DriveSync needs attention", result.message, "dialog-error-symbolic")
            self._set(state, result.message)
        else:
            self.status.last_error = result.message
            if prev != State.ERROR:
                notify.send("DriveSync error", result.message, "dialog-error-symbolic")
            self._set(State.ERROR, result.message)

        if self.pending or changed:
            self._request("follow-up")

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused:
            if not self.running:
                self._set(State.PAUSED, "Paused")
        else:
            self._set(State.IDLE, "Resumed")
            self._request("resumed")

    def set_online(self, online: bool) -> None:
        if online == self.online:
            return
        self.online = online
        if not online:
            if not self.running:
                self._set(State.OFFLINE, "Offline, waiting for network")
        else:
            self._request("network back")

    def clear_conflicts(self) -> None:
        self.status.conflicts = []
        if self.status.state == State.CONFLICTS:
            self._set(State.IDLE, "Up to date")

    def _set(self, state: State, message: str) -> None:
        self.status.state = state
        self.status.message = message
        write_status(self.status)
        for fn in self.listeners:
            fn(self.status)


def write_status(status: Status) -> None:
    cfgmod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cfgmod.STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(status), indent=2))
    tmp.replace(cfgmod.STATUS_FILE)


def read_pid() -> int | None:
    try:
        pid = int(cfgmod.PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError):
        return None
    except PermissionError:
        return None


def run(use_tray: bool = True) -> int:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    from .watcher import FolderWatcher, load_exclude_patterns

    cfg = cfgmod.load()
    syncer.check_rclone()

    if (other := read_pid()) and other != os.getpid():
        log.error("DriveSync is already running (pid %s)", other)
        return 1
    cfgmod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    cfgmod.PID_FILE.write_text(str(os.getpid()))

    def post(fn):
        GLib.idle_add(lambda: (fn(), False)[1])

    def start_worker(fn):
        threading.Thread(target=fn, name="bisync", daemon=True).start()

    coord = Coordinator(cfg, post, start_worker)
    watcher = FolderWatcher(cfg.local, load_exclude_patterns(cfg.filters), cfg.debounce_seconds,
                            lambda: coord.request("local change"))
    coord.watcher = watcher

    loop = GLib.MainLoop()
    tray = None
    if use_tray:
        try:
            from .tray import Tray

            tray = Tray(cfg, coord, loop.quit)
            coord.listeners.append(tray.update)
        except (ImportError, ValueError) as e:
            log.warning("tray icon unavailable (%s); running without it", e)

    monitor = Gio.NetworkMonitor.get_default()
    coord.online = monitor.get_network_available()
    monitor.connect("network-changed", lambda _m, available: coord.set_online(available))

    GLib.timeout_add_seconds(max(30, cfg.interval_minutes * 60),
                             lambda: (coord.request("periodic"), True)[1])
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix

        signal_add = GLibUnix.signal_add_full
    except (ImportError, ValueError):
        signal_add = GLib.unix_signal_add
    signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, lambda: (coord.request("signal"), True)[1])
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (loop.quit(), False)[1])

    watcher.start()
    log.info("watching %s <-> %s%s", cfg.local, cfg.remote,
             f" (folder id {cfg.root_folder_id})" if cfg.root_folder_id else "")
    coord.request("startup")
    try:
        loop.run()
    finally:
        watcher.stop()
        cfgmod.PID_FILE.unlink(missing_ok=True)
        log.info("stopped")
    return 0
