"""Runs rclone bisync and interprets the result."""

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from . import config as cfgmod
from .config import Config

MIN_RCLONE = (1, 66, 0)


class Outcome(str, Enum):
    OK = "ok"
    CONFLICTS = "conflicts"
    NEEDS_RESYNC = "needs_resync"
    AUTH_EXPIRED = "auth_expired"
    ERROR = "error"


@dataclass
class SyncResult:
    outcome: Outcome
    returncode: int
    conflicts: list[str] = field(default_factory=list)
    message: str = ""
    output: str = ""


class RcloneMissing(RuntimeError):
    pass


def rclone_version() -> tuple[int, int, int]:
    if not shutil.which("rclone"):
        raise RcloneMissing("rclone is not installed")
    out = subprocess.run(["rclone", "version"], capture_output=True, text=True, check=True).stdout
    m = re.search(r"rclone v(\d+)\.(\d+)\.(\d+)", out)
    if not m:
        raise RcloneMissing(f"Could not parse rclone version from: {out[:100]!r}")
    return tuple(int(x) for x in m.groups())


def check_rclone() -> None:
    v = rclone_version()
    if v < MIN_RCLONE:
        raise RcloneMissing(
            f"rclone {'.'.join(map(str, v))} is too old; bisync needs >= "
            f"{'.'.join(map(str, MIN_RCLONE))}. Install the official build from https://rclone.org/downloads/"
        )


def remote_flags(cfg: Config) -> list[str]:
    return ["--drive-root-folder-id", cfg.root_folder_id] if cfg.root_folder_id else []


def build_command(cfg: Config, resync: bool = False, dry_run: bool = False) -> list[str]:
    cmd = [
        "rclone", "bisync", str(cfg.local), cfg.remote,
        "--workdir", str(cfgmod.BISYNC_WORKDIR),
        "--resilient", "--recover", "--max-lock", "2m",
        "--create-empty-src-dirs",
        "--compare", "size,modtime",
        "--conflict-resolve", "none", "--conflict-loser", "num", "--suffix-keep-extension",
        "--filters-file", str(cfg.filters),
        "--log-level", "INFO", "--stats-one-line", "--stats", "0", "--color", "NEVER",
    ]
    if cfg.skip_gdocs:
        cmd.append("--drive-skip-gdocs")
    cmd += remote_flags(cfg)
    if resync:
        # Where a file differs on both sides, keep the newer one instead of letting local win.
        cmd += ["--resync", "--resync-mode", "newer"]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


_CONFLICT_RE = re.compile(r"New or changed in both paths\s*-\s*(.+?)\s*$")
_AUTH_RE = re.compile(r"invalid_grant|token (has been )?expired or revoked|couldn't fetch token", re.I)
_RESYNC_RE = re.compile(r"must run --resync|cannot find prior Path1 or Path2 listings", re.I)


def classify(returncode: int, output: str) -> SyncResult:
    conflicts = []
    for line in output.splitlines():
        m = _CONFLICT_RE.search(line)
        if m and m.group(1) not in conflicts:
            conflicts.append(m.group(1))
    errors = [l for l in output.splitlines() if "ERROR" in l]
    last_error = errors[-1].split("ERROR", 1)[1].lstrip(" :") if errors else ""

    if returncode == 0:
        if conflicts:
            return SyncResult(Outcome.CONFLICTS, 0, conflicts, f"{len(conflicts)} conflict(s), both copies kept", output)
        return SyncResult(Outcome.OK, 0, [], "Up to date", output)
    if _AUTH_RE.search(output):
        return SyncResult(Outcome.AUTH_EXPIRED, returncode, conflicts,
                          "Google sign-in expired. Run: drivesync reconnect", output)
    if _RESYNC_RE.search(output):
        return SyncResult(Outcome.NEEDS_RESYNC, returncode, conflicts,
                          "Sync state lost or inconsistent; run 'drivesync resync'", output)
    return SyncResult(Outcome.ERROR, returncode, conflicts, last_error or f"rclone exited with code {returncode}", output)


def run_bisync(cfg: Config, resync: bool = False, dry_run: bool = False) -> SyncResult:
    cfgmod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    cfgmod.BISYNC_WORKDIR.mkdir(parents=True, exist_ok=True)
    cmd = build_command(cfg, resync=resync, dry_run=dry_run)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return SyncResult(Outcome.ERROR, -1, message="rclone is not installed")
    output = proc.stdout + proc.stderr
    with open(cfgmod.RCLONE_LOG, "a") as log:
        mode = " ".join(f for f in ("--resync", "--dry-run") if f in cmd)
        log.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} bisync {mode}\n")
        log.write(output)
    _trim_log()
    return classify(proc.returncode, output)


def _trim_log(max_bytes: int = 5_000_000) -> None:
    log = cfgmod.RCLONE_LOG
    if log.exists() and log.stat().st_size > max_bytes:
        data = log.read_bytes()[-max_bytes // 2:]
        log.write_bytes(data)
