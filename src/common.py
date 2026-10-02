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

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import QMessageBox, QWidget
from PySide6.QtCore import QFile, QStandardPaths

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


def empty_config() -> dict:
    return {"count": 0, "items": {}}


def save_config(path: str, data: dict):
    """Write the config atomically so a crash or kill mid-save can't leave a truncated file."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


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
