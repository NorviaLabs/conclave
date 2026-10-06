"""Create and execute application definitions using the shared Conclave service."""
import argparse
import asyncio
import json
import hashlib
import os
from pathlib import Path
from contextlib import contextmanager

from .application.definition import validate_definition
from .application.service import configured_host
from .application.service import setting


def read_json(path):
    return json.loads(Path(path).read_text())


def create_definition(name, instructions):
    from .application import service as host
    defaults = Path(setting("DEFAULTS_FILE", Path(host.__file__).parent / "data/defaults.json"))
    configured = read_json(defaults)
    definition = {key: configured[key] for key in ("version", "run_config", "run_budget")}
    definition.update(name=name, description=instructions, inputs=[], agents=[{
        "id": "worker", "name": "Worker", "kind": "ai", "role": "custom",
        "instructions": instructions, "tools": [],
    }], state=[{"key": "result", "label": "Result", "kind": "document", "type": "text", "merge": "replace", "statuses": []}],
        workflow=[{"id": "work", "name": "Produce result", "agent": "worker", "task": instructions, "output": "result"}],
        views=[{"id": "result_view", "title": "Result", "collection": "result", "layout": "prose"}])
    validate_definition(definition)
    return definition


@contextmanager
def host_settings(args):
    overrides = {"CONCLAVE_PROVIDER": getattr(args, "provider", None), "CONCLAVE_MODEL": getattr(args, "model", None)}
    database = getattr(args, "database", None)
    if database:
        overrides["CONCLAVE_RUN_DATABASE"] = str(Path(database).resolve())
    overrides = {key: value for key, value in overrides.items() if value is not None}
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def execute(args):
    host = configured_host(workspace=Path.cwd())
    try:
        if args.command == "run":
            definition = read_json(args.definition)
            application_id = args.application_id or hashlib.sha256(str(Path(args.definition).resolve()).encode()).hexdigest()
            value = {"version": 1, "application_id": application_id,
                     "revision": args.revision, "definition": definition, "task": args.task,
                     "inputs": read_json(args.inputs) if args.inputs else {}}
            _, record = await host.handle("start", value)
        elif args.command == "review":
            _, record = await host.handle("review", {"run_id": args.run_id, "review_id": args.review_id, "output": read_json(args.output)})
        else:
            _, record = await host.handle("get" if args.command == "status" else args.command, {"run_id": args.run_id})
        active = host.tasks.get(record["id"])
        if active:
            await active[0]
        _, record = await host.handle("get", {"run_id": record["id"]})
        print(json.dumps(record, indent=2, allow_nan=False))
        return 1 if record["status"] in {"failed", "interrupted"} else 0
    finally:
        await host.close()


def run(argv=None):
    parser = argparse.ArgumentParser(description="Create and run any Conclave application definition.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a minimal editable application JSON file")
    create.add_argument("definition")
    create.add_argument("--name", required=True)
    create.add_argument("--instructions", required=True)
    server = commands.add_parser("serve", help="Start the local Conclave REST API")
    server.add_argument("--port", type=int, default=8080)
    server.add_argument("--provider")
    server.add_argument("--model")
    for name in ("run", "status", "review", "resume", "cancel"):
        command = commands.add_parser(name)
        command.add_argument("--database", help="Run database (shared with Studio by default)")
        command.add_argument("--provider", help="Bundled backend; custom factories use STUDIO_PROVIDER_FACTORY")
        command.add_argument("--model")
        if name == "run":
            command.add_argument("definition", help="Application JSON, including Studio exports")
            command.add_argument("--task", required=True)
            command.add_argument("--inputs", help="JSON file containing declared request field values")
            command.add_argument("--application-id")
            command.add_argument("--revision", type=int, default=1)
        else:
            command.add_argument("run_id")
            if name == "review":
                command.add_argument("review_id")
                command.add_argument("--output", required=True, help="JSON contribution matching the waiting output schema")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .api import serve
            if not 0 <= args.port <= 65535:
                raise ValueError("Use a port between 0 and 65535")
            with host_settings(args):
                asyncio.run(serve(args.port))
            return
        if args.command == "create":
            definition = create_definition(args.name, args.instructions)
            # Refuse to overwrite an existing application.
            with Path(args.definition).open("x") as output:
                output.write(json.dumps(definition, indent=2) + "\n")
            return
        with host_settings(args):
            code = asyncio.run(execute(args))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"conclave: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "conclave: interrupted; inspect the saved run before retrying\n")
    if code:
        parser.exit(code)
