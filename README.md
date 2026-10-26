# DriveSync

Two-way sync between a local folder and a Google Drive folder on Ubuntu. It works like the
"Mirror files" mode of Google Drive for desktop on Windows. The transfers are done by
[rclone bisync](https://rclone.org/bisync/). DriveSync adds:

- an automatic sync a few seconds after you change something locally
- a check for changes on Drive every few minutes, to pick up edits from the web or from Windows
- a tray icon (up to date / syncing / paused / offline / conflict / error) with Sync now, Pause, Open folder and View log
- desktop notifications for conflicts and errors
- a systemd user service, so it starts at login

## Install

```bash
sudo apt install -y python3-venv python3-watchdog python3-gi gir1.2-ayatanaappindicator3-0.1 unzip curl
curl -fsSL https://rclone.org/install.sh | sudo bash    # needs rclone >= 1.66 (Ubuntu's apt package is too old)

git clone https://github.com/temponi8/drivesync.git ~/drivesync && cd ~/drivesync
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
.venv/bin/drivesync setup
```

`setup` takes you through these steps:
1. Google sign-in, which runs `rclone config` in your browser.
2. Choosing the Drive folder. For a folder listed under **Computers** (one the Windows app syncs), open it on
   drive.google.com and paste the URL. For a My Drive folder, type its path.
3. Choosing the local folder (default `~/GoogleDrive`).
4. A dry run that shows what the first sync would copy, then the real first sync.
5. Installing the `drivesync` systemd user service.

## Dual boot with Windows

Keep the Ubuntu copy on the Linux disk. Don't point it at the Windows folder on the NTFS partition.
Google Drive is the hub between the two: Windows ↔ Drive ↔ Ubuntu.
Google Docs/Sheets/Slides stay online and are not copied (`skip_gdocs = true`).

## Commands

| Command | |
|---|---|
| `drivesync status` | current state, last sync, conflicts |
| `drivesync sync-now` | ask the running daemon to sync immediately |
| `drivesync resync` | rebuild the sync baseline (when status says `needs_resync`) |
| `rclone config reconnect gdrive:` | sign in again when status says `auth_expired` (every 7 days while the Google app is in Testing) |
| `drivesync open` | open the local folder |
| `drivesync run [--no-tray]` | run the daemon in the foreground |

Service: `systemctl --user status|restart|stop drivesync`, logs: `journalctl --user -u drivesync -f`.

## Files

- `~/.config/drivesync/config.toml`: settings (`local_path`, `remote`, `root_folder_id`, `interval_minutes`, `debounce_seconds`, `skip_gdocs`, `filters_file`)
- `~/.config/drivesync/filters.txt`: exclude rules, created from the [defaults](drivesync/filters.txt) on first run. After editing it, run `drivesync resync`
- `~/.local/state/drivesync/`: `status.json`, `rclone.log`, and the bisync state in `bisync/`

## How conflicts are handled

If a file changed on both sides between two syncs, both versions are kept. They are renamed
`name.conflict1.ext` (local) and `name.conflict2.ext` (Drive). The tray icon shows a warning
until you dismiss it. Merge the two files and delete the one you don't need.

Safety: bisync stops if a sync would delete more than 50% of the files. DriveSync then reports an error
and never runs `--resync` by itself.

## Privacy

DriveSync runs only on your computer and has no server. See the [privacy policy](https://temponi8.github.io/drivesync/privacy.html).

## Tests

```bash
.venv/bin/python -m pytest
```

## License

MIT, see [LICENSE](LICENSE).
