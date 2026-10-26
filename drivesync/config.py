"""Configuration and standard paths."""

import os
import tomllib
from dataclasses import dataclass, fields
from importlib import resources
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "drivesync"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "drivesync"
CONFIG_FILE = CONFIG_DIR / "config.toml"
STATUS_FILE = STATE_DIR / "status.json"
PID_FILE = STATE_DIR / "daemon.pid"
RCLONE_LOG = STATE_DIR / "rclone.log"
BISYNC_WORKDIR = STATE_DIR / "bisync"


def default_filters_file() -> Path:
    """The user's filters file, created from the packaged defaults on first use.

    rclone stores a .md5 next to the filters file, so it must live in a writable place.
    """
    user = CONFIG_DIR / "filters.txt"
    if not user.exists():
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_text((resources.files("drivesync") / "filters.txt").read_text())
    return user


@dataclass
class Config:
    local_path: str = str(Path.home() / "GoogleDrive")
    remote: str = "gdrive:"
    root_folder_id: str = ""
    interval_minutes: int = 3
    debounce_seconds: int = 5
    filters_file: str = ""
    skip_gdocs: bool = True

    @property
    def local(self) -> Path:
        return Path(self.local_path).expanduser()

    @property
    def filters(self) -> Path:
        return Path(self.filters_file).expanduser() if self.filters_file else default_filters_file()


def load(path: Path = CONFIG_FILE) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"No config at {path}. Run 'drivesync setup' first.")
    data = tomllib.loads(path.read_text())
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known})


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save(cfg: Config, path: Path = CONFIG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# DriveSync configuration"]
    lines += [f"{f.name} = {_toml_value(getattr(cfg, f.name))}" for f in fields(Config)]
    path.write_text("\n".join(lines) + "\n")
