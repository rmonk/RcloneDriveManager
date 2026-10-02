# RcloneDriveManager

Simple app to mount / unmount rclone remote drives using a system tray icon.

Designed for / tested on / probably only works on Linux systems.

## Development Setup & Running

The Makefile sets up a virtualenv (`.venv`, with PySide6 and python-appimage) on first use:

```sh
make run           # run from source
make check         # byte-compile + offscreen startup smoke test
make appimage      # build dist/RcloneDriveManager-<version>-x86_64.AppImage
make run-appimage  # build if anything changed, then run the AppImage
make help          # all targets
```

`make run` and `make run-appimage` keep the app's data (`config.json`, logs) in `.dev-data/` so testing doesn't touch the configuration of an installed copy; your rclone remotes are still used. Pass `DATA_DIR=` to use the real app data. Quit an installed copy first if you test mounts, so two instances don't mount the same drive.

Without make:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U PySide6
python compile.py
python src/main.py
```

## Packaging and Running

- Change version if needed with `./change_version.sh X.Y.Z` (updates `res/version.txt`, `packaging/deb_control` and `packaging/rpm.spec`)

```sh
python compile.py
cd packaging
./ubuntu.sh
./fedora.sh
```

`compile.py` uses `pyside6-uic` / `pyside6-rcc` when installed by pip, and falls back to the `uic` / `rcc` tools shipped inside the PySide6 package (e.g. Fedora's `python3-pyside6`).

The host needs `rclone` and `fuse3` (`fusermount3`) installed. rclone output for each mount is logged to `~/.local/share/rclone-drive-manager/logs/<remote>.log`.

## GUI Config file example

*Can configure via gui, but file can be backed up / copied as needed.*

`~/.local/share/rclone-drive-manager/config.json`

```
{
    "count": 1,
    "items": {
        "0": {
            "remote_name": "OneDrive",
            "mount_point": "~/OneDrive",
            "mount_preset": "general",
            "mount_args": "--vfs-cache-mode full\n--vfs-cache-max-size 10G\n--vfs-cache-max-age 24h\n--vfs-cache-min-free-space 5G"
        }
    }
}
```

The remote can be picked from the remotes in your rclone config (`rclone listremotes`) or typed in.

### Mount option presets

| Preset | Use it for | Main options |
|---|---|---|
| General use (recommended) | Everyday files; any app can open, edit and save in place | `--vfs-cache-mode full`, cache up to 10G / 24h, keep 5G disk free |
| Media streaming | Video and music players, Plex / Jellyfin | `full` cache up to 50G / 1 week, `--vfs-read-ahead 512M`, `--buffer-size 64M`, `--dir-cache-time 1h` |
| Light (low disk use) | Small disks; files are read straight from the remote | `--vfs-cache-mode writes`, cache up to 1G |
| Read-only browsing | Backups, archives, photo libraries | `--read-only`, `full` cache up to 5G |
| S3 / object storage | S3, Swift, B2 and similar | `--vfs-fast-fingerprint --no-modtime --vfs-read-chunk-size 4M --vfs-read-chunk-streams 16` |
| Custom | Anything else | Your own flags |

Choosing "Custom" makes the arguments editable, starting from the previously selected preset, or from your last custom arguments if you have saved some. Presets are applied from their current definition when mounting, so improvements in new versions apply to existing configurations. See the [rclone mount documentation](https://rclone.org/commands/rclone_mount/#vfs-file-caching) for what each option does.

Note that remotes must be setup in rclone. The GUI config just determines what pre-setup remote name to mount and how / where.

Mount arguments are split like a shell command line, so quoting works (e.g. `--exclude "My Files/**"`).

## AppImage

The AppImage bundles Python, PySide6 and rclone. Host requirements: glibc 2.34 or newer (Ubuntu 22.04+, Debian 12+, Fedora 35+, RHEL 9+) and FUSE 3 (`fusermount3`, usually the `fuse3` package), which both the AppImage itself and `rclone mount` need. rclone remotes still come from your normal rclone config (`rclone config`). Use "Start on login" in the tray menu to add an autostart entry pointing at the AppImage.

Build locally (needs `python-appimage` and PySide6 for `compile.py`):

```sh
python -m pip install python-appimage PySide6-Essentials
./packaging/appimage/build.sh
# Output: dist/RcloneDriveManager-<version>-x86_64.AppImage
```

The bundled rclone version is pinned in `packaging/appimage/rclone-version.txt`.

### Updates

Release AppImages embed update information (`gh-releases-zsync`) and each release publishes a matching `.zsync` file, so AppImage managers can update them in place. [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) is recommended: add the AppImage to Gear Lever and it picks up the GitHub release source automatically. AppImageUpdate / `appimageupdatetool` also work and only download changed blocks.

Local builds point at `rmonk/RcloneDriveManager` releases unless `UPDATE_REPO=owner/repo` is set (CI uses the repository it runs in).

### Releases

- Pushing a `vX.Y.Z` tag that matches `res/version.txt` builds the AppImage and publishes a GitHub release (`.github/workflows/build-appimage.yml`).
- `.github/workflows/rclone-update.yml` runs daily. When rclone publishes a new release, it updates `rclone-version.txt`, bumps the patch version (e.g. 1.1.0 → 1.1.1), tags it and publishes a new AppImage.
