from drivesync import syncer
from drivesync.config import Config
from drivesync.syncer import Outcome


def test_ok():
    assert syncer.classify(0, "INFO  : Bisync successful\n").outcome == Outcome.OK


def test_conflicts_detected():
    out = ("NOTICE: - WARNING  New or changed in both paths       - notes.txt\n"
           "NOTICE: - WARNING  New or changed in both paths        - a/b.doc\n")
    r = syncer.classify(0, out)
    assert r.outcome == Outcome.CONFLICTS
    assert r.conflicts == ["notes.txt", "a/b.doc"]


def test_needs_resync():
    out = "ERROR : Bisync critical error: cannot find prior Path1 or Path2 listings, likely due to critical error on prior run\n"
    assert syncer.classify(2, out).outcome == Outcome.NEEDS_RESYNC


def test_error_message():
    r = syncer.classify(1, "ERROR : Failed to bisync: couldn't connect\n")
    assert r.outcome == Outcome.ERROR
    assert "couldn't connect" in r.message


def test_command_flags():
    cfg = Config(local_path="/tmp/x", remote="gdrive:", root_folder_id="ABC", filters_file="/tmp/f.txt")
    cmd = syncer.build_command(cfg, resync=True)
    assert cmd[:4] == ["rclone", "bisync", "/tmp/x", "gdrive:"]
    assert cmd[cmd.index("--drive-root-folder-id") + 1] == "ABC"
    assert cmd[cmd.index("--resync-mode") + 1] == "newer"
    assert "--drive-skip-gdocs" in cmd


def test_auth_expired():
    out = 'ERROR : couldn\'t fetch token: invalid_grant: maybe token expired? - try refreshing with "rclone config reconnect gdrive:"\n'
    r = syncer.classify(1, out)
    assert r.outcome == Outcome.AUTH_EXPIRED
    assert "drivesync reconnect" in r.message
