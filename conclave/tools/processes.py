"""Commands a tool runs, stopped with whoever asked for them.

A tool's command runs in a worker thread so the host keeps answering. Stopping
the caller — a step over its time limit, a run being closed — stops the
command too: it runs in a process group of its own, and the whole group is
killed, rather than left working after the step that started it has stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import threading
import time

# How often a waiting command looks to see whether it should stop.
POLL_SECONDS = 0.05
# How long a stopped caller waits for its command to be gone.
STOP_SECONDS = 5


class CommandStopped(Exception):
    """The command was killed because its caller stopped."""


def _kill(process: subprocess.Popen) -> None:
    if hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, OSError):
        process.kill()


def run_command(command: list[str], *, cwd, timeout: float, stop: threading.Event | None = None) -> subprocess.CompletedProcess:
    """`subprocess.run(command, capture_output=True, text=True, timeout=...)`, killable through `stop`."""
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, errors="replace", start_new_session=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            output, error = process.communicate(timeout=max(0.0, min(POLL_SECONDS, deadline - time.monotonic())))
            return subprocess.CompletedProcess(command, process.returncode, output, error)
        except subprocess.TimeoutExpired:
            if stop is not None and stop.is_set():
                _kill(process)
                process.communicate()
                raise CommandStopped("The command was stopped with the step that started it") from None
            if time.monotonic() >= deadline:
                _kill(process)
                output, error = process.communicate()
                raise subprocess.TimeoutExpired(command, timeout, output, error) from None


async def run_stoppable(command: list[str], *, cwd, timeout: float) -> subprocess.CompletedProcess:
    """Run a command beside the loop; if the caller is cancelled, kill the command and wait for it to go."""
    stop = threading.Event()
    work = asyncio.ensure_future(asyncio.to_thread(run_command, command, cwd=cwd, timeout=timeout, stop=stop))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        stop.set()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(work), STOP_SECONDS)
        raise
