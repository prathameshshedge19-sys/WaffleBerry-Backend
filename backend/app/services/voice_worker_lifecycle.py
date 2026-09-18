"""Single-process worker supervision contract, independent of synthesis speed."""
from contextlib import contextmanager
import os
import signal
import socket
import threading


class WorkerLifecycle:
    def __init__(self):
        self.stopping = threading.Event()

    def request_stop(self, *_):
        self.stopping.set()
        self.notify("STOPPING=1")

    @staticmethod
    def notify(message):
        address = os.environ.get("NOTIFY_SOCKET")
        if not address or not hasattr(socket, "AF_UNIX"):
            return
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.settimeout(1)
            client.sendto(message.encode("ascii"), address)

    @contextmanager
    def signals(self):
        previous = {}
        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, self.request_stop)
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
