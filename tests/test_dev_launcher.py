import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import dev
from dev import busy_ports, port, port_error, stop_children, wait_ready


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


@pytest.mark.skipif(os.name != "posix", reason="Uses POSIX process groups")
def test_launcher_leaves_nothing_that_can_hold_the_port(tmp_path):
    """A service that ignores SIGTERM, and the process it started, must both go."""
    marker = tmp_path / "started"
    service = tmp_path / "service.py"
    service.write_text(
        "import pathlib, signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])\n"
        f"pathlib.Path({str(marker)!r}).write_text('started')\n"
        "time.sleep(30)\n"
    )
    child = subprocess.Popen([sys.executable, str(service)], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        stop_children([child], grace=0.5)
        assert child.poll() is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(child.pid, 0)
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def test_launcher_sees_which_port_stays_busy(monkeypatch):
    """A held port is reported by name, and a free one is not."""

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *exception):
            return False

        def setsockopt(self, *arguments):
            return None

        def bind(self, address):
            assert address[0] == "127.0.0.1"
            if address[1] != 8787:
                raise OSError(48, "Address already in use")

    monkeypatch.setattr(socket, "socket", lambda *arguments: Probe())
    assert port_error(8787) is None
    assert str(port_error(8080)) == "[Errno 48] Address already in use"
    assert busy_ports((8080, 8787), timeout=0) == [8080]


def test_launcher_names_the_port_it_cannot_use(monkeypatch, capsys):
    monkeypatch.setattr(dev, "port_error", lambda number: OSError(48, "Address already in use"))
    assert dev.main(["--backend-port", "18080", "--studio-port", "18787"]) == 1
    assert "Port 18080 is not available: [Errno 48] Address already in use" in capsys.readouterr().err


def test_launcher_ends_the_whole_tree_on_windows(monkeypatch):
    """Windows has no signal, so the services and what they started are killed."""
    calls = []

    class Service:
        pid = 4321
        killed = False

        def kill(self):
            self.killed = True

    service = Service()
    monkeypatch.setattr(dev, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subprocess, "run", lambda *arguments, **keywords: calls.append(arguments))
    dev.stop_service(service, force=False)
    assert calls[0][0] == ["taskkill", "/T", "/F", "/PID", "4321"]

    def missing(*arguments, **keywords):
        raise FileNotFoundError("taskkill")

    monkeypatch.setattr(subprocess, "run", missing)
    dev.stop_service(service, force=False)
    assert service.killed


def test_launcher_names_the_port_that_stays_in_use(monkeypatch, capsys):
    """A port something else still holds is reported when the launcher stops."""
    probes = []

    def port_error(number):
        probes.append(number)
        return None if len(probes) <= 2 else OSError(48, "Address already in use")  # Preflight, then after stopping.

    class Service:
        pid = 4321

        def poll(self):  # A service that exits on its own stops the launcher.
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(dev, "port_error", port_error)
    monkeypatch.setattr(dev, "wait_ready", lambda *arguments, **keywords: None)
    monkeypatch.setattr(signal, "signal", lambda *arguments: None)
    monkeypatch.setattr(subprocess, "run", lambda *arguments, **keywords: SimpleNamespace(stdout="v24.0.0"))
    monkeypatch.setattr(subprocess, "Popen", lambda *arguments, **keywords: Service())
    assert dev.main(["--backend-port", "18080", "--studio-port", "18787"]) == 1
    assert "Still in use after stopping the services: port 18080, 18787." in capsys.readouterr().err
