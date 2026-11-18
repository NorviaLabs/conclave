"""Create and execute application definitions using the shared Conclave service."""
import argparse
import asyncio
import json
import hashlib
import os
from pathlib import Path
from contextlib import contextmanager

from .application.definition import validate_definition
from .application.presentation import render_run
from .application.service import configured_host
from .application.service import setting
from .application.runs import ActionsUnsettled
from .api import configured_api


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
    # The terminal reads a run after it finishes and has no way to answer a
    # permission question, so this host tells the truth: refuse, do not wait.
    host = configured_host(workspace=Path.cwd(), source_dir=getattr(args, "source_dir", None),
                           interactive_approvals=False)
    try:
        if args.command == "run":
            definition = read_json(args.definition)
            application_id = args.application_id or hashlib.sha256(str(Path(args.definition).resolve()).encode()).hexdigest()
            value = {"version": 1, "application_id": application_id,
                     "revision": args.revision, "definition": definition, "task": args.task,
                     "inputs": read_json(args.inputs) if args.inputs else {}}
            if args.source:
                value["resources"] = {"source": args.source}
            _, record = await host.handle("start", value)
        elif args.command == "review":
            _, record = await host.handle("review", {"run_id": args.run_id, "review_id": args.review_id, "output": read_json(args.output)})
        elif args.command in {"actions", "settle", "check"}:
            # What a run was sending when it stopped, and a person's decision
            # about it: printed as the host reports it, nothing else.
            value = {"run_id": args.run_id}
            if args.command != "actions":
                value["action_id"] = args.action_id
            if args.command == "settle":
                value["decision"] = args.decision
                if args.note is not None:
                    value["note"] = args.note
            _, listed = await host.handle(args.command, value)
            print(json.dumps(listed, indent=2, allow_nan=False))
            return 0
        else:
            try:
                _, record = await host.handle("get" if args.command == "status" else args.command, {"run_id": args.run_id})
            except ActionsUnsettled as error:
                views = {item["id"]: item for item in (await host.handle("actions", {"run_id": args.run_id}))[1]["actions"]}
                error.details = [views[identifier] for identifier in error.actions if identifier in views]
                raise
        # Through any automatic retry: the command waits until the run completes,
        # waits for a person, or stops with nothing more to try.
        await host.until_idle(record["id"])
        _, record = await host.handle("get", {"run_id": record["id"]})
        if args.format == "text":
            print(render_run(record), end="")
        else:
            print(json.dumps(record, indent=2, allow_nan=False))
        return 1 if record["status"] in {"failed", "interrupted"} else 0
    finally:
        await host.close()


async def schedule_command(args):
    """Show, pause or resume a schedule. Only a serving host starts its runs."""
    api = configured_api(workspace=Path.cwd())
    try:
        route = f"/api/applications/{args.application_id}/schedule"
        status, body = (await api.dispatch("GET", route) if args.action == "status"
                        else await api.dispatch("POST", route, {"action": args.action}))
        if status >= 400:
            raise ValueError(body.get("error") or "No such application")
        print(json.dumps(body, indent=2, allow_nan=False))
        return 0
    finally:
        await api.host.close()


def run(argv=None):
    parser = argparse.ArgumentParser(description="Create and run any Conclave application definition.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a minimal editable application JSON file")
    create.add_argument("definition")
    create.add_argument("--name", required=True)
    create.add_argument("--instructions", required=True)
    server = commands.add_parser("serve", help="Start the local Conclave REST API and Studio page")
    server.add_argument("--port", type=int, default=8080)
    server.add_argument("--hosted", action="store_true",
                        help="Serve this deployment's adapter (sign-in, origins) on a loopback address")
    server.add_argument("--provider", help="Bundled backend; the one on this computer when omitted")
    server.add_argument("--model", help="Model passed to the backend; its own default when omitted")
    for name in ("run", "status", "review", "resume", "cancel"):
        command = commands.add_parser(name)
        command.add_argument("--database", help="Run database (shared with Studio by default)")
        command.add_argument("--format", choices=("json", "text"), default="json",
                             help="json (default) for the whole record, text to read it")
        command.add_argument("--provider", help="Bundled backend; custom factories use CONCLAVE_PROVIDER_FACTORY")
        command.add_argument("--model", help="Model passed to the backend; its own default when omitted")
        if name == "run":
            command.add_argument("definition", help="Application JSON, including Studio exports")
            command.add_argument("--task", required=True)
            command.add_argument("--inputs", help="JSON file containing declared request field values")
            command.add_argument("--source", help="Authorized source resource ID for this request")
            command.add_argument("--source-dir", help="Folder to authorize as the source for this request")
            command.add_argument("--application-id")
            command.add_argument("--revision", type=int, default=1)
        else:
            command.add_argument("run_id")
            if name == "review":
                command.add_argument("review_id")
                command.add_argument("--output", required=True, help="JSON contribution matching the waiting output schema")
    scheduled = commands.add_parser("schedule", help="Show, pause or resume an application's schedule")
    scheduled.add_argument("action", choices=("status", "pause", "resume"))
    scheduled.add_argument("application_id")
    scheduled.add_argument("--database", help="Run database (shared with Studio by default)")
    actions = commands.add_parser("actions", help="List what a run was sending, and what is known about each action")
    check = commands.add_parser("check", help="Ask the service whether an uncertain action happened")
    settle = commands.add_parser("settle", help="Decide an uncertain action: it happened, send it again, or undo")
    for command in (actions, check, settle):
        command.add_argument("--database", help="Run database (shared with Studio by default)")
        command.add_argument("run_id")
        if command is not actions:
            command.add_argument("action_id")
    decision = settle.add_mutually_exclusive_group(required=True)
    decision.add_argument("--done", dest="decision", action="store_const", const="done", help="It happened: do not send it again")
    decision.add_argument("--resend", dest="decision", action="store_const", const="resend", help="Send it again when the run continues")
    decision.add_argument("--undo", dest="decision", action="store_const", const="undo", help="Withdraw the decision")
    settle.add_argument("--note", help="With --done: what you saw, passed on as your words")
    args = parser.parse_args(argv)
    if args.command == "settle" and args.note is not None and args.decision != "done":
        parser.error("--note goes with --done")
    try:
        if args.command == "serve":
            from .api import serve
            if not 0 <= args.port <= 65535:
                raise ValueError("Use a port between 0 and 65535")
            with host_settings(args):
                asyncio.run(serve(args.port, hosted=args.hosted))
            return
        if args.command == "create":
            definition = create_definition(args.name, args.instructions)
            # Refuse to overwrite an existing application.
            with Path(args.definition).open("x") as output:
                output.write(json.dumps(definition, indent=2) + "\n")
            return
        with host_settings(args):
            code = asyncio.run(schedule_command(args) if args.command == "schedule" else execute(args))
    except ActionsUnsettled as error:
        # Not a failure: the run waits for a person. Say what, and how.
        listed = ", ".join(f"{item['id']} {item['label']}" for item in error.details) or ", ".join(error.actions)
        count = len(error.actions)
        parser.exit(3, f"conclave: {count} action{'s' if count != 1 else ''} still need{'s' if count == 1 else ''} a decision: {listed}\n"
                       f"          settle with: conclave settle {args.run_id} ACTION_ID --done [--note TEXT] | --resend\n")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"conclave: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "conclave: interrupted; inspect the saved run before retrying\n")
    if code:
        parser.exit(code)
