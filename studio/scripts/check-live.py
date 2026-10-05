"""Offline end-to-end Worker -> Python -> fake CLI -> durable review check.

Uses an isolated checkout/database and a fake codex executable. No credentials,
live models, internet, cloud accounts or changes to the user's saved data.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


repository = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="conclave-live-check-") as temporary:
    root = Path(temporary)
    shutil.copytree(repository / "studio", root / "studio", ignore=shutil.ignore_patterns(".studio-local", "node_modules", "dist", "__pycache__"))
    shutil.copytree(repository / "conclave", root / "conclave", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(repository / name, root / name)
    source = root / "sources"
    source.mkdir()
    (source / "source.md").write_text("Offline fixture: three repeat contacts observed. This is not a causal estimate.")
    executable = root / "bin"
    executable.mkdir()
    calls = root / "calls.txt"
    backend = executable / "codex"
    backend.write_text(f"#!{sys.executable}\n" + r'''
import json, os, re, sys, time
from pathlib import Path
prompt = sys.stdin.read()
with Path(os.environ["FIXTURE_CALLS"]).open("a") as log:
    log.write("call\n")
if "SLOW_FIXTURE" in prompt:
    time.sleep(60)
if '"read_file"' in prompt and 'TOOL RESULT for read_file' not in prompt:
    action = {"type": "tool", "name": "read_file", "arguments": {"path": "source.md"}}
elif '"text" string' in prompt:
    action = {"type": "final", "content": "Done", "text": "Offline fixture brief. The supplied observations do not establish causality."}
else:
    match = re.search(r'Allowed status values: (\[[^\n]*\])', prompt)
    statuses = json.loads(match.group(1)) if match else ["recorded"]
    action = {"type": "final", "content": "Done", "records": [{"id": "fixture", "title": "Offline source check", "body": "The supplied observations do not establish causality.", "status": statuses[0], "source_refs": ["source.md:1"]}]}
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(action)}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 4}}))
''')
    backend.chmod(0o755)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = {**os.environ, "PATH": str(executable) + os.pathsep + os.environ["PATH"],
                   "STUDIO_PORT": str(port), "STUDIO_PROVIDER": "codex", "STUDIO_SOURCE_DIR": str(source),
                   "STUDIO_WEB": "0", "FIXTURE_CALLS": str(calls), "UV_NO_SYNC": "1",
                   "UV_PROJECT_ENVIRONMENT": sys.prefix}
    process = None
    log = root / "check.log"

    def call(path, method="GET", value=None, origin=True):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = base
        request = urllib.request.Request(base + path, method=method, headers=headers,
                                         data=json.dumps(value).encode() if value is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as response:
            return response.code, json.loads(response.read())

    def start(output):
        worker = subprocess.Popen(["node", "scripts/dev.mjs"], cwd=root / "studio", env=environment,
                                  stdin=subprocess.DEVNULL, stdout=output, stderr=output)
        for _ in range(100):
            if worker.poll() is not None:
                raise RuntimeError(log.read_text())
            try:
                status, runtime = call("/api/runtime")
                if status == 200 and runtime.get("available"):
                    assert runtime["tools"] == ["read_file"]
                    return worker
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.05)
        worker.terminate()
        worker.wait(timeout=5)
        raise RuntimeError("Runtime did not start: " + log.read_text())

    def wait_status(run_id, expected):
        for _ in range(200):
            status, record = call("/api/runs/" + run_id)
            assert status == 200, record
            if record["status"] == expected:
                return record
            assert record["status"] in {"pending", "running"}, record
            time.sleep(0.02)
        raise RuntimeError("Run did not reach " + expected)

    with log.open("w") as output:
        try:
            process = start(output)
            _, applications = call("/api/applications")
            definition = copy.deepcopy(next(item["definition"] for item in applications["applications"] if item["id"] == "research-brief"))
            definition["name"] = "Offline local bridge check"
            for member in definition["agents"]:
                if member.get("kind", "ai") == "ai":
                    member["tools"] = ["read_file"]
            status, saved = call("/api/applications", "POST", {"definition": definition})
            assert status == 201
            if "--browser" in sys.argv:
                browser_definition = copy.deepcopy(definition)
                browser_definition["name"] = "Browser interaction check"
                status, browser_application = call("/api/applications", "POST", {"definition": browser_definition})
                assert status == 201
                subprocess.run(["node", str(repository / "studio/scripts/check-browser.mjs"), base, browser_application["id"]],
                               env=environment, check=True)
            values = {field["id"]: "Use source.md; this offline fixture contains observational evidence." for field in definition["inputs"]}
            request = {"version": 1, "application_id": saved["id"], "revision": 1, "task": "Assess the supplied source.md fixture", "inputs": values}
            assert call("/api/runs", "POST", request, origin=False)[0] == 403
            assert call("/api/runs", "POST", {**request, "revision": 999})[0] == 409
            assert call("/api/runs", "POST", {**request, "definition": definition})[0] == 400
            status, run = call("/api/runs", "POST", request)
            assert status == 201, run
            record = wait_status(run["id"], "waiting_for_human")
            assert any(item["event"]["type"] == "ToolCalled" and item["event"]["ok"] for item in record["events"])
            assert all("checkpoint" not in item["event"] for item in record["events"])
            before_review = calls.read_text().splitlines()
            process.terminate()
            process.wait(timeout=5)
            process = start(output)
            _, restored = call("/api/runs/" + run["id"])
            assert restored["status"] == "waiting_for_human"
            assert restored["request"]["inputs"] == values
            review = restored["reviews"][0]
            answer = {"records": [{"id": "review", "title": "Operator review", "body": "Approved for the offline protocol check", "status": "approved", "source_refs": []}]}
            prefix = "/api/runs/" + run["id"]
            assert call(prefix + "/reviews", "POST", {"review_id": review["id"], "output": {"records": []}})[0] == 400
            status, reviewed = call(prefix + "/reviews", "POST", {"review_id": review["id"], "output": answer})
            assert status == 200 and reviewed["reviews"][0]["reviewer"]
            assert call(prefix + "/reviews", "POST", {"review_id": review["id"], "output": answer})[0] == 400
            assert call(prefix + "/resume", "POST", {})[0] == 200
            completed = wait_status(run["id"], "completed")
            assert calls.read_text().splitlines() == before_review
            assert completed["metrics"]["tokens"] > 0 and "cost_usd" not in completed["metrics"]
            assert call(prefix + "/resume", "POST", {})[0] == 400
            status, history = call("/api/runs?application_id=" + saved["id"])
            assert status == 200 and history["runs"][0]["id"] == run["id"]
            status, slow = call("/api/runs", "POST", {**request, "task": "SLOW_FIXTURE"})
            assert status == 201
            status, cancelled = call("/api/runs/" + slow["id"] + "/cancel", "POST", {})
            assert status == 200 and cancelled["status"] == "interrupted"
            print("PASS: saved snapshots, origin/revision gates, Worker/Python/fake-CLI bridge, actual source read, restart/review/resume, no replay, reported metrics and cancellation.")
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
