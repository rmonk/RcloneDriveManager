
import errno
import re
import shlex
import subprocess
import os
import sys
import time
import traceback
from collections import deque
from typing import Optional, List, Dict, Tuple

from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QWidget, QApplication
from PySide6.QtGui import QIcon, QCursor, QAction
from PySide6.QtCore import QTimer

from configwindow import ConfigWindow
import presets
from common import APP_TITLE, warn, ask, host_env, find_tool, find_rclone, find_inhibit, load_config, \
    data_dir, config_path, autostart_path


# How long to wait for a new mount to appear before assuming rclone is still starting
MOUNT_WAIT_SECS = 5.0


def unescape_mountinfo(path: str) -> str:
    # /proc/self/mountinfo escapes space, tab, newline and backslash as octal
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def is_mounted(path: str) -> bool:
    """Check mountinfo instead of stat so stale FUSE mounts (ENOTCONN) are detected."""
    path = os.path.realpath(path)
    try:
        with open("/proc/self/mountinfo", "r") as f:
            for line in f:
                fields = line.split(" ")
                if len(fields) > 4 and unescape_mountinfo(fields[4]) == path:
                    return True
    except OSError:
        traceback.print_exc()
    return False


def is_stale_mount(path: str) -> bool:
    """A FUSE mount whose daemon has died fails with ENOTCONN."""
    try:
        os.stat(path)
        return False
    except OSError as e:
        return e.errno == errno.ENOTCONN


def fuse_unmount(mountpoint: str, lazy: bool = False) -> bool:
    """Unmount a user FUSE mount, trying fusermount3, fusermount, then umount."""
    candidates = [["fusermount3", "-u"], ["fusermount", "-u"], ["umount"]]
    for cmd in candidates:
        tool = find_tool(cmd[0])
        if tool is None:
            continue
        args = [tool] + cmd[1:]
        if lazy:
            args.append("-z" if cmd[0].startswith("fusermount") else "-l")
        args.append(mountpoint)
        try:
            res = subprocess.run(args, env=host_env(), timeout=5)
            if res.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            traceback.print_exc()
    return False


def log_tail(log_path: str, lines: int = 20) -> str:
    try:
        with open(log_path, "r", errors="replace") as f:
            return "".join(deque(f, lines)).strip()
    except OSError:
        return ""


class TrayIcon(QSystemTrayIcon):
    def __init__(self, config_win: ConfigWindow, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.cfg_file = config_path()
        self.log_dir = os.path.join(data_dir(), "logs")
        self.autostart_file = autostart_path()

        self.config_win = config_win
        self.menu = QMenu()
        self.setContextMenu(self.menu)
        self.poll_timer = QTimer(self)
        # name: (mountpoint, popen, log path)
        self.mounted_list: Dict[str, Tuple[str, subprocess.Popen, str]] = {}
        self.mount_actions: List[QAction] = []
        self.data = {}
        self.data["count"] = 0
        self.data["items"] = {}
        self.construct_menu()
        self.setIcon(QIcon(":/icon.png"))
        self.setToolTip(APP_TITLE)

        self.activated.connect(self.showMenuOnTrigger)

        data, problem = load_config(self.cfg_file)
        if problem != "":
            warn("The configuration file could not be read.", problem)
        self.update_menu(data)

        # Keep autostart entry pointing at the current executable (AppImages move)
        if os.path.exists(self.autostart_file):
            self.write_autostart()

        self.config_win.closed.connect(self.update_menu)
        self.poll_timer.timeout.connect(self.poll_mounted)
        self.poll_timer.setSingleShot(False)
        self.poll_timer.start(5000)

    def showMenuOnTrigger(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.contextMenu().popup(QCursor.pos())

    def update_menu(self, data):
        if "count" not in data or "items" not in data:
            return
        for action in self.mount_actions:
            self.menu.removeAction(action)
            action.deleteLater()
        self.mount_actions.clear()
        self.data = data
        for i in range(self.data["count"]):
            name = self.data["items"][str(i)]["remote_name"]
            # Escape & so it is not treated as a mnemonic
            act = QAction(name.replace("&", "&&"), self.menu)
            act.setData(name)
            act.setCheckable(True)
            act.setChecked(name in self.mounted_list)
            act.triggered.connect(self.toggle_mount)
            self.menu.insertAction(self.sep_2, act)
            self.mount_actions.append(act)

    def construct_menu(self):
        self.lbl_action = self.menu.addAction(APP_TITLE)
        self.sep_1 = self.menu.addSeparator()
        self.sep_2 = self.menu.addSeparator()
        self.autostart_action = self.menu.addAction("Start on login")
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(os.path.exists(self.autostart_file))
        self.autostart_action.triggered.connect(self.toggle_autostart)
        self.quit_action = self.menu.addAction("Quit")
        self.quit_action.triggered.connect(self.exit_app)
        self.lbl_action.triggered.connect(self.open_config)
        self.setContextMenu(self.menu)

    def open_config(self):
        self.config_win.show(self.data)

    def act_for_name(self, name: str) -> Optional[QAction]:
        for act in self.mount_actions:
            if act.data() == name:
                return act
        return None

    def set_checked(self, name: str, checked: bool):
        act = self.act_for_name(name)
        if act is not None:
            act.setChecked(checked)

    ############################################################################
    # Autostart
    ############################################################################

    def autostart_exec(self) -> str:
        appimage = os.environ.get("APPIMAGE")
        if appimage:
            return shlex.quote(appimage)
        main_py = os.path.join(os.path.dirname(os.path.realpath(__file__)), "main.py")
        return "{} {}".format(shlex.quote(sys.executable), shlex.quote(main_py))

    def write_autostart(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.autostart_file), exist_ok=True)
            with open(self.autostart_file, "w") as f:
                f.write("[Desktop Entry]\n")
                f.write("Type=Application\n")
                f.write("Name={}\n".format(APP_TITLE))
                f.write("Comment=Tray icon to mount / unmount rclone remotes.\n")
                f.write("Exec={}\n".format(self.autostart_exec()))
                f.write("Icon=rclone-drive-manager\n")
                f.write("Terminal=false\n")
                f.write("X-GNOME-Autostart-enabled=true\n")
            return True
        except OSError as e:
            traceback.print_exc()
            warn("Failed to enable start on login.", str(e))
            return False

    def toggle_autostart(self):
        if self.autostart_action.isChecked():
            self.autostart_action.setChecked(self.write_autostart())
        else:
            try:
                os.remove(self.autostart_file)
            except FileNotFoundError:
                pass
            except OSError as e:
                warn("Failed to disable start on login.", str(e))
            self.autostart_action.setChecked(os.path.exists(self.autostart_file))

    ############################################################################
    # Mounting
    ############################################################################

    def poll_mounted(self):
        to_delete = []
        for name, (mountpoint, proc, log_path) in self.mounted_list.items():
            res = proc.poll()
            if res is not None:
                to_delete.append((name, mountpoint, res, log_path))
        for name, mountpoint, res, log_path in to_delete:
            del self.mounted_list[name]
            self.set_checked(name, False)
            if is_mounted(mountpoint):
                # rclone died without unmounting. Clear the stale mount.
                fuse_unmount(mountpoint, lazy=True)
            # Remove mount dir when unmounted (only if empty to prevent accidental data loss)
            try:
                os.rmdir(mountpoint)
            except OSError:
                traceback.print_exc()
            if res != 0:
                warn("Drive Unmounted Unexpectedly",
                     "Drive {} was unmounted unexpectedly. Rclone exited with code {}.\n\n{}".format(
                         name, res, log_tail(log_path)))

    def toggle_mount(self):
        act: QAction = self.sender()
        name = act.data()
        if act.isChecked():
            # Don't actually check until mounted
            act.setChecked(False)
            self.mount(name)
        else:
            # Don't actually uncheck until unmounted
            act.setChecked(True)
            self.unmount(name)

    def mount(self, name: str):
        idx = -1
        for i in range(self.data["count"]):
            if self.data["items"][str(i)]["remote_name"] == name:
                idx = i
                break
        if idx == -1:
            warn("Error occurred mounting the drive", "No configuration with the name {} was found.".format(name))
            return

        rclone = find_rclone()
        if rclone is None:
            warn("Error occurred mounting the drive", "rclone was not found. Install rclone and try again.")
            return
        if find_tool("fusermount3") is None and find_tool("fusermount") is None:
            warn("Error occurred mounting the drive",
                 "fusermount3 was not found. Install your distribution's fuse3 package and try again.")
            return

        mountpoint = self.data["items"][str(idx)]["mount_point"]
        if mountpoint == "":
            warn("Error occurred mounting the drive", "No mountpoint was specified.")
            return
        mountpoint = os.path.expandvars(os.path.expanduser(mountpoint))

        if is_mounted(mountpoint):
            if not is_stale_mount(mountpoint):
                # A working mount from another program (e.g. rclone started by hand). Never unmount it.
                warn("Error occurred mounting the drive",
                     "{} is already mounted by another program. Unmount it there first, "
                     "or choose a different mount point.".format(mountpoint))
                return
            if not ask("{} is a broken mount left over from a crash. Clean it up and continue?".format(mountpoint)):
                return
            if not fuse_unmount(mountpoint, lazy=True):
                warn("Error occurred mounting the drive", "Failed to unmount {}.".format(mountpoint))
                return

        try:
            os.makedirs(mountpoint, exist_ok=True)
            if not os.path.isdir(mountpoint):
                warn("Error occurred mounting the drive", "Mountpoint exists, but is not a directory.")
                return
            if len(os.listdir(mountpoint)) != 0:
                warn("Error occurred mounting the drive", "Mountpoint exists, but is a non-empty directory.")
                return
        except OSError as e:
            traceback.print_exc()
            detail = "Mountpoint could not be created or read: {}".format(e)
            if e.errno == errno.ENOTCONN:
                detail += "\nThe mountpoint is a stale mount. Try running: fusermount3 -uz {}".format(mountpoint)
            warn("Error occurred mounting the drive", detail)
            return

        try:
            item = self.data["items"][str(idx)]
            # Presets use their current definition so tuning a preset applies to existing configs
            preset = presets.get_preset(item.get("mount_preset", presets.CUSTOM))
            mount_args = preset.args if preset is not None else item.get("mount_args", "")
            user_args = shlex.split(str(mount_args))
        except ValueError as e:
            warn("Error occurred mounting the drive", "Mount arguments could not be parsed: {}".format(e))
            return

        args = []
        inhibit = find_inhibit()
        if inhibit is not None:
            # Mounted remotes cause some systems to lockup on sleep
            args.append(inhibit)
            args.append("--what=sleep:shutdown")
            args.append("--who={}".format(APP_TITLE))
            args.append("--why={} mounted".format(name))
        args.append(rclone)
        args.append("mount")
        args.extend(user_args)
        args.append("{}:/".format(name))
        args.append(mountpoint)

        print(shlex.join(args))
        log_path = os.path.join(self.log_dir, "{}.log".format(name.replace("/", "_")))
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            with open(log_path, "w") as log:
                p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, env=host_env())
        except OSError as e:
            traceback.print_exc()
            warn("Error occurred mounting the drive", "Failed to start rclone: {}".format(e))
            return

        # Wait for the mount to appear, or for rclone to fail
        start = time.time()
        while time.time() - start < MOUNT_WAIT_SECS:
            if p.poll() is not None:
                try:
                    os.rmdir(mountpoint)
                except OSError:
                    pass
                warn("Error occurred mounting the drive",
                     "Rclone exited with error code {}.\n\n{}".format(p.returncode, log_tail(log_path)))
                return
            if is_mounted(mountpoint):
                break
            QApplication.processEvents()
            time.sleep(0.05)

        self.mounted_list[name] = (mountpoint, p, log_path)
        self.set_checked(name, True)

    def unmount(self, name: str, force: bool = False, noprompt: bool = False) -> bool:
        # Don't check this process in poll_mounted anymore
        mountpoint, proc, log_path = self.mounted_list.pop(name)

        # If process is already dead, just clean up
        if proc.poll() is not None:
            if is_mounted(mountpoint):
                fuse_unmount(mountpoint, lazy=True)
            self.set_checked(name, False)
            return True

        # Try clean unmount
        # Try up to 3 times with 100ms delay between
        ok = False
        for i in range(3):
            if fuse_unmount(mountpoint):
                ok = True
                break
            time.sleep(0.1)

        # Force unmount by killing rclone if clean unmount failed
        if not ok:
            if force:
                proc.terminate()
                try:
                    proc.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                if is_mounted(mountpoint):
                    fuse_unmount(mountpoint, lazy=True)
            else:
                # Unmount failed. Add back to list
                self.mounted_list[name] = (mountpoint, proc, log_path)
                if not noprompt:
                    warn("Unmount failed",
                         "Failed to cleanly unmount {}. Files may be in use.".format(name))
                return False
        else:
            # rclone exits once its mount is gone
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()

        # Remove mount dir when unmounted (only if empty to prevent accidental data loss)
        try:
            os.rmdir(mountpoint)
        except OSError:
            traceback.print_exc()

        self.set_checked(name, False)
        return True

    def exit_app(self):
        if ask("Are you sure you want to quit? QUITTING WILL UNMOUNT ALL DRIVES!"):
            # Unmount all
            self.poll_timer.stop()
            for name in list(self.mounted_list.keys()):
                if not self.unmount(name, False, True):
                    if not ask("Failed to cleanly umount {}. Force unmount? If no is selected, "
                               "the application will not exit.".format(name)):
                        # Exit was aborted. Restart poll timer
                        self.poll_timer.start(5000)
                        return
                    # Force unmount
                    self.unmount(name, True, True)
            QApplication.instance().quit()
