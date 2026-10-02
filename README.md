# RcloneDriveManager

Simple app to mount / unmount rclone remote drives using a system tray icon.

Designed for / tested on / probably only works on Linux systems.

## Development Setup & Running

```sh
python3 -m venv env
source env/bin/activate
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
            "mount_args": "--vfs-cache-mode writes"
        }
    }
}
```

Note that remotes must be setup in rclone. The GUI config just determines what pre-setup remote name to mount and how / where.

Mount arguments are split like a shell command line, so quoting works (e.g. `--exclude "My Files/**"`).

## AppImage

The AppImage bundles Python, PySide6 and rclone, so the only host requirement is FUSE 3 (`fusermount3`, usually the `fuse3` package). rclone remotes still come from your normal rclone config (`rclone config`). Use "Start on login" in the tray menu to add an autostart entry pointing at the AppImage.

Build locally (needs `python-appimage` and PySide6 for `compile.py`):

```sh
python -m pip install python-appimage PySide6-Essentials
./packaging/appimage/build.sh
# Output: dist/RcloneDriveManager-<version>-x86_64.AppImage
```

The bundled rclone version is pinned in `packaging/appimage/rclone-version.txt`.

### Releases

- Pushing a `vX.Y.Z` tag that matches `res/version.txt` builds the AppImage and publishes a GitHub release (`.github/workflows/build-appimage.yml`).
- `.github/workflows/rclone-update.yml` runs daily. When rclone publishes a new release, it updates `rclone-version.txt`, bumps the patch version (e.g. 1.1.0 → 1.1.1), tags it and publishes a new AppImage.
