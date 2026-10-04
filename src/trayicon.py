
import errno
import http.client
import json
import re
import shlex
import subprocess
import os
import socket
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Dict, Set, Tuple

from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QWidget, QApplication, QMessageBox, QProgressDialog
from PySide6.QtGui import QIcon, QCursor
from PySide6.QtCore import QTimer

from configwindow import ConfigWindow
import presets
from common import APP_TITLE, warn, ask, host_env, find_tool, find_rclone, find_inhibit, load_config, \
    data_dir, config_path, autostart_path, retry_settings, rc_socket_path, add_bookmark, remove_bookmark, \
    remove_stale_bookmarks, RETRY_SECS
from network import NetworkMonitor


# How long to wait for a new mount to appear before assuming rclone is still starting
MOUNT_WAIT_SECS = 5.0
# A recovery mount whose rclone exits within this time counts as a failed attempt
CONFIRM_SECS = 30.0
# How long to wait for rclone's remote control to answer
RC_TIMEOUT_SECS = 0.5


class MountError(Exception):
    def __init__(self, detail: str, retry: bool = True):
        super().__init__(detail)
        self.detail = detail
        # False for problems another attempt can't fix (bad config, missing programs)
        self.retry = retry


def summarize_error(detail: str) -> str:
    """First line of an error plus its last line (usually rclone's final log line), for notifications."""
    lines = [line.strip() for line in detail.splitlines() if line.strip() != ""]
    if len(lines) == 0:
        return ""
    summary = lines[0] if len(lines) == 1 else "{}\n{}".format(lines[0], lines[-1])
    return summary if len(summary) <= 300 else summary[:297] + "..."


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


@dataclass
class Mount:
    mountpoint: str
    proc: subprocess.Popen
    log_path: str
    # rclone remote control socket, None if not enabled
    rc_socket: Optional[str]


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def rc_call(socket_path: str, method: str) -> Optional[dict]:
    """Call an rclone remote control method. None if rclone isn't answering."""
    conn = UnixHTTPConnection(socket_path, RC_TIMEOUT_SECS)
    try:
        conn.request("POST", "/" + method, body="{}", headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        body = res.read()
        if res.status != 200:
            return None
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def upload_counts(mount: Mount) -> Optional[Tuple[int, int]]:
    """(uploading, queued) files in a mount's VFS cache. None without a disk cache or rc socket."""
    if mount.rc_socket is None:
        return None
    stats = rc_call(mount.rc_socket, "vfs/stats")
    cache = stats.get("diskCache") if stats is not None else None
    if not isinstance(cache, dict):
        return None
    in_progress = cache.get("uploadsInProgress", 0)
    queued = cache.get("uploadsQueued", 0)
    if not isinstance(in_progress, int) or not isinstance(queued, int):
        return None
    return in_progress, queued


def shorten(text: str, length: int) -> str:
    return text if len(text) <= length else text[:length - 1] + "…"


def plural(count: int, word: str) -> str:
    return "{} {}{}".format(count, word, "" if count == 1 else "s")


class RemoteMenu:
    """Tray submenu for one configured remote."""

    def __init__(self, tray: "TrayIcon", name: str):
        # Escape & so it is not treated as a mnemonic
        self.label = name.replace("&", "&&")
        self.menu = QMenu(self.label, tray.menu)
        self.status = self.menu.addAction("")
        self.status.setEnabled(False)
        self.menu.addSeparator()
        self.toggle = self.menu.addAction("Mount")
        self.toggle.triggered.connect(lambda: tray.toggle_mount(name))
        self.open = self.menu.addAction("Open folder")
        self.open.triggered.connect(lambda: tray.open_folder(name))
        self.log = self.menu.addAction("View log")
        self.log.triggered.connect(lambda: tray.open_log(name))


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
        self.mounted_list: Dict[str, Mount] = {}
        # Names currently inside start_mount (it processes events while waiting)
        self.mounting: Set[str] = set()
        self.remote_menus: Dict[str, RemoteMenu] = {}
        # Pending VFS uploads per mounted remote, refreshed by poll_mounted
        self.uploads: Dict[str, int] = {}

        # Recovery: mounts being retried after failing on startup or dropping.
        # name -> failed attempts so far
        self.recovery_failures: Dict[str, int] = {}
        # name -> "startup" or "drop"
        self.recovery_reason: Dict[str, str] = {}
        # Mounts that started but may still fail: name -> start time
        self.recovery_pending: Dict[str, float] = {}
        # Mounts whose failures have been notified
        self.recovery_notified: Set[str] = set()
        # Scheduled retries
        self.retry_timers: Dict[str, QTimer] = {}
        # Failed while offline; retried when the network comes back
        self.waiting_for_network: Set[str] = set()
        # Mounts that gave up: name -> short reason, shown until the next mount attempt
        self.gave_up: Dict[str, str] = {}

        self.network = NetworkMonitor(self)
        self.network.online.connect(self.network_online)

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

        # Nothing is mounted yet, so any bookmarks of ours are left over from a crash
        remove_stale_bookmarks()

        self.config_win.closed.connect(self.update_menu)
        self.poll_timer.timeout.connect(self.poll_mounted)
        self.poll_timer.setSingleShot(False)
        self.poll_timer.start(5000)

        # After the event loop starts, so the tray icon is shown before any mount errors
        QTimer.singleShot(0, self.mount_startup_items)

    def showMenuOnTrigger(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.contextMenu().popup(QCursor.pos())

    def update_menu(self, data):
        if "count" not in data or "items" not in data:
            return
        for remote_menu in self.remote_menus.values():
            self.menu.removeAction(remote_menu.menu.menuAction())
            remote_menu.menu.deleteLater()
        self.remote_menus.clear()
        self.data = data
        for i in range(self.data["count"]):
            name = self.data["items"][str(i)]["remote_name"]
            remote_menu = RemoteMenu(self, name)
            self.menu.insertMenu(self.sep_remotes, remote_menu.menu)
            self.remote_menus[name] = remote_menu
        self.refresh_menu_state()

    def construct_menu(self):
        self.lbl_action = self.menu.addAction(APP_TITLE)
        self.sep_1 = self.menu.addSeparator()
        self.sep_remotes = self.menu.addSeparator()
        self.mount_all_action = self.menu.addAction("Mount all")
        self.mount_all_action.triggered.connect(self.mount_all)
        self.unmount_all_action = self.menu.addAction("Unmount all")
        self.unmount_all_action.triggered.connect(self.unmount_all)
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

    def remote_status(self, name: str) -> Tuple[str, str]:
        """(menu title prefix, status line) for a remote."""
        if name in self.mounted_list:
            status = "Mounted at {}".format(self.mounted_list[name].mountpoint)
            if self.uploads.get(name, 0) > 0:
                status += "\nUploading {}".format(plural(self.uploads[name], "file"))
            return "✓", status
        if name in self.recovery_failures:
            if name in self.waiting_for_network:
                return "…", "Waiting for the network to reconnect"
            item = self.config_item(name)
            retries, _ = retry_settings(item if item is not None else {})
            verb = "Reconnecting" if self.recovery_reason.get(name) == "drop" else "Mounting"
            return "…", "{}, attempt {} of {}".format(verb, self.recovery_failures[name] + 1, retries + 1)
        if name in self.gave_up:
            return "!", "Could not mount: {}".format(self.gave_up[name])
        return "", "Not mounted"

    def refresh_menu_state(self):
        mounted = 0
        tooltip = []
        for name, remote_menu in self.remote_menus.items():
            is_mounted_now = name in self.mounted_list
            mounted += 1 if is_mounted_now else 0
            prefix, status = self.remote_status(name)
            remote_menu.menu.setTitle("{} {}".format(prefix, remote_menu.label) if prefix != "" else remote_menu.label)
            # Menu items show a single short line; the tooltip gets a little more
            remote_menu.status.setText(shorten(status.replace("\n", " · "), 80).replace("&", "&&"))
            remote_menu.toggle.setText("Unmount" if is_mounted_now else
                                       "Mount now" if name in self.recovery_failures else "Mount")
            remote_menu.open.setEnabled(is_mounted_now)
            remote_menu.log.setEnabled(os.path.exists(self.log_path(name)))
            if prefix != "" and (not is_mounted_now or self.uploads.get(name, 0) > 0):
                tooltip.append(shorten("{}: {}".format(name, status.split("\n")[-1]), 120))
        self.mount_all_action.setEnabled(mounted < len(self.remote_menus))
        self.unmount_all_action.setEnabled(len(self.mounted_list) > 0)
        lines = ["{}: {} of {} mounted".format(APP_TITLE, mounted, len(self.remote_menus))] + tooltip
        self.setToolTip("\n".join(lines))

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

    def config_item(self, name: str) -> Optional[dict]:
        for i in range(self.data["count"]):
            item = self.data["items"][str(i)]
            if item.get("remote_name") == name:
                return item
        return None

    def log_path(self, name: str) -> str:
        return os.path.join(self.log_dir, "{}.log".format(name.replace("/", "_")))

    def poll_mounted(self):
        exited = []
        for name, mount in self.mounted_list.items():
            res = mount.proc.poll()
            if res is not None:
                exited.append((name, mount, res))
        exited_names = [name for name, _, _ in exited]
        now = time.time()
        for name, started in list(self.recovery_pending.items()):
            if name in self.mounted_list and name not in exited_names and now - started >= CONFIRM_SECS:
                self.recover_succeeded(name)
        for name, mount, res in exited:
            self.handle_exit(name, mount, res)
        self.uploads.clear()
        for name, mount in self.mounted_list.items():
            counts = upload_counts(mount)
            if counts is not None and sum(counts) > 0:
                self.uploads[name] = sum(counts)
        self.refresh_menu_state()

    def handle_exit(self, name: str, mount: Mount, res: int):
        """rclone exited without unmount() being asked to stop it."""
        del self.mounted_list[name]
        if is_mounted(mount.mountpoint):
            # rclone died without unmounting. Clear the stale mount.
            fuse_unmount(mount.mountpoint, lazy=True)
        self.cleanup_after_unmount(name, mount)
        error = MountError("Rclone exited with error code {}.\n\n{}".format(res, log_tail(mount.log_path)))
        if name in self.recovery_pending:
            # rclone gave up shortly after a recovery mount: count it as a failed attempt
            del self.recovery_pending[name]
            self.recover_failed(name, error)
            return
        if res == 0:
            # Unmounted from outside the app (e.g. fusermount -u): that was deliberate
            return
        item = self.config_item(name)
        if item is not None and item.get("auto_remount", True) is True:
            print("{}: {} dropped (rclone exited with code {}), remounting".format(APP_TITLE, name, res),
                  file=sys.stderr, flush=True)
            self.start_recovery(name, "drop")
        else:
            warn("Drive Unmounted Unexpectedly",
                 "Drive {} was unmounted unexpectedly. Rclone exited with code {}.\n\n{}".format(
                     name, res, log_tail(mount.log_path)))

    def cleanup_after_unmount(self, name: str, mount: Mount):
        # Remove mount dir when unmounted (only if empty to prevent accidental data loss)
        try:
            os.rmdir(mount.mountpoint)
        except OSError:
            traceback.print_exc()
        remove_bookmark(mount.mountpoint)
        if mount.rc_socket is not None:
            try:
                os.remove(mount.rc_socket)
            except OSError:
                pass
        self.uploads.pop(name, None)

    ############################################################################
    # Recovery (startup mounts and dropped mounts)
    ############################################################################

    def mount_startup_items(self):
        for i in range(self.data["count"]):
            item = self.data["items"][str(i)]
            name = item.get("remote_name", "")
            if item.get("mount_on_startup", False) is True and name != "" and name not in self.mounted_list:
                self.start_recovery(name, "startup")

    def start_recovery(self, name: str, reason: str):
        self.end_recovery(name)
        self.gave_up.pop(name, None)
        self.recovery_failures[name] = 0
        self.recovery_reason[name] = reason
        self.recover_attempt(name)

    def recovery_wanted(self, name: str) -> bool:
        item = self.config_item(name)
        if item is None or name in self.mounted_list:
            return False
        if self.recovery_reason.get(name) == "drop":
            return item.get("auto_remount", True) is True
        return item.get("mount_on_startup", False) is True

    def recover_attempt(self, name: str):
        timer = self.retry_timers.pop(name, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if name not in self.recovery_failures or name in self.mounting or name in self.recovery_pending:
            # Cancelled (e.g. mounted or unmounted from the menu meanwhile), or already under way
            return
        if not self.recovery_wanted(name):
            self.end_recovery(name)
            return
        self.waiting_for_network.discard(name)
        try:
            self.start_mount(name, interactive=False)
        except MountError as e:
            self.recover_failed(name, e)
            return
        finally:
            self.refresh_menu_state()
        if name in self.mounted_list:
            # poll_mounted confirms the mount, or counts rclone exiting early as a failure
            self.recovery_pending[name] = time.time()

    def recover_failed(self, name: str, error: MountError):
        if name not in self.recovery_failures:
            return
        if error.retry and not self.network.is_online():
            # Don't use up retries while offline; network_online() retries straight away
            print("{}: mounting {} failed while offline, waiting for the network: {}".format(
                APP_TITLE, name, error.detail), file=sys.stderr, flush=True)
            self.waiting_for_network.add(name)
            self.refresh_menu_state()
            return
        failures = self.recovery_failures[name] + 1
        self.recovery_failures[name] = failures
        item = self.config_item(name)
        retries, notify_after = retry_settings(item if item is not None else {})
        attempts = retries + 1
        give_up = not error.retry or failures >= attempts
        drop = self.recovery_reason.get(name) == "drop"
        print("{}: {} {} failed (attempt {} of {}): {}".format(
            APP_TITLE, "remounting" if drop else "mounting", name, failures, attempts, error.detail),
            file=sys.stderr, flush=True)

        title = "{} {}".format("Could not reconnect" if drop else "Could not mount", name)
        reason = summarize_error(error.detail)
        if not error.retry:
            self.notify(title, reason)
        elif give_up:
            self.notify(title, "Gave up after {}. Mount it from the tray menu once the problem is fixed."
                               "\n{}".format(plural(failures, "attempt"), reason))
        elif failures == notify_after:
            self.recovery_notified.add(name)
            self.notify(title, "{} failed. Retrying every {} seconds, {} more.\n{}".format(
                plural(failures, "attempt"), RETRY_SECS, plural(attempts - failures, "time"), reason))

        if give_up:
            self.end_recovery(name)
            self.gave_up[name] = reason.split("\n")[-1]
        else:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self.recover_attempt(name))
            timer.start(RETRY_SECS * 1000)
            self.retry_timers[name] = timer
        self.refresh_menu_state()

    def recover_succeeded(self, name: str):
        if name in self.recovery_notified:
            self.notify("{} {}".format("Reconnected" if self.recovery_reason.get(name) == "drop" else "Mounted",
                                       name),
                        "Mounted after {}.".format(plural(self.recovery_failures.get(name, 0), "failed attempt")),
                        QSystemTrayIcon.MessageIcon.Information)
        self.end_recovery(name)

    def end_recovery(self, name: str):
        self.recovery_failures.pop(name, None)
        self.recovery_reason.pop(name, None)
        self.recovery_pending.pop(name, None)
        self.recovery_notified.discard(name)
        self.waiting_for_network.discard(name)
        timer = self.retry_timers.pop(name, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def network_online(self):
        print("{}: network is online".format(APP_TITLE), file=sys.stderr, flush=True)
        for name in list(self.recovery_failures.keys()):
            if name in self.waiting_for_network or name in self.retry_timers:
                self.recover_attempt(name)

    def notify(self, title: str, message: str,
               icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Warning):
        print("{}: {}: {}".format(APP_TITLE, title, message), file=sys.stderr, flush=True)
        self.showMessage(title, message, icon, 15000)

    ############################################################################
    # Menu actions
    ############################################################################

    def toggle_mount(self, name: str):
        # A manual mount or unmount takes over from retries
        self.end_recovery(name)
        self.gave_up.pop(name, None)
        if name in self.mounted_list:
            self.request_unmount([name])
        else:
            self.mount(name)
        self.refresh_menu_state()

    def mount_all(self):
        for name in list(self.remote_menus.keys()):
            if name not in self.mounted_list:
                self.end_recovery(name)
                self.gave_up.pop(name, None)
                self.mount(name)
        self.refresh_menu_state()

    def unmount_all(self):
        for name in list(self.mounted_list.keys()):
            self.end_recovery(name)
        self.request_unmount(list(self.mounted_list.keys()))

    def open_path(self, path: str):
        xdg_open = find_tool("xdg-open")
        if xdg_open is None:
            warn("Could not open {}".format(path), "xdg-open was not found.")
            return
        try:
            subprocess.Popen([xdg_open, path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=host_env(), start_new_session=True)
        except OSError as e:
            warn("Could not open {}".format(path), str(e))

    def open_folder(self, name: str):
        if name in self.mounted_list:
            self.open_path(self.mounted_list[name].mountpoint)

    def open_log(self, name: str):
        self.open_path(self.log_path(name))

    def mount(self, name: str):
        try:
            self.start_mount(name, interactive=True)
        except MountError as e:
            warn("Error occurred mounting the drive", e.detail)

    def start_mount(self, name: str, interactive: bool):
        """Start rclone for a configured remote. Raises MountError on failure."""
        if name in self.mounting or name in self.mounted_list:
            return
        self.mounting.add(name)
        try:
            self._start_mount(name, interactive)
        finally:
            self.mounting.discard(name)

    def _start_mount(self, name: str, interactive: bool):
        idx = -1
        for i in range(self.data["count"]):
            if self.data["items"][str(i)]["remote_name"] == name:
                idx = i
                break
        if idx == -1:
            raise MountError("No configuration with the name {} was found.".format(name), retry=False)

        rclone = find_rclone()
        if rclone is None:
            raise MountError("rclone was not found. Install rclone and try again.", retry=False)
        if find_tool("fusermount3") is None and find_tool("fusermount") is None:
            raise MountError("fusermount3 was not found. Install your distribution's fuse3 package and try again.",
                             retry=False)

        mountpoint = self.data["items"][str(idx)]["mount_point"]
        if mountpoint == "":
            raise MountError("No mountpoint was specified.", retry=False)
        mountpoint = os.path.expandvars(os.path.expanduser(mountpoint))

        if is_mounted(mountpoint):
            if not is_stale_mount(mountpoint):
                # A working mount from another program (e.g. rclone started by hand). Never unmount it.
                raise MountError("{} is already mounted by another program. Unmount it there first, "
                                 "or choose a different mount point.".format(mountpoint), retry=False)
            # Unattended (startup) mounts clean up without asking: a dead FUSE mount holds no data
            if interactive and not ask("{} is a broken mount left over from a crash. "
                                       "Clean it up and continue?".format(mountpoint)):
                return
            if not fuse_unmount(mountpoint, lazy=True):
                raise MountError("Failed to unmount {}.".format(mountpoint))

        try:
            os.makedirs(mountpoint, exist_ok=True)
            if not os.path.isdir(mountpoint):
                raise MountError("Mountpoint exists, but is not a directory.", retry=False)
            if len(os.listdir(mountpoint)) != 0:
                raise MountError("Mountpoint exists, but is a non-empty directory.", retry=False)
        except OSError as e:
            traceback.print_exc()
            detail = "Mountpoint could not be created or read: {}".format(e)
            if e.errno == errno.ENOTCONN:
                detail += "\nThe mountpoint is a stale mount. Try running: fusermount3 -uz {}".format(mountpoint)
            raise MountError(detail)

        try:
            item = self.data["items"][str(idx)]
            # Presets use their current definition so tuning a preset applies to existing configs
            preset = presets.get_preset(item.get("mount_preset", presets.CUSTOM))
            mount_args = preset.args if preset is not None else item.get("mount_args", "")
            user_args = shlex.split(str(mount_args))
            inhibit_sleep = item.get("inhibit_sleep", False) is True
        except ValueError as e:
            raise MountError("Mount arguments could not be parsed: {}".format(e), retry=False)

        args = []
        inhibit = find_inhibit() if inhibit_sleep else None
        if inhibit is not None:
            # Opt-in: mounted remotes cause some systems to lockup on sleep
            args.append(inhibit)
            args.append("--what=sleep:shutdown")
            args.append("--who={}".format(APP_TITLE))
            args.append("--why={} mounted".format(name))
        args.append(rclone)
        args.append("mount")
        args.extend(user_args)
        # Remote control on a private socket, used to see pending uploads. Skipped if the user configured rc.
        rc_socket = None
        if not any(arg.startswith("--rc") for arg in user_args):
            rc_socket = rc_socket_path(name)
        if rc_socket is not None:
            try:
                # rclone leaves its socket behind if it is killed, and can't bind over it
                os.remove(rc_socket)
            except FileNotFoundError:
                pass
            except OSError:
                traceback.print_exc()
            args.extend(["--rc", "--rc-addr", "unix://{}".format(rc_socket)])
        args.append("{}:/".format(name))
        args.append(mountpoint)

        print(shlex.join(args))
        log_path = self.log_path(name)
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            if os.path.exists(log_path):
                # Keep the previous run's log, e.g. from before a drop
                os.replace(log_path, log_path + ".1")
            with open(log_path, "w") as log:
                p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, env=host_env())
        except OSError as e:
            traceback.print_exc()
            raise MountError("Failed to start rclone: {}".format(e))

        # Wait for the mount to appear, or for rclone to fail
        start = time.time()
        while time.time() - start < MOUNT_WAIT_SECS:
            if p.poll() is not None:
                try:
                    os.rmdir(mountpoint)
                except OSError:
                    pass
                raise MountError("Rclone exited with error code {}.\n\n{}".format(p.returncode, log_tail(log_path)))
            if is_mounted(mountpoint):
                break
            QApplication.processEvents()
            time.sleep(0.05)

        self.mounted_list[name] = Mount(mountpoint, p, log_path, rc_socket)
        self.gave_up.pop(name, None)
        if item.get("bookmark", False) is True:
            add_bookmark(mountpoint, name)

    def unmount(self, name: str, force: bool = False, noprompt: bool = False) -> bool:
        # Don't check this process in poll_mounted anymore
        mount = self.mounted_list.pop(name)
        mountpoint, proc = mount.mountpoint, mount.proc

        # If process is already dead, just clean up
        if proc.poll() is not None:
            if is_mounted(mountpoint):
                fuse_unmount(mountpoint, lazy=True)
            self.cleanup_after_unmount(name, mount)
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
                self.mounted_list[name] = mount
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

        self.cleanup_after_unmount(name, mount)
        return True

    ############################################################################
    # Unmounting, waiting for uploads
    ############################################################################

    def request_unmount(self, names: List[str], quitting: bool = False):
        """Unmount, first offering to wait if files are still uploading."""
        pending = {}
        for name in names:
            counts = upload_counts(self.mounted_list[name]) if name in self.mounted_list else None
            if counts is not None and sum(counts) > 0:
                pending[name] = sum(counts)
        if len(pending) == 0:
            if quitting and not ask("Are you sure you want to quit? QUITTING WILL UNMOUNT ALL DRIVES!"):
                return
            self.finish_unmount(names, quitting)
            return

        dialog = QMessageBox()
        dialog.setWindowTitle(APP_TITLE)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText("Files are still uploading:\n{}\n\nUnfinished uploads stay in the local cache and "
                       "resume the next time the drive is mounted.".format(
                           "\n".join("  {}: {}".format(n, plural(c, "file")) for n, c in pending.items())))
        wait_btn = dialog.addButton("Wait, then quit" if quitting else "Wait, then unmount",
                                    QMessageBox.ButtonRole.AcceptRole)
        now_btn = dialog.addButton("Quit now" if quitting else "Unmount now",
                                   QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(wait_btn)
        dialog.exec()
        if dialog.clickedButton() == wait_btn:
            self.wait_for_uploads(names, quitting)
        elif dialog.clickedButton() == now_btn:
            self.finish_unmount(names, quitting)

    def wait_for_uploads(self, names: List[str], quitting: bool):
        progress = QProgressDialog("Waiting for uploads to finish...", "Cancel", 0, 0)
        progress.setWindowTitle(APP_TITLE)
        progress.setMinimumDuration(0)
        timer = QTimer(progress)

        def check():
            remaining = 0
            for name in names:
                counts = upload_counts(self.mounted_list[name]) if name in self.mounted_list else None
                remaining += sum(counts) if counts is not None else 0
            if remaining > 0:
                progress.setLabelText("Waiting for {} to finish uploading...".format(plural(remaining, "file")))
                return
            timer.stop()
            # close() emits canceled
            progress.canceled.disconnect(cancel)
            progress.close()
            progress.deleteLater()
            self.finish_unmount(names, quitting)

        def cancel():
            timer.stop()
            progress.deleteLater()

        progress.canceled.connect(cancel)
        timer.timeout.connect(check)
        timer.start(1000)
        progress.show()
        check()

    def finish_unmount(self, names: List[str], quitting: bool):
        if quitting:
            self.poll_timer.stop()
            for name in list(self.recovery_failures.keys()):
                self.end_recovery(name)
        for name in names:
            if name not in self.mounted_list:
                continue
            if not self.unmount(name, False, quitting) and quitting:
                if not ask("Failed to cleanly umount {}. Force unmount? If no is selected, "
                           "the application will not exit.".format(name)):
                    # Exit was aborted. Restart poll timer
                    self.poll_timer.start(5000)
                    self.refresh_menu_state()
                    return
                # Force unmount
                self.unmount(name, True, True)
        self.refresh_menu_state()
        if quitting:
            QApplication.instance().quit()

    def exit_app(self):
        self.request_unmount(list(self.mounted_list.keys()), quitting=True)

