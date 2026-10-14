"""Run the Studio dev server beside the Conclave backend: uv run python dev.py.

`conclave serve` already serves the Studio page on one address; this launcher
adds the Node dev server for UI work and stops both together. The backend finds
the model provider already set up on this computer unless one is named here.
Stopping the launcher stops both services, so neither keeps its port.

The backend is started as `serve --hosted`, this deployment's own adapter, so the
page gets what a deployment has: the hosted routes, the origin allowlist and — when
`studio/.studio-local/login.json` exists, or `--login` names one — Google sign-in.
The page is served from its own port, so that origin is allowed to call the adapter
here rather than left to be discovered as a 403 on the first write.

Connections work here without any setting: the first run generates a key into
`studio/.studio-local/` and every run after it reuses that key, so stored
credentials stay encrypted and readable across restarts.
"""
import argparse
import os
from pathlib import Path
import json
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


def port(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return number


def local_folder(root):
    """What this launcher generates for this machine: the ignored local folder.

    The backend keeps its own data here too, so the files this launcher writes
    sit beside the database they belong to.
    """

    return Path(root) / "studio" / ".studio-local"


def login_file(root, *, chosen=None, off=False):
    """The login file this run should use: what was asked for, or the local one."""

    if off:
        return None
    if chosen:
        return Path(chosen)
    default = local_folder(root) / "login.json"
    return default if default.exists() else None


def login_details(login):
    """The tenant and the return address a login file names, for what is printed."""

    try:
        value = json.loads(Path(login).read_text())
    except (OSError, ValueError):
        return {"tenant": "?", "redirect_uri": "?"}
    if not isinstance(value, dict):
        return {"tenant": "?", "redirect_uri": "?"}
    redirects = [item for item in value.get("redirect_uris", []) if isinstance(item, str)]
    return {"tenant": str(value.get("tenant", "?")), "redirect_uri": redirects[0] if redirects else "?"}


def login_environment(environ, *, login, studio_port, session_key=None):
    """What the pair needs on top of the environment it was given.

    The page is served from another origin than the adapter, so that origin has to
    be allowed to call it — without this every write is refused, which reads as a
    broken page rather than a missing setting. A login deployment also needs a
    session key and the file that says who may sign in. Anything already set wins.
    """

    origins = [item for item in environ.get("CONCLAVE_UI_ORIGINS", "").split(",") if item.strip()]
    for host in ("127.0.0.1", "localhost"):
        origin = f"http://{host}:{studio_port}"
        if origin not in origins:
            origins.append(origin)
    settings = {"CONCLAVE_UI_ORIGINS": ",".join(origins)}
    if login is not None:
        settings["CONCLAVE_GOOGLE_LOGIN_FILE"] = str(login)
        settings["CONCLAVE_SESSION_KEY"] = environ.get("CONCLAVE_SESSION_KEY") or session_key or secrets.token_urlsafe(32)
    return settings


def integration_environment(environ, folder):
    """The connections key: generated once, kept, and reused after that.

    The backend stores connection credentials encrypted, and refuses to store any
    at all without a key. So the first run writes one into the local folder —
    beside the database it decrypts — and every later run reads the same key
    back: a new key would make the connections already stored under the old one
    unreadable. A key already in the environment wins.
    """

    if environ.get("CONCLAVE_INTEGRATION_KEY"):
        return {}
    path = Path(folder) / "integration.key"
    try:
        key = path.read_text().strip()
    except OSError:
        key = ""
    if not key:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key + "\n")
        path.chmod(0o600)
    return {"CONCLAVE_INTEGRATION_KEY": key}


def sign_in_lines(login, details, *, backend_port, studio_port):
    """Where to sign in, and the one address Google has to know about."""

    if login is None:
        return ["Google sign-in is off: no login file. Pass --login PATH to use one."]
    expected = f"http://127.0.0.1:{backend_port}/auth/google/callback"
    lines = [f"Sign in at http://127.0.0.1:{studio_port}/ — the page asks for Google, then shows the account it confirmed."]
    if details["redirect_uri"] == expected:
        lines.append(f"Google must allow this exact redirect URI: {expected} (tenant {details['tenant']})")
    else:
        # The likeliest way for a correct setup to look broken: the two addresses
        # Google compares, in the login file and in the console, do not match.
        lines.append(f"Google must allow {expected}, but the login file says {details['redirect_uri']}: a sign-in "
                     "will be refused until the file and the Google console name the same URI.")
    return lines


def port_error(number):
    """Why no service could listen on this local port now, or None if one could.

    SO_REUSEADDR is what the services set themselves, so sockets the previous
    run left in TIME_WAIT do not look like a port something is holding.
    """
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", number))
        except OSError as error:
            return error
    return None


def busy_ports(ports, timeout=1):
    """The ports that stay unbindable, after a moment for a service to release them."""
    deadline = time.monotonic() + timeout
    while True:
        busy = [number for number in ports if port_error(number) is not None]
        if not busy or time.monotonic() >= deadline:
            return busy
        time.sleep(0.1)


def stop_service(service, force):
    """Stop one service and, when forced, everything it started.

    A service that wraps another process can leave that process holding the
    port, so the forced stop ends the whole process group, or tree on Windows.
    """
    if os.name != "posix":
        try:  # Windows has no signal to catch, so the tree is ended outright.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(service.pid)], capture_output=True, check=False)
        except OSError:
            service.kill()
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    for target in (-service.pid, service.pid):
        try:
            os.kill(target, sig)
        except OSError:
            pass


def stop_children(children, grace=5):
    """Stop every service, so the ports they listen on come free."""
    for child in reversed(children):
        if child.poll() is None:
            stop_service(child, force=False)
    deadline = time.monotonic() + grace
    for child in children:
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            stop_service(child, force=True)
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def wait_ready(url, children, stopped, *, token=None, timeout=30):
    deadline = time.monotonic() + timeout
    while not stopped.is_set():
        if any(child.poll() is not None for child in children):
            raise RuntimeError("A service exited during startup; see its output above.")
        try:
            headers = {"Authorization": "Bearer " + token} if token else {}
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=1) as response:
                if response.status == 200:
                    return
        except urllib.error.HTTPError as error:
            # A deployment with sign-in answers 401 here, and 403 for an origin it
            # was not told about: either way the service is up and listening.
            if error.code in (401, 403):
                return
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Service did not become ready: " + url)
        stopped.wait(0.1)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start the Conclave backend and the Studio dev server together.")
    parser.add_argument("--provider", help="Backend provider, such as codex; the one on this computer when omitted")
    parser.add_argument("--model", help="Model passed to the backend; its own default when omitted")
    parser.add_argument("--backend-port", type=port, default=8080)
    parser.add_argument("--studio-port", type=port, default=8787)
    parser.add_argument("--node", default=os.environ.get("STUDIO_NODE", "node"), help="Node.js 24+ executable (or set STUDIO_NODE)")
    login_choice = parser.add_mutually_exclusive_group()
    login_choice.add_argument("--login", help="Login file giving Google sign-in (the default when it exists)")
    login_choice.add_argument("--no-login", action="store_true", help="Run without Google sign-in, whatever the login file says")
    args = parser.parse_args(argv)
    children, stopped = [], threading.Event()
    root = Path(__file__).resolve().parent
    ports = (args.backend_port, args.studio_port)
    login = login_file(root, chosen=args.login, off=args.no_login)
    details = login_details(login) if login is not None else {"tenant": "?", "redirect_uri": "?"}

    def handle_signal(signum, _frame):
        # A signal handler may only do signal-safe work: os.write, not print.
        if stopped.is_set():
            os.write(2, b"\nForcing both services to stop.\n")
            stop_children(children, grace=0.0)
            return
        stopped.set()
        os.write(2, f"\nStopping both services (signal {signum}); Ctrl+C again to force.\n".encode())

    try:
        if args.backend_port == args.studio_port:
            raise RuntimeError("Backend and Studio must use different ports.")
        if login is not None and not login.exists():
            raise RuntimeError(f"No login file at {login}.")
        for number in ports:
            error = port_error(number)
            if error is not None:
                raise RuntimeError(f"Port {number} is not available: {error}")
        version = subprocess.run([args.node, "--version"], capture_output=True, text=True, check=True)
        if int(version.stdout.strip().removeprefix('v').split('.')[0]) < 24:
            raise RuntimeError("Studio requires Node.js 24 or newer; choose it with --node.")
        stop_signals = [signal.SIGINT, signal.SIGTERM]
        if os.name == "posix":
            stop_signals.append(signal.SIGHUP)  # A closed terminal would otherwise orphan both services.
        for sig in stop_signals:
            signal.signal(sig, handle_signal)
        api_url = f"http://127.0.0.1:{args.backend_port}"
        local = local_folder(root)
        connections = integration_environment(os.environ, local)
        environment = {**os.environ, "CONCLAVE_API_URL": api_url, "STUDIO_PORT": str(args.studio_port),
                       **login_environment(os.environ, login=login, studio_port=args.studio_port), **connections}
        backend = [sys.executable, "-m", "conclave", "serve", "--hosted", "--port", str(args.backend_port)]
        if args.provider:
            backend += ["--provider", args.provider]
        if args.model:
            backend += ["--model", args.model]
        children.append(subprocess.Popen(backend, cwd=root, env=environment, start_new_session=os.name == "posix"))
        wait_ready(api_url + "/api/health", children, stopped, token=environment.get("CONCLAVE_API_TOKEN"))
        if stopped.is_set():
            return 0
        children.append(subprocess.Popen([args.node, "scripts/dev.mjs"], cwd=root / "studio", env=environment, start_new_session=os.name == "posix"))
        studio_url = f"http://127.0.0.1:{args.studio_port}"
        wait_ready(studio_url + "/api/health", children, stopped)
        if stopped.is_set():
            return 0
        print(f"Ready: Studio {studio_url} | Conclave {api_url}/api/health"
              "\nThe backend serves the Studio page too, unless CONCLAVE_API_TOKEN is set.")
        for line in sign_in_lines(login, details, backend_port=args.backend_port, studio_port=args.studio_port):
            print(line)
        if connections:
            print(f"Connections are on: credentials are encrypted with the key kept at {local / 'integration.key'}.")
        print("Press Ctrl+C to stop both services.", flush=True)
        while not stopped.wait(0.25):
            if any(child.poll() is not None for child in children):
                print("A service stopped; stopping the other.", file=sys.stderr)
                return 1
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"Unable to start both services: {error}", file=sys.stderr)
        return 1
    finally:
        stop_children(children)
        if children:
            stuck = busy_ports(ports)
            if stuck:
                held = ", ".join(str(number) for number in stuck)
                print(f"Still in use after stopping the services: port {held}."
                      " Stop whatever holds it before starting again.", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
