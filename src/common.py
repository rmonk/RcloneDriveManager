# BSD 3-Clause License

# Copyright (c) 2022, Marcus Behel
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.

# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.

# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.

# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# Helpers shared by the tray icon and config window

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import QMessageBox, QWidget
from PySide6.QtCore import QFile, QStandardPaths, QUrl

APP_TITLE = "RcloneDriveManager"

# Variables an AppImage (or other bundled runtime) may set that must not leak
# into host programs such as systemd-inhibit and fusermount3
_BUNDLE_ENV_VARS = [
    "PYTHONHOME", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE",
    "LD_LIBRARY_PATH", "LD_PRELOAD", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH", "SSL_CERT_FILE", "TCL_LIBRARY", "TK_LIBRARY",
]

# Relocates app data and the autostart entry (used by `make run` so test runs can't
# touch an installed copy's configuration or login items)
HOME_OVERRIDE_VAR = "RCLONE_DRIVE_MANAGER_HOME"

# Retries after a failed startup mount or a dropped mount (per mount, overridable in the config)
DEFAULT_RETRIES = 5
DEFAULT_NOTIFY_AFTER_FAILURES = 3
RETRY_SECS = 30

# Longest path a unix socket can bind to (sun_path is 108 bytes including the terminator)
MAX_SOCKET_PATH = 107

_rclone_version: Optional[str] = None
_inhibit_cmd: Optional[str] = None
_inhibit_checked = False


def warn(text: str, detail: str = "", parent: Optional[QWidget] = None):
    # Also log, so problems shown in dialogs end up in the journal
    print("{}: {}{}".format(APP_TITLE, text, " " + detail if detail != "" else ""), file=sys.stderr, flush=True)
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(APP_TITLE)
    dialog.setText(text)
    if detail != "":
        dialog.setDetailedText(detail)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
    dialog.setDefaultButton(QMessageBox.StandardButton.Ok)
    dialog.exec()


def ask(text: str, parent: Optional[QWidget] = None) -> bool:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(APP_TITLE)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Icon.Question)
    dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    dialog.setDefaultButton(QMessageBox.StandardButton.No)
    return dialog.exec() == QMessageBox.StandardButton.Yes


def data_dir() -> str:
    override = os.environ.get(HOME_OVERRIDE_VAR, "")
    if override != "":
        return os.path.join(override, "rclone-drive-manager")
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)


def config_path() -> str:
    return os.path.join(data_dir(), "config.json")


def autostart_path() -> str:
    override = os.environ.get(HOME_OVERRIDE_VAR, "")
    base = os.path.join(override, "autostart") if override != "" else os.path.join(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation), "autostart")
    return os.path.join(base, "rclone-drive-manager.desktop")


def rc_socket_path(name: str) -> Optional[str]:
    """Private unix socket for a mount's rclone remote control, or None if no short enough path exists.
    Named by a hash so long remote names fit, and so separate app data dirs (`make run`) don't collide."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    base = os.path.join(runtime, "rclone-drive-manager") if runtime != "" else os.path.join(data_dir(), "run")
    digest = hashlib.sha1("{}\0{}".format(data_dir(), name).encode()).hexdigest()[:16]
    path = os.path.join(base, "{}.sock".format(digest))
    if len(path.encode()) > MAX_SOCKET_PATH:
        return None
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
        os.chmod(base, 0o700)
    except OSError:
        traceback.print_exc()
        return None
    return path


################################################################################
# File manager bookmarks
################################################################################

def bookmarks_path() -> str:
    """GTK bookmarks file, shown in the sidebar of GNOME Files and GTK file choosers."""
    override = os.environ.get(HOME_OVERRIDE_VAR, "")
    base = override if override != "" else \
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
    # Resolve symlinks (e.g. managed dotfiles) so the atomic write replaces the target, not the link
    return os.path.realpath(os.path.join(base, "gtk-3.0", "bookmarks"))


def _bookmark_uri(path: str) -> str:
    return bytes(QUrl.fromLocalFile(os.path.abspath(path)).toEncoded()).decode()


def _added_bookmarks_path() -> str:
    # Bookmarks this app added, so it never removes one the user made
    return os.path.join(data_dir(), "bookmarks.json")


def _load_added_bookmarks() -> List[str]:
    try:
        with open(_added_bookmarks_path(), "r") as f:
            added = json.load(f)
        return [uri for uri in added if isinstance(uri, str)] if isinstance(added, list) else []
    except (OSError, ValueError):
        return []


def _read_bookmarks() -> List[str]:
    try:
        with open(bookmarks_path(), "r") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def _bookmark_line_uri(line: str) -> str:
    return line.split(" ", 1)[0]


def add_bookmark(path: str, label: str):
    uri = _bookmark_uri(path)
    try:
        lines = _read_bookmarks()
        if any(_bookmark_line_uri(line) == uri for line in lines):
            # Already there (possibly added by the user): leave it alone and don't track it
            return
        lines.append("{} {}".format(uri, label) if label != "" else uri)
        write_atomic(bookmarks_path(), "\n".join(lines) + "\n")
        added = _load_added_bookmarks()
        added.append(uri)
        write_atomic(_added_bookmarks_path(), json.dumps(added))
    except OSError:
        traceback.print_exc()


def remove_bookmark(path: str):
    _remove_bookmark_uri(_bookmark_uri(path))


def _remove_bookmark_uri(uri: str):
    added = _load_added_bookmarks()
    if uri not in added:
        return
    try:
        lines = _read_bookmarks()
        kept = [line for line in lines if _bookmark_line_uri(line) != uri]
        if len(kept) != len(lines):
            write_atomic(bookmarks_path(), "\n".join(kept) + ("\n" if len(kept) > 0 else ""))
        write_atomic(_added_bookmarks_path(), json.dumps([u for u in added if u != uri]))
    except OSError:
        traceback.print_exc()


def remove_stale_bookmarks():
    """Remove bookmarks left behind when the app was killed with drives mounted."""
    for uri in _load_added_bookmarks():
        _remove_bookmark_uri(uri)


def _config_int(item: dict, key: str, default: int, minimum: int) -> int:
    value = item.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        return default
    return value


def retry_settings(item: dict) -> Tuple[int, int]:
    """(retries, notify_after_failures) for a mount, with notify capped at the total attempts."""
    # "startup_retries" is the name used by development builds before retries also covered drops
    retries = _config_int(item, "retries", _config_int(item, "startup_retries", DEFAULT_RETRIES, 0), 0)
    notify_after = _config_int(item, "notify_after_failures", DEFAULT_NOTIFY_AFTER_FAILURES, 1)
    return retries, min(notify_after, retries + 1)


def empty_config() -> dict:
    return {"count": 0, "items": {}}


def write_atomic(path: str, text: str):
    """Write a file atomically so a crash or kill mid-save can't leave it truncated."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".{}-".format(os.path.basename(path)), dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def save_config(path: str, data: dict):
    write_atomic(path, json.dumps(data, indent=4))


def load_config(path: str) -> Tuple[dict, str]:
    """Load the config. Returns (data, problem); problem is "" when the file was fine.
    A missing or empty file is an empty config. An unreadable file is moved aside so it
    can be recovered and is not overwritten by the next save."""
    if not os.path.exists(path):
        return empty_config(), ""
    try:
        with open(path, "r") as f:
            text = f.read()
        if text.strip() == "":
            return empty_config(), ""
        data = json.loads(text)
        if not isinstance(data, dict) or not isinstance(data.get("count"), int) \
                or not isinstance(data.get("items"), dict):
            raise ValueError("Unexpected configuration format")
        return data, ""
    except (OSError, ValueError) as e:
        backup = "{}.corrupt-{}".format(path, time.strftime("%Y%m%d-%H%M%S"))
        try:
            os.replace(path, backup)
            moved = "It was moved to {} and an empty configuration was started.".format(backup)
        except OSError:
            moved = "It could not be moved aside."
        return empty_config(), "{}: {}\n\n{}".format(type(e).__name__, e, moved)


def host_env() -> Dict[str, str]:
    """Environment for launching external programs, without bundled runtime variables."""
    env = dict(os.environ)
    if "APPDIR" in env:
        for var in _BUNDLE_ENV_VARS:
            env.pop(var, None)
        # Drop AppImage directories from PATH so host tools are found first
        appdir = env["APPDIR"]
        paths = [p for p in env.get("PATH", "").split(os.pathsep) if not p.startswith(appdir)]
        env["PATH"] = os.pathsep.join(paths)
    return env


def find_tool(name: str) -> Optional[str]:
    """Find a host program (fusermount3, systemd-inhibit, ...) on PATH."""
    return shutil.which(name, path=host_env().get("PATH"))


def find_rclone() -> Optional[str]:
    """Prefer an rclone bundled in the AppImage, otherwise use the host rclone."""
    appdir = os.environ.get("APPDIR")
    if appdir:
        bundled = os.path.join(appdir, "usr", "bin", "rclone")
        if os.access(bundled, os.X_OK):
            return bundled
    return find_tool("rclone")


def find_inhibit() -> Optional[str]:
    """systemd-inhibit if it is installed and logind is reachable (it isn't in containers / WSL)."""
    global _inhibit_cmd, _inhibit_checked
    if not _inhibit_checked:
        _inhibit_checked = True
        tool = find_tool("systemd-inhibit")
        if tool is not None:
            try:
                res = subprocess.run([tool, "--list"], capture_output=True, timeout=5, env=host_env())
                if res.returncode == 0:
                    _inhibit_cmd = tool
            except (OSError, subprocess.SubprocessError):
                pass
            if _inhibit_cmd is None:
                print("systemd-inhibit is not usable. Mounts will not inhibit sleep.")
    return _inhibit_cmd


def list_remotes() -> List[str]:
    """Names of the remotes in the user's rclone config (without the trailing ':')."""
    rclone = find_rclone()
    if rclone is None:
        return []
    try:
        res = subprocess.run([rclone, "listremotes"], capture_output=True, text=True,
                             timeout=10, env=host_env())
    except (OSError, subprocess.SubprocessError):
        return []
    if res.returncode != 0:
        return []
    return [line.strip().rstrip(":") for line in res.stdout.splitlines() if line.strip() != ""]


def rclone_version() -> str:
    global _rclone_version
    if _rclone_version is None:
        _rclone_version = ""
        rclone = find_rclone()
        if rclone is not None:
            try:
                res = subprocess.run([rclone, "version"], capture_output=True, text=True,
                                     timeout=5, env=host_env())
                first = res.stdout.splitlines()[0] if res.stdout else ""
                _rclone_version = first.replace("rclone", "").strip()
            except (OSError, subprocess.SubprocessError):
                pass
    return _rclone_version


def app_version() -> str:
    version_file = QFile(":/version.txt")
    if version_file.open(QFile.OpenModeFlag.ReadOnly):
        return bytes(version_file.readLine()).strip().decode()
    return ""
