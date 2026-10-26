"""Desktop notifications via libnotify."""

import logging

log = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Notify", "0.7")
    from gi.repository import Notify

    _available = Notify.init("DriveSync")
except (ImportError, ValueError):
    _available = False


def send(summary: str, body: str = "", icon: str = "emblem-synchronizing-symbolic") -> None:
    log.info("notify: %s %s", summary, body)
    if not _available:
        return
    try:
        Notify.Notification.new(summary, body, icon).show()
    except Exception as e:  # no notification server (headless, SSH)
        log.debug("notification failed: %s", e)
