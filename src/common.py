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

import os
import shutil
import subprocess
from typing import Dict, Optional

from PySide6.QtWidgets import QMessageBox, QWidget
from PySide6.QtCore import QFile

APP_TITLE = "RcloneDriveManager"

# Variables an AppImage (or other bundled runtime) may set that must not leak
# into host programs such as systemd-inhibit and fusermount3
_BUNDLE_ENV_VARS = [
    "PYTHONHOME", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE",
    "LD_LIBRARY_PATH", "LD_PRELOAD", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH", "SSL_CERT_FILE", "TCL_LIBRARY", "TK_LIBRARY",
]

_rclone_version: Optional[str] = None


def warn(text: str, detail: str = "", parent: Optional[QWidget] = None):
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
