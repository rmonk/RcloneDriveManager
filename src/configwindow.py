
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

from PySide6.QtWidgets import QMainWindow, QWidget, QFileDialog
from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent

from typing import List, Optional
from ui_configwindow import Ui_ConfigWindow
from ui_config_list_item import Ui_ConfigListItem
from common import APP_TITLE, warn, app_version, rclone_version, list_remotes, save_config, config_path
import presets
import os


class ConfigListItem(QWidget):
    removed = Signal(QWidget)

    def __init__(self, remotes: List[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ui = Ui_ConfigListItem()
        self.ui.setupUi(self)
        self.ui.btn_remove.clicked.connect(self.__remove)
        self.ui.btn_browse.clicked.connect(self.browse_mountpoint)

        self.ui.cmb_remote.addItems(remotes)
        self.ui.cmb_remote.setCurrentIndex(-1)
        if len(remotes) == 0:
            self.ui.cmb_remote.lineEdit().setPlaceholderText("No rclone remotes found. Run 'rclone config' to add one.")
        else:
            self.ui.cmb_remote.lineEdit().setPlaceholderText("Select or type an rclone remote")

        # Last custom arguments (kept when switching to a preset and back)
        self.custom_args: Optional[str] = None
        # Preset shown before switching to custom (prefills custom when there are no custom args)
        self.preset_id = presets.DEFAULT
        for preset in presets.PRESETS:
            self.ui.cmb_preset.addItem(preset.label, preset.id)
        self.ui.cmb_preset.addItem("Custom", presets.CUSTOM)
        self.ui.cmb_preset.currentIndexChanged.connect(self.preset_changed)
        self.ui.txt_args.textChanged.connect(self.args_edited)
        self.set_preset(presets.DEFAULT)

    def __remove(self):
        self.removed.emit(self)

    def browse_mountpoint(self):
        current = os.path.expanduser(self.ui.txt_mountpoint.text().strip())
        start = os.path.expanduser("~")
        if current != "":
            # Start at the folder, or the closest parent that exists
            while current != "" and not os.path.isdir(current):
                parent = os.path.dirname(current.rstrip("/"))
                if parent == current:
                    break
                current = parent
            if os.path.isdir(current):
                start = current
        path = QFileDialog.getExistingDirectory(self, "Select Mount Point", start)
        if path == "":
            return
        home = os.path.expanduser("~")
        if path == home or path.startswith(home + "/"):
            path = "~" + path[len(home):]
        self.ui.txt_mountpoint.setText(path)

    def current_preset(self) -> str:
        return self.ui.cmb_preset.currentData()

    def set_preset(self, preset_id: str):
        idx = self.ui.cmb_preset.findData(preset_id)
        if idx == self.ui.cmb_preset.currentIndex():
            self.preset_changed(idx)
        else:
            self.ui.cmb_preset.setCurrentIndex(idx)

    def preset_changed(self, idx: int):
        preset_id = self.ui.cmb_preset.itemData(idx)
        if preset_id == presets.CUSTOM:
            if self.custom_args is None:
                # Start from the previously selected preset
                previous = presets.get_preset(self.preset_id)
                self.custom_args = previous.args if previous is not None else ""
            self.ui.txt_args.setReadOnly(False)
            self.ui.txt_args.setToolTip("")
            self.ui.txt_args.setPlainText(self.custom_args)
            self.ui.lbl_preset_desc.setText(presets.CUSTOM_DESCRIPTION)
            self.ui.txt_args.setFocus()
        else:
            preset = presets.get_preset(preset_id)
            self.preset_id = preset_id
            self.ui.txt_args.setReadOnly(True)
            self.ui.txt_args.setToolTip("Select \"Custom\" to edit these options")
            self.ui.txt_args.setPlainText(preset.args)
            self.ui.lbl_preset_desc.setText(preset.description)

    def args_edited(self):
        if self.current_preset() == presets.CUSTOM:
            self.custom_args = self.ui.txt_args.toPlainText()

    def set_values(self, item: dict):
        self.ui.cmb_remote.setCurrentText(item.get("remote_name", ""))
        self.ui.txt_mountpoint.setText(item.get("mount_point", ""))
        args = item.get("mount_args", "")
        preset_id = item.get("mount_preset")
        if preset_id is None:
            # Configs saved before presets existed
            preset_id = presets.match_preset(args)
        elif preset_id != presets.CUSTOM and presets.get_preset(preset_id) is None:
            preset_id = presets.CUSTOM
        self.custom_args = item.get("custom_args")
        if preset_id == presets.CUSTOM and self.custom_args is None:
            self.custom_args = args
        self.set_preset(preset_id)

    def values(self) -> dict:
        item = {}
        item["remote_name"] = self.ui.cmb_remote.currentText().strip().rstrip(":")
        item["mount_point"] = self.ui.txt_mountpoint.text().strip()
        item["mount_preset"] = self.current_preset()
        item["mount_args"] = self.ui.txt_args.toPlainText()
        if self.custom_args is not None:
            item["custom_args"] = self.custom_args
        return item


class ConfigWindow(QMainWindow):
    closed = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ui = Ui_ConfigWindow()
        self.ui.setupUi(self)
        self.list_items = []
        self.remotes: List[str] = []
        self.ui.btn_add.clicked.connect(self.add_config)
        self.cfg_file = config_path()
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
        item = ConfigListItem(self.remotes)
        item.removed.connect(self.remove_config)
        self.ui.sa_main.layout().insertWidget(self.ui.sa_main.layout().count() - 1, item)
        self.list_items.append(item)
        return item

    def remove_config(self, which: ConfigListItem):
        self.ui.sa_main.layout().removeWidget(which)
        self.list_items.remove(which)
        which.hide()
        which.deleteLater()

    def clear_configs(self):
        for cfg_list_item in self.list_items:
            self.ui.sa_main.layout().removeWidget(cfg_list_item)
            # Hide now; deleteLater only runs once control returns to the event loop
            cfg_list_item.hide()
            cfg_list_item.deleteLater()
        self.list_items.clear()

    def show(self, data: dict):
        self.clear_configs()
        # Re-read each time so remotes added with 'rclone config' show up
        self.remotes = list_remotes()
        if "count" in data:
            self.last_data = data
        for i in range(data.get("count", 0)):
            item = self.add_config()
            item.set_values(data["items"][str(i)])
        return super().show()

    def collect_data(self) -> dict:
        data = {}
        data["count"] = len(self.list_items)
        data["items"] = {}
        for i, item in enumerate(self.list_items):
            data["items"][str(i)] = item.values()
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
            save_config(self.cfg_file, data)
            self.last_data = data
            self.closed.emit(data)
        except Exception as e:
            traceback.print_exc()
            warn("Error occurred saving configuration file.",
                 "{} occurred with message {}.".format(type(e).__name__, str(e)), self)
            self.closed.emit(self.last_data)
        return super().closeEvent(event)
