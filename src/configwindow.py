
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

import traceback

from PySide6.QtWidgets import QMainWindow, QWidget
from PySide6.QtCore import Signal, QStandardPaths
from PySide6.QtGui import QCloseEvent

from typing import Optional
from ui_configwindow import Ui_ConfigWindow
from ui_config_list_item import Ui_ConfigListItem
from common import APP_TITLE, warn, app_version, rclone_version
import json
import os


class ConfigListItem(QWidget):
    removed = Signal(QWidget)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ui = Ui_ConfigListItem()
        self.ui.setupUi(self)
        self.ui.btn_remove.clicked.connect(self.__remove)

    def __remove(self):
        self.removed.emit(self)


class ConfigWindow(QMainWindow):
    closed = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ui = Ui_ConfigWindow()
        self.ui.setupUi(self)
        self.list_items = []
        self.ui.btn_add.clicked.connect(self.add_config)
        self.cfg_file = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) + "/config.json"
        self.last_data = {"count": 0, "items": {}}

        title = APP_TITLE
        version_txt = app_version()
        if version_txt != "":
            title += " - v{0}".format(version_txt)
        rclone_txt = rclone_version()
        if rclone_txt != "":
            title += " (rclone {0})".format(rclone_txt)
        self.setWindowTitle(title)

    def add_config(self):
        item = ConfigListItem()
        # Default args
        item.ui.txt_args.setPlainText("--dir-cache-time 1m0s\n--vfs-cache-mode full")
        item.removed.connect(self.remove_config)
        self.ui.sa_main.layout().insertWidget(self.ui.sa_main.layout().count() - 1, item)
        self.list_items.append(item)
        return item

    def remove_config(self, which: ConfigListItem):
        self.ui.sa_main.layout().removeWidget(which)
        self.list_items.remove(which)
        which.deleteLater()

    def clear_configs(self):
        for cfg_list_item in self.list_items:
            self.ui.sa_main.layout().removeWidget(cfg_list_item)
            cfg_list_item.deleteLater()
        self.list_items.clear()

    def show(self, data: dict):
        self.clear_configs()
        if "count" in data:
            self.last_data = data
        for i in range(data.get("count", 0)):
            item = self.add_config()
            item.ui.txt_remote.setText(data["items"][str(i)]["remote_name"])
            item.ui.txt_mountpoint.setText(data["items"][str(i)]["mount_point"])
            item.ui.txt_args.setPlainText(data["items"][str(i)]["mount_args"])
        return super().show()

    def collect_data(self) -> dict:
        data = {}
        data["count"] = len(self.list_items)
        data["items"] = {}
        for i, item in enumerate(self.list_items):
            data["items"][str(i)] = {}
            data["items"][str(i)]["remote_name"] = item.ui.txt_remote.text().strip()
            data["items"][str(i)]["mount_point"] = item.ui.txt_mountpoint.text().strip()
            data["items"][str(i)]["mount_args"] = item.ui.txt_args.toPlainText()
        return data

    def validate(self, data: dict) -> str:
        names = [data["items"][str(i)]["remote_name"] for i in range(data["count"])]
        if "" in names:
            return "Every configuration needs a remote name."
        dupes = sorted({n for n in names if names.count(n) > 1})
        if len(dupes) > 0:
            return "Remote names must be unique. Duplicated: {}".format(", ".join(dupes))
        return ""

    def closeEvent(self, event: QCloseEvent):
        data = self.collect_data()
        problem = self.validate(data)
        if problem != "":
            warn("Configuration not saved.", problem, self)
            event.ignore()
            return
        try:
            os.makedirs(os.path.dirname(self.cfg_file), exist_ok=True)
            with open(self.cfg_file, "w") as f:
                json.dump(data, f, indent=4)
            self.last_data = data
            self.closed.emit(data)
        except Exception as e:
            traceback.print_exc()
            warn("Error occurred saving configuration file.",
                 "{} occurred with message {}.".format(type(e).__name__, str(e)), self)
            self.closed.emit(self.last_data)
        return super().closeEvent(event)
