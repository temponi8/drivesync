from pathlib import Path

from importlib import resources
from drivesync.watcher import FolderWatcher, load_exclude_patterns


def test_excludes():
    w = FolderWatcher(Path("/r"), load_exclude_patterns(Path(str(resources.files("drivesync") / "filters.txt"))), 1, lambda: None)
    assert w.is_excluded("/r/doc/.~lock.report.odt#")
    assert w.is_excluded("/r/.Trash-1000/files/x")
    assert w.is_excluded("/r/a/file.txt.swp")
    assert not w.is_excluded("/r/a/report.odt")
    assert w.is_excluded("/elsewhere/file")
