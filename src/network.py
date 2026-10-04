import sys

from PySide6.QtCore import QObject, Signal, Slot, SLOT
from PySide6.QtDBus import QDBusConnection, QDBusMessage

from common import APP_TITLE

NM_SERVICE = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
# NM_STATE_CONNECTED_GLOBAL: full internet access
NM_STATE_CONNECTED_GLOBAL = 70


class NetworkMonitor(QObject):
    """Tracks whether NetworkManager reports internet access. Without NetworkManager
    (containers, other network managers) it always reports online."""
    online = Signal()

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)
        self.available = False
        self.state = NM_STATE_CONNECTED_GLOBAL
        bus = QDBusConnection.systemBus()
        if not bus.isConnected():
            print("{}: system bus unavailable, not watching the network".format(APP_TITLE), file=sys.stderr)
            return
        msg = QDBusMessage.createMethodCall(NM_SERVICE, NM_PATH, "org.freedesktop.DBus.Properties", "Get")
        msg.setArguments([NM_SERVICE, "State"])
        reply = bus.call(msg, timeout=2000)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or len(reply.arguments()) == 0:
            print("{}: NetworkManager unavailable, not watching the network ({})".format(
                APP_TITLE, reply.errorName()), file=sys.stderr)
            return
        state = reply.arguments()[0]
        state = state.variant() if hasattr(state, "variant") else state
        if not isinstance(state, int):
            return
        self.state = state
        self.available = bus.connect(NM_SERVICE, NM_PATH, NM_SERVICE, "StateChanged",
                                     self, SLOT("state_changed(uint)"))

    def is_online(self) -> bool:
        return self.state >= NM_STATE_CONNECTED_GLOBAL

    @Slot("uint")
    def state_changed(self, state: int):
        was_online = self.is_online()
        self.state = state
        if self.is_online() and not was_online:
            self.online.emit()
