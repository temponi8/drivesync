"""Command line interface: drivesync setup | run | sync-now | status | resync | open."""

import argparse
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
from importlib import resources
from pathlib import Path

from . import config as cfgmod
from . import syncer
from .config import Config

UNIT_PATH = Path.home() / ".config/systemd/user/drivesync.service"
FOLDER_URL_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")


def ask(prompt: str, default: str = "") -> str:
    answer = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    return answer or default


def confirm(prompt: str, default: bool = False) -> bool:
    answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def drive_remotes() -> list[str]:
    out = subprocess.run(["rclone", "listremotes", "--long"], capture_output=True, text=True).stdout
    return [line.split(":")[0] for line in out.splitlines() if line.split()[-1:] == ["drive"]]


def dry_run_summary(cfg: Config) -> bool:
    print("\nChecking what the first sync will do (dry run)…")
    result = syncer.run_bisync(cfg, resync=True, dry_run=True)
    if result.outcome == syncer.Outcome.ERROR:
        print(f"Dry run failed: {result.message}\nSee {cfgmod.RCLONE_LOG}")
        return False
    copies = [l for l in result.output.splitlines() if "Skipped copy as --dry-run" in l]
    print(f"{len(copies)} file(s) would be copied.")
    for line in copies[:15]:
        print("  " + line.split("NOTICE:", 1)[-1].split(": Skipped")[0].strip())
    if len(copies) > 15:
        print(f"  … and {len(copies) - 15} more")
    return True


def install_service() -> None:
    exe = shutil.which("drivesync") or f"{sys.executable} -m drivesync"
    template = (resources.files("drivesync") / "drivesync.service").read_text()
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(template.replace("@EXEC@", exe))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "drivesync.service"], check=True)
    print(f"Installed and started {UNIT_PATH}")


def remote_has_client_id(name: str) -> bool:
    out = subprocess.run(["rclone", "config", "show", name], capture_output=True, text=True).stdout
    return any(l.split("=", 1)[1].strip() for l in out.splitlines() if l.startswith("client_id"))


def ask_client_id() -> list[str]:
    """Ask for the user's own Google OAuth client; returns rclone config key=value args."""
    print("\nGoogle is retiring rclone's shared client ID, so you should use your own (free, ~5 minutes):\n"
          "  https://rclone.org/drive/#making-your-own-client-id\n"
          "Leave empty to use the shared one for now (it may stop working at any time).")
    client_id = ask("Client ID (ends with .apps.googleusercontent.com)")
    if not client_id:
        return []
    secret = ask("Client secret")
    return [f"client_id={client_id}", f"client_secret={secret}"]


def choose_drive_folder(name: str, cfg: Config) -> list[str]:
    """Ask until we get a Drive folder we can read; sets cfg.remote/root_folder_id, returns its entries."""
    print("\nWhich folder ON GOOGLE DRIVE should be synced? (You choose the folder on this computer next.)\n"
          "  - Folder under 'Computers' (synced by the Windows app): open it on drive.google.com and\n"
          "    paste the URL from the address bar (https://drive.google.com/drive/folders/...).\n"
          "  - Folder under My Drive: type its path (e.g. 'Work/Notes'), or leave empty for all of My Drive.")
    while True:
        where = ask("Drive folder URL or path")
        if m := FOLDER_URL_RE.search(where):
            cfg.root_folder_id, cfg.remote = m.group(1), f"{name}:"
        elif where.startswith(("/home", "~", "/mnt", "/media")):
            print("That is a path on this computer. Enter the Google Drive folder here; the local folder comes next.")
            continue
        else:
            cfg.root_folder_id, cfg.remote = "", f"{name}:{where.strip('/')}"
        probe = subprocess.run(["rclone", "lsf", cfg.remote, "--max-depth", "1", *syncer.remote_flags(cfg)],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return probe.stdout.splitlines()
        errors = [l for l in probe.stderr.splitlines() if "ERROR" in l]
        print(f"Cannot read that Drive folder: {errors[-1].split('ERROR', 1)[1].lstrip(' :') if errors else probe.stderr.strip()}")


def cmd_setup(_args) -> int:
    if not sys.stdin.isatty():
        print("'drivesync setup' asks questions, so run it in a terminal window "
              "(open Terminal with Ctrl+Alt+T).", file=sys.stderr)
        return 1
    try:
        syncer.check_rclone()
    except syncer.RcloneMissing as e:
        print(f"{e}\nInstall rclone with:\n  sudo -v && curl https://rclone.org/install.sh | sudo bash")
        return 1

    remotes = drive_remotes()
    if remotes:
        name = ask(f"rclone Google Drive remote to use ({', '.join(remotes)})", remotes[0])
    else:
        name = ask("No Google Drive remote configured yet. Name for the new one", "gdrive")
    if name not in remotes:
        client = ask_client_id()
        print("A browser window will open to sign in to Google…")
        # stdout would print the config, including the OAuth token; keep it off the screen.
        subprocess.run(["rclone", "config", "create", name, "drive", "scope=drive", *client],
                       stdout=subprocess.DEVNULL, check=True)
    elif not remote_has_client_id(name):
        print(f"\nThe '{name}' remote uses rclone's shared Google client ID, which Google is retiring in 2026.")
        if client := ask_client_id():
            print("Sign in again with the new client ID (a browser window will open)…")
            # Changing the client ID makes rclone run the browser sign-in itself.
            subprocess.run(["rclone", "config", "update", name, *client], stdout=subprocess.DEVNULL, check=True)

    cfg = cfgmod.load() if cfgmod.CONFIG_FILE.exists() else Config()
    entries = choose_drive_folder(name, cfg)
    print(f"Found {len(entries)} item(s) at the top level: {', '.join(entries[:8])}{' …' if len(entries) > 8 else ''}")

    cfg.local_path = ask("Local folder on this computer", cfg.local_path)
    if cfg.local.resolve().parts[:2] in (("/", "mnt"), ("/", "media")):
        print("Warning: this looks like another disk (e.g. the Windows partition). Syncing a folder that\n"
              "Windows' Drive app also syncs can cause conflicts and missed changes. A folder on the Linux disk\n"
              "is recommended.")
        if not confirm("Use it anyway?"):
            return 1
    cfg.local.mkdir(parents=True, exist_ok=True)
    cfgmod.save(cfg)
    print(f"Saved {cfgmod.CONFIG_FILE}")

    if not dry_run_summary(cfg):
        return 1
    if not confirm("Run the first sync now? Files only on one side are copied to the other; "
                   "where both sides differ, the newer copy wins", True):
        print("Stopped. Run 'drivesync resync' when ready.")
        return 0
    result = syncer.run_bisync(cfg, resync=True)
    print(f"First sync: {result.outcome.value} – {result.message}")
    if result.outcome not in (syncer.Outcome.OK, syncer.Outcome.CONFLICTS):
        return 1

    if confirm("Start DriveSync automatically at login (systemd user service)?", True):
        install_service()
    return 0


def cmd_resync(_args) -> int:
    cfg = cfgmod.load()
    if not dry_run_summary(cfg) or not confirm("Run resync now?", True):
        return 1
    result = syncer.run_bisync(cfg, resync=True)
    print(f"Resync: {result.outcome.value} – {result.message}")
    _signal_daemon()
    return 0 if result.outcome in (syncer.Outcome.OK, syncer.Outcome.CONFLICTS) else 1


def cmd_reconnect(_args) -> int:
    cfg = cfgmod.load()
    name = cfg.remote.split(":", 1)[0]
    print("A browser window will open to sign in to Google again…")
    if subprocess.run(["rclone", "config", "reconnect", f"{name}:"]).returncode != 0:
        return 1
    _signal_daemon()
    print("Signed in. DriveSync will sync now.")
    return 0


def cmd_run(args) -> int:
    from . import daemon

    return daemon.run(use_tray=not args.no_tray)


def _signal_daemon() -> bool:
    from .daemon import read_pid

    pid = read_pid()
    if pid:
        os.kill(pid, signal.SIGUSR1)
    return bool(pid)


def cmd_sync_now(_args) -> int:
    if _signal_daemon():
        print("Sync requested.")
        return 0
    print("DriveSync daemon is not running (start it with: systemctl --user start drivesync)")
    return 1


def cmd_status(_args) -> int:
    from .daemon import read_pid

    if not read_pid():
        print("Daemon: not running")
    try:
        status = json.loads(cfgmod.STATUS_FILE.read_text())
    except FileNotFoundError:
        print("No status yet.")
        return 0
    print(f"State:        {status['state']} – {status['message']}")
    print(f"Last sync:    {status.get('last_sync') or '-'}")
    print(f"Last success: {status.get('last_success') or '-'}")
    if status.get("last_error"):
        print(f"Last error:   {status['last_error']}")
    for c in status.get("conflicts", []):
        print(f"Conflict:     {c}")
    return 0


def cmd_open(_args) -> int:
    subprocess.Popen(["xdg-open", str(cfgmod.load().local)])
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="drivesync", description="Two-way Google Drive folder sync")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="sign in, choose folders, run the first sync").set_defaults(fn=cmd_setup)
    run = sub.add_parser("run", help="run the background daemon")
    run.add_argument("--no-tray", action="store_true", help="run without a tray icon")
    run.set_defaults(fn=cmd_run)
    sub.add_parser("sync-now", help="ask the daemon to sync immediately").set_defaults(fn=cmd_sync_now)
    sub.add_parser("status", help="show sync status").set_defaults(fn=cmd_status)
    sub.add_parser("resync", help="rebuild the sync baseline after an error").set_defaults(fn=cmd_resync)
    sub.add_parser("open", help="open the local folder").set_defaults(fn=cmd_open)
    sub.add_parser("reconnect", help="sign in to Google again").set_defaults(fn=cmd_reconnect)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.fn(args)
    except (FileNotFoundError, syncer.RcloneMissing) as e:
        print(e, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
