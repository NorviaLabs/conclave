import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from pathlib import Path
from types import SimpleNamespace

import pytest

import dev
from dev import busy_ports, port, port_error, stop_children, wait_ready


@pytest.fixture(autouse=True)
def local_folder(tmp_path, monkeypatch):
    """Keep everything the launcher generates inside this test's own folder."""
    folder = tmp_path / "studio" / ".studio-local"
    monkeypatch.setattr(dev, "local_folder", lambda root: folder)
    return folder


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
@pytest.mark.parametrize("force", [False, True])
def test_launcher_stops_child_without_own_process_group(force):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        dev.stop_service(child, force=force)
        child.wait(timeout=5)
        assert child.returncode == -(signal.SIGKILL if force else signal.SIGTERM)
    finally:
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


@pytest.mark.skipif(os.name != "posix", reason="Uses POSIX terminal hangup")
def test_launcher_forces_shutdown_on_repeated_signal(monkeypatch, capfd):
    handlers, stops = {}, []
    service = SimpleNamespace(pid=4321)

    def stop_when_ready(*arguments, **keywords):
        handlers[signal.SIGHUP](signal.SIGHUP, None)
        assert not stops
        handlers[signal.SIGINT](signal.SIGINT, None)

    monkeypatch.setattr(dev, "port_error", lambda number: None)
    monkeypatch.setattr(dev, "wait_ready", stop_when_ready)
    monkeypatch.setattr(dev, "stop_children", lambda children, **keywords: stops.append((list(children), keywords)))
    monkeypatch.setattr(signal, "signal", handlers.__setitem__)
    monkeypatch.setattr(subprocess, "run", lambda *arguments, **keywords: SimpleNamespace(stdout="v24.0.0"))
    monkeypatch.setattr(subprocess, "Popen", lambda *arguments, **keywords: service)
    assert dev.main(["--backend-port", "18080", "--studio-port", "18787"]) == 0
    assert stops == [([service], {"grace": 0.0}), ([service], {})]
    error = capfd.readouterr().err
    assert "Stopping both services" in error
    assert "Forcing both services to stop." in error


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


# --- The pair a signed-in Studio needs ---------------------------------------

def login_file(tmp_path, **value):
    path = tmp_path / "login.json"
    path.write_text(json.dumps({"tenant": "acme", "redirect_uris": ["http://127.0.0.1:8080/auth/google/callback"],
                                **value}))
    return path


def test_launcher_finds_the_login_file_only_when_one_is_wanted(tmp_path):
    (tmp_path / "studio" / ".studio-local").mkdir(parents=True)
    assert dev.login_file(tmp_path) is None
    local = tmp_path / "studio" / ".studio-local" / "login.json"
    local.write_text("{}")
    assert dev.login_file(tmp_path) == local
    assert dev.login_file(tmp_path, off=True) is None
    assert dev.login_file(tmp_path, chosen="/somewhere/else.json") == Path("/somewhere/else.json")


def test_the_page_origin_is_allowed_and_the_login_is_passed_on():
    settings = dev.login_environment({"CONCLAVE_UI_ORIGINS": "https://studio.example.com"},
                                     login=Path("login.json"), studio_port=8787, session_key="k" * 40)
    assert settings["CONCLAVE_UI_ORIGINS"] == "https://studio.example.com,http://127.0.0.1:8787,http://localhost:8787"
    assert settings["CONCLAVE_GOOGLE_LOGIN_FILE"] == "login.json"
    assert settings["CONCLAVE_SESSION_KEY"] == "k" * 40

    # A key already in the environment is reused, so restarting keeps sessions.
    kept = dev.login_environment({"CONCLAVE_SESSION_KEY": "existing-key"}, login=Path("login.json"), studio_port=8787)
    assert kept["CONCLAVE_SESSION_KEY"] == "existing-key"

    # Generated when there is none, and long enough to be one.
    generated = dev.login_environment({}, login=Path("login.json"), studio_port=8787)
    assert len(generated["CONCLAVE_SESSION_KEY"]) >= 32

    # Without a login there is nothing to key or to point at.
    plain = dev.login_environment({}, login=None, studio_port=8787)
    assert "CONCLAVE_SESSION_KEY" not in plain and "CONCLAVE_GOOGLE_LOGIN_FILE" not in plain
    assert plain["CONCLAVE_UI_ORIGINS"] == "http://127.0.0.1:8787,http://localhost:8787"


# --- Connections without any setting -----------------------------------------

def test_the_launcher_generates_one_connections_key_and_keeps_it(local_folder):
    """Credentials have to stay readable across restarts, so the key is written once."""

    from cryptography.fernet import Fernet

    first = dev.integration_environment({}, local_folder)
    path = local_folder / "integration.key"
    assert first["CONCLAVE_INTEGRATION_KEY"] == path.read_text().strip()
    assert path.stat().st_mode & 0o777 == 0o600
    Fernet(first["CONCLAVE_INTEGRATION_KEY"].encode())  # A key the connection store accepts.

    # Restarting reuses it: a fresh key would orphan the connections already stored.
    assert dev.integration_environment({}, local_folder) == first

    # A key already in the environment wins, and nothing is written for it.
    elsewhere = local_folder / "elsewhere"
    assert dev.integration_environment({"CONCLAVE_INTEGRATION_KEY": "given"}, elsewhere) == {}
    assert not elsewhere.exists()

    # An empty file is no key at all.
    path.write_text("\n")
    assert dev.integration_environment({}, local_folder)["CONCLAVE_INTEGRATION_KEY"] != first["CONCLAVE_INTEGRATION_KEY"]


def test_the_launcher_names_the_redirect_uri_google_must_allow():
    matching = dev.sign_in_lines(Path("login.json"), {"tenant": "acme", "redirect_uri": "http://127.0.0.1:8080/auth/google/callback"},
                                 backend_port=8080, studio_port=8787)
    assert "Sign in at http://127.0.0.1:8787/" in matching[0]
    assert matching[1] == "Google must allow this exact redirect URI: http://127.0.0.1:8080/auth/google/callback (tenant acme)"

    # The likeliest way for a correct setup to look broken is these two disagreeing.
    wrong = dev.sign_in_lines(Path("login.json"), {"tenant": "acme", "redirect_uri": "http://127.0.0.1:8123/auth/google/callback"},
                              backend_port=8080, studio_port=8787)
    assert "but the login file says http://127.0.0.1:8123/auth/google/callback" in wrong[1]

    assert dev.sign_in_lines(None, {"tenant": "?", "redirect_uri": "?"}, backend_port=8080, studio_port=8787) == [
        "Google sign-in is off: no login file. Pass --login PATH to use one."]


def test_the_launcher_reads_a_login_file_without_trusting_it():
    assert dev.login_details(Path("/nonexistent/login.json")) == {"tenant": "?", "redirect_uri": "?"}


def test_readiness_accepts_an_api_that_asks_for_sign_in(monkeypatch):
    """A login deployment answers 401 here; that is a service that is up."""

    def gated(*arguments, **keywords):
        raise urllib.error.HTTPError("http://127.0.0.1:8080/api/health", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", gated)
    assert wait_ready("http://127.0.0.1:8080/api/health", [], threading.Event(), timeout=1) is None


def test_readiness_still_waits_for_a_service_that_never_answers(monkeypatch):
    def refused(*arguments, **keywords):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    with pytest.raises(RuntimeError, match="did not become ready"):
        wait_ready("http://127.0.0.1:8080/api/health", [], threading.Event(), timeout=0.3)


def test_the_launcher_starts_the_hosted_adapter_with_the_login(monkeypatch, tmp_path, capsys, local_folder):
    started = []
    login = login_file(tmp_path)

    class Service:
        pid = 4321

        def poll(self):  # Exit at once, so the launcher finishes.
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(dev, "port_error", lambda number: None)
    monkeypatch.setattr(dev, "wait_ready", lambda *arguments, **keywords: None)
    monkeypatch.setattr(signal, "signal", lambda *arguments: None)
    monkeypatch.setattr(subprocess, "run", lambda *arguments, **keywords: SimpleNamespace(stdout="v24.0.0"))
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **keywords: (started.append((argv, keywords)), Service())[1])
    monkeypatch.delenv("CONCLAVE_SESSION_KEY", raising=False)
    monkeypatch.delenv("CONCLAVE_UI_ORIGINS", raising=False)
    monkeypatch.delenv("CONCLAVE_INTEGRATION_KEY", raising=False)

    assert dev.main(["--backend-port", "18080", "--studio-port", "18787", "--login", str(login)]) == 1
    backend, frontend = started
    assert backend[0][1:] == ["-m", "conclave", "serve", "--hosted", "--port", "18080"]
    assert frontend[0] == ["node", "scripts/dev.mjs"]
    for _, keywords in started:
        assert keywords["env"]["CONCLAVE_GOOGLE_LOGIN_FILE"] == str(login)
        assert "http://127.0.0.1:18787" in keywords["env"]["CONCLAVE_UI_ORIGINS"]
        assert len(keywords["env"]["CONCLAVE_SESSION_KEY"]) >= 32
        assert keywords["env"]["CONCLAVE_INTEGRATION_KEY"] == (local_folder / "integration.key").read_text().strip()
    printed = capsys.readouterr().out
    assert "Sign in at http://127.0.0.1:18787/" in printed
    assert "Google must allow http://127.0.0.1:18080/auth/google/callback" in printed
    assert "Connections are on" in printed


def test_the_launcher_refuses_a_login_file_that_is_not_there(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dev, "port_error", lambda number: None)
    monkeypatch.setattr(signal, "signal", lambda *arguments: None)
    monkeypatch.setattr(subprocess, "run", lambda *arguments, **keywords: SimpleNamespace(stdout="v24.0.0"))
    assert dev.main(["--backend-port", "18080", "--studio-port", "18787",
                     "--login", str(tmp_path / "missing.json")]) == 1
    assert "No login file at" in capsys.readouterr().err


def test_the_launcher_will_not_take_both_login_flags():
    with pytest.raises(SystemExit) as refused:
        dev.main(["--login", "a.json", "--no-login"])
    assert refused.value.code == 2
