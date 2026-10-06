"""Run Conclave and Studio together: uv run python dev.py --provider codex."""
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


def stop_children(children):
    for child in reversed(children):
        if child.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGTERM)
                else:
                    child.terminate()
            except ProcessLookupError:
                pass
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
            child.wait()


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
    parser = argparse.ArgumentParser(description="Start the Conclave backend and Studio frontend together.")
    parser.add_argument("--provider", help="Backend provider, such as codex; otherwise use host configuration")
    parser.add_argument("--model")
    parser.add_argument("--backend-port", type=port, default=8080)
    parser.add_argument("--studio-port", type=port, default=8787)
    parser.add_argument("--node", default=os.environ.get("STUDIO_NODE", "node"), help="Node.js 24+ executable (or set STUDIO_NODE)")
    args = parser.parse_args(argv)
    children, stopped = [], threading.Event()
    root = Path(__file__).resolve().parent
    try:
        if args.backend_port == args.studio_port:
            raise RuntimeError("Backend and Studio must use different ports.")
        for number in (args.backend_port, args.studio_port):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", number))
        version = subprocess.run([args.node, "--version"], capture_output=True, text=True, check=True)
        if int(version.stdout.strip().removeprefix('v').split('.')[0]) < 24:
            raise RuntimeError("Studio requires Node.js 24 or newer; choose it with --node.")
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopped.set())
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
        print(f"Ready: Studio {studio_url} | Conclave {api_url}\nPress Ctrl+C to stop both services.", flush=True)
        while not stopped.wait(0.25):
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A service exited; stopping the other service.")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"Unable to start both services: {error}", file=sys.stderr)
        return 1
    finally:
        stop_children(children)


if __name__ == "__main__":
    sys.exit(main())
