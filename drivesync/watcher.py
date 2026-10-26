"""Watches the local folder and calls back (debounced) when something changes."""

import fnmatch
import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


def load_exclude_patterns(filters_file: Path) -> list[str]:
    """Basename patterns from '- pattern' lines of an rclone filters file."""
    patterns = []
    for line in filters_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("- "):
            pat = line[2:].strip()
            patterns.append(pat.removesuffix("/**"))
    return patterns


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "FolderWatcher"):
        self.watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed_no_write"):
            return
        if event.is_directory and event.event_type == "modified":
            return
        paths = [event.src_path, getattr(event, "dest_path", "")]
        if all(not p or self.watcher.is_excluded(p) for p in paths):
            return
        self.watcher.notify()


class FolderWatcher:
    """Calls `on_change` once activity has been quiet for `debounce` seconds.

    While `paused_for_sync` is set (rclone is writing into the folder), events are
    only recorded in `changed_during_sync` instead of triggering, so the daemon can
    decide whether a follow-up sync is needed.
    """

    def __init__(self, root: Path, excludes: list[str], debounce: float, on_change: Callable[[], None]):
        self.root = root
        self.excludes = excludes
        self.debounce = debounce
        self.on_change = on_change
        self.paused_for_sync = False
        self.changed_during_sync = False
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._observer = Observer()

    def is_excluded(self, path: str) -> bool:
        p = Path(path)
        try:
            parts = p.relative_to(self.root).parts
        except ValueError:
            return True
        return any(fnmatch.fnmatch(part, pat) for part in parts for pat in self.excludes)

    def notify(self) -> None:
        with self._lock:
            if self.paused_for_sync:
                self.changed_during_sync = True
                return
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self.on_change)
            self._timer.daemon = True
            self._timer.start()

    def begin_sync(self) -> None:
        with self._lock:
            self.paused_for_sync = True
            self.changed_during_sync = False
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def end_sync(self) -> bool:
        """Resume normal watching; returns True if anything changed during the sync."""
        with self._lock:
            self.paused_for_sync = False
            return self.changed_during_sync

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._observer.schedule(_Handler(self), str(self.root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5)
        if self._timer:
            self._timer.cancel()
