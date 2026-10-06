"""Smoke-test the actual local Worker and persisted application/member APIs."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

source = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="conclave-studio-check-") as temporary:
    root = Path(temporary) / "studio"
    root.mkdir()
    # Keep the check's database and build output separate from the user's data.
    import shutil
    shutil.copytree(source.parent / "conclave", root.parent / "conclave", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("scripts", "web", "shared", "worker", "drizzle", ".openai"):
        shutil.copytree(source / name, root / name)
    shutil.copy2(source / "package.json", root / "package.json")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    defaults = json.loads((root.parent / "conclave/application/data/defaults.json").read_text())
    defaults["run_config"]["max_tool_steps"] = 17
    defaults_path = root / "custom-defaults.json"
    defaults_path.write_text(json.dumps(defaults))
    catalogue = json.loads((root.parent / "conclave/application/data/catalogue.json").read_text())
    catalogue["manualPlanning"]["outputLabels"]["document"] = "Custom deployment report"
    catalogue_path = root / "custom-catalogue.json"
    catalogue_path.write_text(json.dumps(catalogue))
    environment = {**os.environ, "STUDIO_PORT": str(port), "STUDIO_DEFAULTS_FILE": str(defaults_path), "STUDIO_CATALOGUE_FILE": str(catalogue_path)}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        api_port = sock.getsockname()[1]
    environment.update(CONCLAVE_API_URL=f"http://127.0.0.1:{api_port}", CONCLAVE_DATA_DIR=str(root / ".studio-local"))
    process = backend = None
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

    def stop():
        for child in (process, backend):
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    def start(output):
        global backend
        backend = subprocess.Popen([sys.executable, "-m", "conclave", "serve", "--port", str(api_port)], cwd=root.parent, env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=output)
        worker = subprocess.Popen(
            ["node", "scripts/dev.mjs"], cwd=root, env=environment,
            stdin=subprocess.DEVNULL, stdout=output, stderr=output,
        )
        for _ in range(100):
            if worker.poll() is not None:
                raise RuntimeError(log.read_text())
            try:
                if call("/")[0] == 200 and call("/api/health")[0] == 200:
                    return worker
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        worker.terminate()
        worker.wait(timeout=5)
        raise RuntimeError("Local Worker did not start: " + log.read_text())

    with log.open("w") as output:
        try:
            process = start(output)
            for path in ("/", "/catalogue.js", "/downloads/application.py"):
                status, payload = call(path)
                assert status == 200 and payload, (path, status)
            assert call("/investigation")[0] == 404
            assert call("/samples.js")[0] == 404
            assert call("/api/config/fixtures")[0] == 404
            status, payload = call("/api/applications")
            assert status == 200
            applications = json.loads(payload)["applications"]
            assert len(applications) == 18
            assert all(row["definition"]["run_config"]["max_tool_steps"] == 17 for row in applications)
            assert json.loads(call("/catalogue/defaults.json")[1])["run_config"]["max_tool_steps"] == 17
            assert json.loads(call("/catalogue/bundled.json")[1])["manualPlanning"]["outputLabels"]["document"] == "Custom deployment report"
            status, payload = call("/api/members")
            assert status == 200 and json.loads(payload)["members"] == []
            definition = copy.deepcopy(next(
                row["definition"] for row in applications if row["id"] == "research-brief"
            ))
            catalog = {"version": 1, "roles": [], "outputs": [], "starters": [
                {"id": "my_starter", "name": "My starter", "definition": copy.deepcopy(definition)}
            ]}
            assert json.loads(call("/api/catalogue")[1])["revision"] == 0
            assert call("/api/catalogue", "PUT", {"catalogue": catalog, "revision": 0})[0] == 200
            assert call("/api/catalogue", "PUT", {"catalogue": catalog, "revision": 0})[0] == 409
            assert call("/api/catalogue", "PUT", {"catalogue": catalog, "revision": 1}, origin=False)[0] == 403
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
            stop()
            process = start(output)
            restored_catalog = json.loads(call("/api/catalogue")[1])
            assert restored_catalog["revision"] == 1 and restored_catalog["catalogue"] == catalog
            empty_catalog = {"version": 1, "roles": [], "outputs": [], "starters": []}
            assert call("/api/catalogue", "PUT", {"catalogue": empty_catalog, "revision": 1})[0] == 200
            assert json.loads(call("/api/catalogue")[1])["catalogue"] == empty_catalog
            saved = json.loads(call("/api/applications")[1])["applications"]
            assert next(row for row in saved if row["id"] == created["id"])["revision"] == 2
            assert len(json.loads(call("/api/members")[1])["members"]) == 1
            print("PASS: Worker assets, 18 starters, create/update, revision conflicts, "
                  "origin checks, cycle rejection and application/member persistence after restart.")
        finally:
            stop()
