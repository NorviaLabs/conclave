"""Smoke-test the actual local Worker and persisted application/member APIs."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

source = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="conclave-studio-check-") as temporary:
    root = Path(temporary)
    # Keep the check's database and build output separate from the user's data.
    import shutil
    for name in ("scripts", "web", "shared", "runtime", "worker", "drizzle", ".openai"):
        shutil.copytree(source / name, root / name)
    shutil.copy2(source / "package.json", root / "package.json")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = {**os.environ, "STUDIO_PORT": str(port)}
    process = None
    log = root / "check.log"

    def call(path, method="GET", body=None, origin=True):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = base
        request = urllib.request.Request(
            base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as response:
            return response.code, response.read()

    def start(output):
        worker = subprocess.Popen(
            ["node", "scripts/dev.mjs"], cwd=root, env=environment,
            stdin=subprocess.DEVNULL, stdout=output, stderr=output,
        )
        for _ in range(100):
            if worker.poll() is not None:
                raise RuntimeError(log.read_text())
            try:
                if call("/")[0] == 200:
                    return worker
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        worker.terminate()
        worker.wait(timeout=5)
        raise RuntimeError("Local Worker did not start: " + log.read_text())

    with log.open("w") as output:
        try:
            process = start(output)
            for path in ("/", "/investigation", "/catalogue.js", "/downloads/application.py"):
                status, payload = call(path)
                assert status == 200 and payload, (path, status)
            status, payload = call("/api/applications")
            assert status == 200
            applications = json.loads(payload)["applications"]
            assert len(applications) == 18
            status, payload = call("/api/members")
            assert status == 200 and json.loads(payload)["members"] == []
            definition = copy.deepcopy(next(
                row["definition"] for row in applications if row["id"] == "research-brief"
            ))
            definition["name"] = "Terminal handoff persistence check"
            status, payload = call("/api/applications", "POST", {"definition": definition})
            assert status == 201, (status, payload)
            created = json.loads(payload)
            definition["name"] = "Terminal handoff revision check"
            status, payload = call(
                "/api/applications/" + created["id"], "PUT",
                {"definition": definition, "revision": 1},
            )
            assert status == 200 and json.loads(payload)["revision"] == 2
            assert call(
                "/api/applications/" + created["id"], "PUT",
                {"definition": definition, "revision": 1},
            )[0] == 409
            assert call("/api/applications", "POST", {"definition": definition}, origin=False)[0] == 403
            invalid = copy.deepcopy(definition)
            first, second = [step["id"] for step in invalid["workflow"][:2]]
            invalid["connections"] = [
                {"from": first, "to": second}, {"from": second, "to": first}
            ]
            assert call("/api/applications", "POST", {"definition": invalid})[0] == 400
            assert call("/api/members", "POST", {"member": definition["agents"][0]})[0] == 201
            process.terminate()
            process.wait(timeout=5)
            process = start(output)
            saved = json.loads(call("/api/applications")[1])["applications"]
            assert next(row for row in saved if row["id"] == created["id"])["revision"] == 2
            assert len(json.loads(call("/api/members")[1])["members"]) == 1
            print("PASS: Worker assets, 18 starters, create/update, revision conflicts, "
                  "origin checks, cycle rejection and application/member persistence after restart.")
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
