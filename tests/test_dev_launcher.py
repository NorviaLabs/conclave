import argparse
import subprocess
import sys
import threading

import pytest

from dev import port, stop_children, wait_ready


@pytest.mark.parametrize("value", ["0", "-1", "65536"])
def test_launcher_rejects_invalid_ports(value):
    with pytest.raises(argparse.ArgumentTypeError):
        port(value)


def test_launcher_detects_exited_service_before_readiness():
    class Exited:
        def poll(self):
            return 1

    with pytest.raises(RuntimeError, match="exited during startup"):
        wait_ready("http://127.0.0.1:1/api/health", [Exited()], threading.Event())


def test_launcher_stops_its_children():
    import os
    children = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=os.name == "posix") for _ in range(2)]
    try:
        stop_children(children)
        assert all(child.poll() is not None for child in children)
        stop_children(children)  # Cleanup is safe after a child already exited.
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
