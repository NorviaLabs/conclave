"""Run the Studio dev server beside the Conclave backend: uv run python dev.py.

`conclave serve` already serves the Studio page on one address; this launcher
adds the Node dev server for UI work and stops both together. The backend finds
the model provider already set up on this computer unless one is named here.
Stopping the launcher stops both services, so neither keeps its port.
"""
import argparse
import os
from pathlib import Path
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
    args = parser.parse_args(argv)
    children, stopped = [], threading.Event()
    root = Path(__file__).resolve().parent
    ports = (args.backend_port, args.studio_port)

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
        environment = {**os.environ, "CONCLAVE_API_URL": api_url, "STUDIO_PORT": str(args.studio_port)}
        backend = [sys.executable, "-m", "conclave", "serve", "--port", str(args.backend_port)]
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
              "\nThe backend serves the Studio page too, unless CONCLAVE_API_TOKEN is set."
              "\nPress Ctrl+C to stop both services.", flush=True)
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
