"""Local REST adapter for Conclave's shared application service.

HTTP presentation stays outside the reusable multi-agent runtime. This local,
single-user server uses asyncio and SQLite without adding core dependencies.
"""
import asyncio
import hmac
import json
import signal
from typing import NamedTuple
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .application.catalogue import configured_data, starter_definitions
from .application.definition import validate_definition
from .application.service import configured_host, setting, request_body_limit
from .application.workspace import Workspace, Conflict
from .application.resources import load_resources


SDK_SOURCE = '"""Compatibility facade for the installed Conclave application adapter."""\nfrom conclave.application import run_application, validate_definition\n\n__all__ = ["run_application", "validate_definition"]\n'

CONTENT_TYPES = {
    ".css": "text/css;charset=utf-8",
    ".html": "text/html;charset=utf-8",
    ".js": "text/javascript;charset=utf-8",
    ".json": "application/json;charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

# The browser loads its modules from absolute paths, so the page and its
# sources live beside the application data they import over REST.
SHARED_MODULES = {
    "definition.js": ("'../../conclave/application/data/defaults.json'", "/api/config/defaults"),
    "catalogue.js": ("'../../conclave/application/data/catalogue.json'", "/api/config/catalogue"),
}


class StaticResponse(NamedTuple):
    status: int
    body: bytes
    content_type: str
    filename: str | None = None


def studio_assets(root=None) -> dict[str, tuple[bytes, str]]:
    """The Studio page and its modules, keyed by request path.

    Read once at startup: the server never touches the filesystem for a
    request path, so no crafted path can reach a file outside this bundle.
    A checkout without Studio serves the REST API alone.
    """

    studio = Path(root) if root is not None else Path(__file__).resolve().parents[1] / "studio"
    web, shared = studio / "web", studio / "shared"

    if not web.is_dir() or not shared.is_dir():
        return {}

    assets = {
        "/" + path.relative_to(web).as_posix(): (
            path.read_bytes(),
            CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
        )
        for path in sorted(web.rglob("*"))
        if path.is_file()
    }

    for name, (imported, endpoint) in SHARED_MODULES.items():
        source = (shared / name).read_text().replace(imported, f"'{endpoint}'")

        assets["/" + name] = (source.encode(), CONTENT_TYPES[".js"])

    return assets


def static_page(api, method, target, assets) -> StaticResponse | None:
    """A served page, module or downloaded adapter, or None for no match.

    `api` is None when a hosted adapter owns the API: the bundle is still served
    from this origin, and the catalogue comes from the adapter instead.
    """

    path = unquote(urlsplit(target).path)

    if method not in {"GET", "HEAD"}:
        return StaticResponse(405, b"", "text/plain;charset=utf-8")

    if path == "/downloads/application.py":
        return StaticResponse(200, SDK_SOURCE.encode(), "text/x-python;charset=utf-8", "conclave_application.py")

    catalogue = {} if api is None else {"/catalogue/defaults.json": api.defaults, "/catalogue/bundled.json": api.catalogue}

    if path in catalogue:
        return StaticResponse(200, json.dumps(catalogue[path]).encode(), CONTENT_TYPES[".json"])

    asset = assets.get("/index.html" if path == "/" else path)

    if asset is None:
        return None

    return StaticResponse(200, asset[0], asset[1])


def startup_lines(api, address, assets, token) -> list[str]:
    """What an operator needs at startup: where it is, and what runs it."""

    lines = [f"Conclave REST API: {address}/api/health"]

    if assets:
        lines.append(f"Studio: {address}")
    elif token:
        # A browser page cannot present a bearer token, so a token
        # deployment keeps the Studio dev server and this API separate.
        lines.append("Studio is not served while CONCLAVE_API_TOKEN is set; start the Studio dev server instead.")
    elif not api.host.profile.serve_ui:
        lines.append(f"This {api.host.profile.name} host serves the API only; deploy the Studio bundle separately.")

    if api.host.provider is not None:
        lines.append(f"AI backend: {api.host.provider_name or 'configured'}"
                     + (" (found on this computer)" if api.host.provider_source == "detected" else ""))
    elif api.host.unavailable:
        lines.append(f"AI backend: unavailable. {api.host.unavailable}")

    return lines


def reject_nonfinite(value):
    raise ValueError("Nonfinite JSON numbers are unsupported")


class ApplicationAPI:
    def __init__(self, host, workspace, *, defaults=None, catalogue=None):
        self.host, self.workspace = host, workspace
        self.defaults = defaults if defaults is not None else configured_data("defaults")
        self.catalogue = catalogue if catalogue is not None else configured_data("catalogue")
        self.proposal_lock = asyncio.Lock()
        from .application.step_testing import StepTestStore
        self.step_tests = StepTestStore(host.paths.root / 'step-tests.sqlite')
        self.test_tasks = {}

    async def _step_testing(self, method, parts, query, value, actor):
        import copy
        from .application.capture import redact
        from .application.step_testing import authored_fixture, fixture_preview, test_scope, compare
        from .application.step_execution import execute_test, safe_tools
        application, section = parts[2], parts[3]
        saved = self.workspace.get(application)
        kind = 'fixture' if section == 'fixtures' else 'test'
        store = self.step_tests
        if section == 'fixtures' and method == 'POST' and parts[4:] == ['author']:
            # A step that has never run can still be tested: the person supplies
            # the request inputs and the output of every step it depends on, and
            # the fixture records that they did so rather than claiming a run.
            if not isinstance(value, dict) or not isinstance(value.get('step_id'), str) or set(value) - {'step_id', 'name', 'definition', 'inputs', 'upstream', 'reference', 'reference_recorded', 'task'}:
                raise ValueError('Choose a step to test')
            name = value.get('name')
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError('Name the fixture (up to 100 characters)')
            definition = copy.deepcopy(value.get('definition', saved['definition']))
            data = authored_fixture(definition, application, saved['revision'], value['step_id'],
                                    inputs=value.get('inputs'), upstream=value.get('upstream'),
                                    reference=value.get('reference'), reference_recorded=bool(value.get('reference_recorded')),
                                    task=value.get('task') or '')
            data['name'] = name.strip()
            return 201, store.save(application, actor.name, kind, data)
        if section == 'fixtures' and method == 'POST' and (len(parts) == 4 or parts[4:] == ['preview']):
            if not isinstance(value, dict) or set(value) - {'run_id', 'step_id', 'name', 'data'} or not {'run_id', 'step_id'} <= set(value):
                raise ValueError('Choose a source run and completed step')
            _, record = await self.host.handle('get', {'run_id': value['run_id']}, principal=actor)
            if record['request']['application_id'] != application:
                raise KeyError(application)
            preview = fixture_preview(record, value['step_id'])
            if len(parts) == 5:
                return 200, preview
            name = value.get('name')
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError('Name the fixture (up to 100 characters)')
            data = value.get('data', preview)
            if not isinstance(data, dict) or set(data) != set(preview):
                raise ValueError('Use the fixture preview envelope')
            editable = {'inputs', 'input_state', 'patches', 'reference', 'task'}
            if any(data[key] != preview[key] for key in preview.keys() - editable):
                raise ValueError('Fixture provenance cannot be edited')
            data = redact(copy.deepcopy(data))
            from .application.definition import _dependency_state, validate_request_inputs
            validate_request_inputs(data['definition'], data['inputs'])
            if _dependency_state(data['definition'], data['step_id'], data['patches']) != data['input_state']:
                raise ValueError('Edited prerequisite patches must match the input state')
            data['name'] = name.strip()
            return 201, store.save(application, actor.name, kind, data)
        if len(parts) == 4 and method == 'GET':
            return 200, {section: store.list(application, actor.name, kind)}
        if section == 'tests' and len(parts) == 4 and method == 'POST':
            if not isinstance(value, dict) or not {'fixture_id', 'scope'} <= set(value) or set(value) - {'fixture_id', 'scope', 'definition', 'model', 'mocks'}:
                raise ValueError('Choose a fixture and test scope')
            fixture = store.get(application, actor.name, 'fixture', value['fixture_id'])
            # An authored fixture belongs to the definition it was written
            # against, so that is the default; a recorded one is tested against
            # the application's saved version unless the caller says otherwise.
            definition = copy.deepcopy(value.get('definition', fixture['definition'] if fixture.get('authored') else saved['definition']))
            if value.get('model'):
                if not isinstance(value['model'], str):
                    raise ValueError('Model override must be a model ID')
                next(step for step in definition['workflow'] if step['id'] == fixture['step_id'])['model'] = value['model']
            selected, seeds, warnings = test_scope(fixture, definition, value['scope'])
            if definition['run_budget'].get('cost_usd') is not None:
                raise ValueError('This provider interface cannot report cost; a finite cost cap cannot be enforced')
            if self.host.unavailable or self.host.provider is None and self.host.backends is None:
                raise ValueError('No provider is available for this test')
            tools = self.host.tools
            safe_tools(tools, value.get('mocks', {}))
            record = store.save(application, actor.name, 'test', {
                'version': 1, 'fixture_id': fixture['id'], 'scope': value['scope'], 'tested_steps': sorted(selected),
                'seeded_steps': sorted(seeds), 'status': 'pending', 'events': [], 'reviews': [],
                'request': {'definition': definition, 'task': fixture['task'], 'inputs': fixture['inputs'], 'revision': saved['revision']},
                'reference': fixture['reference'], 'reference_recorded': bool(fixture.get('reference_recorded')),
                'source_run': fixture.get('source_run'), 'authored': bool(fixture.get('authored')),
                'warnings': warnings, 'mocks': value.get('mocks', {}), 'safe_mode': True})
            task = asyncio.create_task(execute_test(store, application, actor.name, record, fixture, self.host.backends or self.host.provider, tools))
            self.test_tasks[record['id']] = task
            task.add_done_callback(lambda finished: self.test_tasks.pop(record['id'], None))
            return 202, record
        if len(parts) >= 5:
            record = store.get(application, actor.name, kind, parts[4])
            if len(parts) == 5 and method == 'GET':
                return 200, record
            if len(parts) == 5 and method == 'DELETE' and kind == 'fixture':
                store.delete(application, actor.name, kind, parts[4])
                return 204, None
            if parts[5:] == ['compare'] and method == 'GET' and kind == 'test':
                reference = record['reference']
                if 'other' in query:
                    other = store.get(application, actor.name, kind, query['other'][0])
                    if other['fixture_id'] != record['fixture_id']:
                        raise ValueError('Compare tests of the same fixture')
                    reference = other.get('output')
                elif not record.get('reference_recorded'):
                    return 200, {'kind': 'none', 'equal': None, 'note': 'This fixture recorded no reference output to compare against'}
                return 200, compare(reference, record.get('output'))
        return 404, {'error': 'Endpoint not found'}

    async def dispatch(self, method, target, value=None, *, principal=None, streaming=False):
        """Answer one request. `principal` is who acted, when the transport
        established one; the host's own principal answers otherwise. `streaming`
        is what the transport can do, which decides whether this host reports
        that a run's events can be pushed."""
        try:
            return await self._dispatch(method, target, value, principal, streaming)
        except Conflict as error:
            return 409, {"error": str(error)}
        except KeyError:
            return 404, {"error": "Resource not found"}
        except ValueError as error:
            return 400, {"error": str(error)}
        except (TypeError, AttributeError):
            return 400, {"error": "Invalid request fields"}
        except Exception:
            # Backend exceptions may contain credentials; never expose them.
            return 503, {"error": "The Conclave service is unavailable"}

    @staticmethod
    def fields(value, required):
        if not isinstance(value, dict) or set(value) != set(required):
            raise ValueError("Use the supported request fields")

    async def _dispatch(self, method, target, value, principal=None, streaming=False):
        url = urlsplit(target)
        parts = [unquote(part) for part in url.path.strip('/').split('/')]
        query = parse_qs(url.query)
        if parts[:1] != ["api"]:
            return 404, {"error": "Endpoint not found"}
        path = url.path
        actor = principal or self.host.principal
        if actor.tenant != self.host.principal.tenant:
            return 403, {'error': 'Tenant is not authorized'}
        if len(parts) >= 4 and parts[1] == 'applications' and parts[3] in {'fixtures', 'tests'}:
            return await self._step_testing(method, parts, query, value, actor)
        if parts[1:2] == ['integrations'] or parts[1:2] == ['connections'] or parts[1:2] == ['hooks']:
            from .integrations.services import integrations
            from .integrations.transport import IntegrationError
            from dataclasses import asdict
            actor = principal or self.host.principal
            if actor.tenant != self.host.principal.tenant:
                return 403, {'error': 'Connection tenant is not authorized'}
            store = getattr(self.host, 'connections', None)
            if parts[1:2] == ['hooks'] and method == 'POST':
                # The transport verified the signature before this body existed;
                # a browser, or the Studio proxy, cannot claim that in a payload.
                if not (principal is not None and principal.source == 'hook') or len(parts) != 3:
                    return 403, {'error': 'Signed hook deliveries are the only way to start this run'}
                self.fields(value, {'payload'})
                if store is None:
                    return 503, {'error': 'Configure CONCLAVE_INTEGRATION_KEY to enable inbound hooks'}
                record = store.hook(parts[2])
                application = record['applications'][0]
                try:
                    saved = self.workspace.get(application)
                except Exception:
                    return 409, {'error': 'The hook connection names an application that is not saved on this host'}
                # A delivery supplies only the inputs the application declared,
                # as bounded text; anything else in the payload is not state.
                payload = value['payload'] if isinstance(value['payload'], dict) else {}
                inputs = {item['id']: payload[item['id']] for item in saved['definition'].get('inputs', [])
                          if item.get('type') != 'number' and isinstance(payload.get(item['id']), str)
                          and len(payload[item['id']]) <= 20000}
                return await self.host.handle('hook', {'connection_id': parts[2], 'payload': value['payload'],
                                                       'definition': saved['definition'], 'revision': saved['revision'],
                                                       'owner': record['owner'], 'inputs': inputs})
            if parts == ['api', 'integrations'] and method == 'GET':
                # One truthful description per service, plus each host instance as
                # its own choice, so a catalogue never promises what cannot run.
                def described(service, instance=None):
                    return {**asdict(service), 'available': bool(service.operations),
                            'description': 'Connect an account to use ' + service.name + '.',
                            'group': self.host._integration_group(service.category), 'origin': 'integration',
                            # An instance is one connectable thing: it is its own
                            # catalogue entry, and it says which service it runs.
                            **({'id': instance, 'integration': service.id} if instance else {})}
                catalogue = store.catalogue() if store else integrations()
                instances = store.adapters.instances if store and store.adapters else {}
                covered = {service.id for service in instances.values()}
                return 200, {'integrations': [described(service) for identifier, service in catalogue.items()
                                              if identifier not in covered]
                             + [described(service, identifier) for identifier, service in instances.items()
                                if service.operations],
                             'connections_enabled': store is not None,
                             'oauth_providers': [{'id': name, 'integrations': config.get('integrations', []),
                                                  'redirect_uris': config['redirect_uris']}
                                                 for name, config in (store.oauth.providers.items() if store and store.oauth else [])]}
            if parts == ['api', 'integrations', 'recommend'] and method == 'POST':
                self.fields(value, {'goal', 'application_id'})
                if not isinstance(value['goal'], str) or len(value['goal']) > 4000:
                    raise ValueError('Use a bounded goal string')
                from .integrations.recommendations import recommend
                return 200, {'recommendations': recommend(value['goal'], store.list(actor.tenant, actor.name) if store else [], value['application_id'],
                                                          catalogue=store.catalogue() if store else None)}
            if store is None:
                return 503, {'error': 'Configure CONCLAVE_INTEGRATION_KEY to enable encrypted connections'}
            try:
                if parts == ['api', 'connections']:
                    if method == 'GET':
                        return 200, {'connections': store.list(actor.tenant, actor.name)}
                    if method == 'POST':
                        return 201, store.create(value, actor.tenant, actor.name)
                if parts == ['api', 'connections', 'oauth', 'begin'] and method == 'POST':
                    return 200, store.begin_oauth(value, actor.tenant, actor.name)
                if parts == ['api', 'connections', 'oauth', 'callback'] and method == 'POST':
                    return 201, await store.finish_oauth(value, actor.tenant, actor.name)
                if len(parts) == 4 and parts[3] == 'revoke' and method == 'POST':
                    self.fields(value, set())
                    return 200, await store.revoke(parts[2], actor.tenant, actor.name)
                if len(parts) == 3 and method == 'DELETE':
                    store.disconnect(parts[2], actor.tenant, actor.name)
                    return 204, None
                if len(parts) == 4 and parts[3] == 'health' and method == 'POST':
                    self.fields(value, set())
                    return 200, await store.health(parts[2], actor.tenant, actor.name)
            except IntegrationError as error:
                return 400, {'error': str(error)}
            return 404, {'error': 'Connection endpoint not found'}
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            return 405, {"error": "Method not allowed"}
        if method == "GET" and path == "/api/health":
            return 200, {"status": "ok", "api_version": 1}
        if method == "GET" and path == "/api/runtime":
            return await self.host.handle("runtime", {}, streaming=streaming)
        if path == "/api/settings/sources":
            if method == "GET":
                return 200, {"sources": {key: {"name": item["name"], "path": str(item["path"]),
                                              "applications": item["applications"]}
                                          for key, item in (self.host.resources or {}).items()}}
            if method == "PUT":
                self.fields(value, {"sources"})
                if not isinstance(value["sources"], dict):
                    raise ValueError("Source settings must contain a resource object")
                if self.host.profile.name != "local":
                    # A tenant of a hosted deployment does not name a path on
                    # the server: its sources come from the deployment, which is
                    # also what keeps one tenant's folders away from another.
                    raise ValueError("This host's sources are configured by the deployment")
                resources = load_resources(None, configured=value["sources"])
                filename = self.host.paths.root / "sources.json"
                filename.parent.mkdir(parents=True, exist_ok=True)
                temporary = filename.with_suffix('.json.new')
                temporary.write_text(json.dumps(value["sources"], indent=2) + '\n')
                temporary.replace(filename)
                self.host.resources = resources
                return 200, {"saved": True}
        if method == "GET" and path == "/api/sdk/application":
            return 200, {"filename": "conclave_application.py", "source": SDK_SOURCE}
        if method == "GET" and len(parts) == 3 and parts[1] == "config":
            data = {"defaults": self.defaults, "catalogue": self.catalogue}
            if parts[2] in data:
                return 200, data[parts[2]]
        if path == "/api/definitions/validate" and method == "POST":
            self.fields(value, {"definition"})
            validate_definition(value["definition"])
            return 200, {"valid": True}
        if path == "/api/catalogue":
            if method == "GET":
                return 200, self.workspace.catalogue()
            if method == "PUT":
                self.fields(value, {"catalogue", "revision"})
                return 200, self.workspace.save_catalogue(value["catalogue"], value["revision"], self.catalogue)
        if path == "/api/applications":
            if method == "GET":
                return 200, {"applications": self.workspace.applications()}
            if method == "POST":
                self.fields(value, {"definition"})
                return 201, self.workspace.save(value["definition"])
        if len(parts) == 3 and parts[1] == "applications":
            if method == "GET":
                return 200, self.workspace.get(parts[2])
            if method == "PUT":
                self.fields(value, {"definition", "revision"})
                return 200, self.workspace.save(value["definition"], identifier=parts[2], revision=value["revision"])
            if method == "DELETE":
                self.fields(value, {"revision"})
                self.workspace.delete(parts[2], value["revision"])
                return 204, None
        if path == "/api/members":
            if method == "GET":
                return 200, {"members": self.workspace.members()}
            if method == "POST":
                self.fields(value, {"member"})
                return 201, self.workspace.add_member(value["member"])
        if len(parts) == 3 and parts[1] == "members" and method == "DELETE":
            self.fields(value, set())
            self.workspace.delete_member(parts[2])
            return 204, None
        if path == "/api/proposals" and method == "POST":
            if self.proposal_lock.locked():
                return 409, {"error": "A proposal is already being generated"}
            async with self.proposal_lock:
                return await self.host.handle("propose", value)
        if path == "/api/goals/refine" and method == "POST":
            # One model call at a time per tenant: this is the same kind of
            # request as a proposal, so it waits on the same lock.
            if self.proposal_lock.locked():
                return 409, {"error": "A proposal is already being generated"}
            async with self.proposal_lock:
                return await self.host.handle("refine", value)
        if path == "/api/goals/questions" and method == "POST":
            # The same one-model-call-at-a-time rule, for the same reason.
            if self.proposal_lock.locked():
                return 409, {"error": "A proposal is already being generated"}
            async with self.proposal_lock:
                return await self.host.handle("questions", value)
        if path == "/api/runs":
            if method == "GET":
                identifiers = query.get("application_id", [])
                if len(identifiers) != 1:
                    raise ValueError("Choose an application")
                return await self.host.handle("list", {"application_id": identifiers[0]}, principal=principal)
            if method == "POST":
                self.fields(value, {"version", "application_id", "revision", "task", "inputs"} | ({"resources"} if isinstance(value, dict) and "resources" in value else set()))
                if "resources" in value and (not isinstance(value["resources"], dict) or set(value["resources"]) - {"source"}):
                    raise ValueError("Select a named source resource")
                saved = self.workspace.get(value["application_id"])
                if type(value["revision"]) is not int or value["revision"] != saved["revision"]:
                    raise Conflict("This application changed. Reload its saved version before starting.")
                # The backend supplies the durable snapshot; callers cannot swap it.
                return await self.host.handle("start", {**value, "definition": saved["definition"]}, principal=principal)
        if len(parts) in {3, 4} and parts[1] == "runs":
            run_id = parts[2]
            if len(parts) == 4 and parts[3] == 'tool-approvals':
                await self.host.handle('get', {'run_id':run_id}, principal=principal)
                owner = (principal or self.host.principal).name
                if method == 'GET':
                    return 200, {'approvals':self.host.tool_approvals.list(run_id, owner)}
                if method == 'POST':
                    self.fields(value, {'approval_id','approved'})
                    self.host.tool_approvals.decide(run_id, value['approval_id'], owner, value['approved'])
                    return 200, {'recorded':True}
            if len(parts) == 3 and method == "GET":
                return await self.host.handle("get", {"run_id": run_id}, principal=principal)
            if len(parts) == 4:
                action = parts[3]
                if method == "GET" and action in {"events", "reviews"}:
                    _, record = await self.host.handle("get", {"run_id": run_id}, principal=principal)
                    if action == "reviews":
                        return 200, {"reviews": record["reviews"]}
                    cursor = query.get("after", ["0"])
                    if len(cursor) != 1 or not cursor[0].isdigit():
                        raise ValueError("Event cursor must be a nonnegative integer")
                    events = [event for event in record["events"] if event["sequence"] > int(cursor[0])]
                    return 200, {"events": events, "next_cursor": record["events"][-1]["sequence"] if record["events"] else 0, "status": record["status"]}
                if method == "POST" and action in {"resume", "cancel", "pause", "reviews"}:
                    self.fields(value, {"review_id", "output"} if action == "reviews" else set())
                    return await self.host.handle("review" if action == "reviews" else action, {**value, "run_id": run_id},
                                                  principal=principal)
        return 404, {"error": "Endpoint not found"}


def configured_api(*, workspace=None, principal=None):
    host = configured_host(workspace=workspace, principal=principal)
    defaults, catalogue = (configured_data(name) for name in ("defaults", "catalogue"))
    # One tenant's applications and catalogue; the host's principal decided
    # which, and a request cannot reach another tenant's store.
    store = Workspace(host.paths.applications, starters=starter_definitions(catalogue, defaults),
                      member_limit=int(setting("MEMBER_LIMIT", "200")))
    return ApplicationAPI(host, store, defaults=defaults, catalogue=catalogue)


class TenantAPIs:
    """One application service per tenant, built on first use.

    Storage is per tenant, so a request can only ever reach the tenant its
    service was built for. The acting principal is still passed per request,
    because several people may act inside one tenant.
    """

    def __init__(self, workspace=None, *, factory=None):
        self.workspace, self.factory, self.apis = workspace, factory or configured_api, {}

    def for_principal(self, principal) -> ApplicationAPI:
        service = self.apis.get(principal.tenant)

        if service is None:
            service = self.apis[principal.tenant] = self.factory(workspace=self.workspace, principal=principal)

        return service

    async def close(self) -> None:
        services, self.apis = list(self.apis.values()), {}

        for service in services:
            await service.host.close()


async def asgi_exchange(app, *, method, target, headers, body, port, writer, head=False):
    """One request through an ASGI callable, written straight to the socket.

    This is what lets a deployment's own adapter — its authentication, origin
    allowlist and framing — run on this machine over loopback, so sign-in and the
    hosted behaviour can be exercised before anything is deployed.

    The adapter's messages are written as they arrive rather than collected. A
    run's event stream never ends on its own, so a server that waited for the app
    to finish would hold the whole response until the run did — and a browser
    watching a step work would see nothing at all, with no error to fall back on.
    """

    parts = urlsplit(target)
    answered = False

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal answered
        if message["type"] == "http.response.start":
            lines = [f"HTTP/1.1 {message['status']} Response"]
            lines += [f"{key.decode() if isinstance(key, bytes) else key}: "
                      f"{value.decode() if isinstance(value, bytes) else value}"
                      for key, value in message.get("headers", [])]
            lines.append("Connection: close")
            # Written by hand rather than through the helper below: the head is
            # what tells a watching client the stream has started.
            writer.write("\r\n".join(lines).encode() + b"\r\n\r\n")
            await writer.drain()
            answered = True
            return
        if message["type"] != "http.response.body":
            return
        payload = message.get("body", b"")
        if payload and not head:
            writer.write(payload)
            await writer.drain()

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": unquote(parts.path), "query_string": parts.query.encode("latin-1"), "root_path": "",
             # The adapter reads `raw_path` as the request target, query and all,
             # which is what the servers it runs under hand it.
             "raw_path": target.encode("latin-1"),
             "headers": [(key.encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()],
             "client": ("127.0.0.1", 0), "server": ("127.0.0.1", port)}
    await app(scope, receive, send)
    if not answered:
        # Nothing was written, so this server still owns the framing.
        return 500, [], b'{"error": "The adapter sent no response"}'
    return None, [], b""


async def start_server(api, port=8080, *, token=None, assets=None, app=None):
    """Start a loopback HTTP/1.1 server; one bounded request per connection.

    `assets` serves the Studio bundle from this same origin, so one
    process and one address run the whole local application. When `app` is given,
    requests go to that ASGI callable instead of the local dispatch: the
    deployment's adapter owns authentication, origins and framing, and this
    process is only the socket. The loopback bind and Host check stay, so the
    address is still this machine's own.
    """
    assets = assets or {}

    async def respond(reader, writer):
        status, result = 400, {"error": "Invalid HTTP request"}
        head, hosted = False, None

        try:
            async with asyncio.timeout(15):
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 16384:
                    raise ValueError()
                lines = raw.decode("ascii").split("\r\n")
                method, target, version = lines[0].split(' ')
                head = method == "HEAD"
                if version != "HTTP/1.1" or not target.startswith('/'):
                    raise ValueError()
                headers = {}
                for line in lines[1:-2]:
                    key, item = line.split(':', 1)
                    key = key.lower()
                    if key in headers:
                        raise ValueError()
                    headers[key] = item.strip()
                local_port = writer.get_extra_info("sockname")[1]
                valid_hosts = {f"127.0.0.1:{local_port}", f"localhost:{local_port}"}
                length = int(headers.get("content-length", "0"))
                if length < 0 or "transfer-encoding" in headers:
                    raise ValueError()
                limit = request_body_limit()
                if limit is not None and length > limit:
                    status, result = 413, {"error": f"Request exceeds host limit of {limit} bytes"}
                elif headers.get("host") not in valid_hosts:
                    status, result = 403, {"error": "Use the local Conclave address"}
                elif app is not None:
                    # The adapter decides everything else, including which
                    # origins may call it: this process is only serving it.
                    content = await reader.readexactly(length)
                    status, result = None, (method, target, content)
                elif headers.get("origin") and headers["origin"] not in {"http://" + host for host in valid_hosts}:
                    status, result = 403, {"error": "Use the local Conclave address"}
                elif token and not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + token):
                    status, result = 401, {"error": "Authentication required"}
                elif method in {"POST", "PUT", "DELETE"} and not headers.get("content-type", "").startswith("application/json"):
                    status, result = 415, {"error": "Use a JSON request"}
                else:
                    content = await reader.readexactly(length)
                    value = json.loads(content, parse_constant=reject_nonfinite) if content else None
                    status, result = None, (method, target, value)
            if status is None:
                method, target, value = result
                if app is not None:
                    page = None if urlsplit(target).path.startswith(("/api/", "/auth/")) else static_page(None, method, target, assets)
                    if page is not None:
                        status, result = page.status, page
                    else:
                        # The adapter answers in its own framing: nothing is added
                        # here, and its own failures stay its own to describe.
                        try:
                            hosted = await asgi_exchange(app, method=method, target=target, headers=headers,
                                                         body=value, port=local_port, writer=writer, head=head)
                        except Exception:
                            status, result = 500, {"error": "The adapter could not answer this request"}
                else:
                    page = None if urlsplit(target).path.startswith("/api/") else static_page(api, method, target, assets)
                    if page is not None:
                        status, result = page.status, page
                    else:
                        # Provider work is independent of the request-read timeout.
                        status, result = await api.dispatch(method, target, value)
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            status, result = 400, {"error": "Invalid HTTP or JSON request"}
        if hosted is not None and hosted[0] is None:
            # The adapter already wrote its own head and body, frame by frame.
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass
            return
        if hosted is not None:
            status, headers_out, payload = hosted
        else:
            payload, content_type, filename = b"", "application/json", None
            if isinstance(result, StaticResponse):
                status, payload, content_type, filename = result
            elif status != 204:
                payload = json.dumps(result, allow_nan=False).encode()
            # This server's own headers keep their original spelling; a hosted
            # response is passed through exactly as its adapter framed it.
            headers_out = [("Content-Type", content_type), ("Content-Length", str(len(payload))),
                           ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]
            if filename:
                headers_out.append(("Content-Disposition", f'attachment; filename="{filename}"'))
        response = [f"HTTP/1.1 {status} Response"]
        response += [f"{key.decode() if isinstance(key, bytes) else key}: "
                     f"{value.decode() if isinstance(value, bytes) else value}" for key, value in headers_out]
        response.append("Connection: close")
        try:
            writer.write("\r\n".join(response).encode() + b"\r\n\r\n" + (b"" if head else payload))
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                # A browser that closes a stream it is watching looks like this
                # from here, and a pipe that is already broken is not this
                # server's failure: the run carries on in its own task.
                pass
    return await asyncio.start_server(respond, "127.0.0.1", port, limit=16384)


async def serve(port=8080, *, hosted=False):
    """Serve this machine: the local application, or a deployment's adapter.

    `hosted=True` runs `configured_application()` — the same ASGI callable a
    deployment serves — on a loopback address, so identity, origins and framing
    can be exercised here before anything is deployed. It prints where to start
    signing in, because a page that answers 401 is the intended first view.
    """

    if hosted:
        # Imported here: the adapter imports this module, so the arrow only
        # points this way at call time.
        from .application.profile import resolve_profile
        from .asgi import LOGIN_START, configured_application
        from .identity import GoogleLoginError
        settings_origins = [item.strip() for item in setting("UI_ORIGINS", "").split(",") if item.strip()]
        try:
            app = configured_application()
        except GoogleLoginError as error:
            # A deployment's own settings are the operator's to fix, and the
            # command should say which one in a line rather than a traceback.
            raise ValueError(str(error)) from error
        # The same switch the rest of the host uses: a local profile serves the
        # bundle, a hosted one is served somewhere else.
        assets = studio_assets() if resolve_profile(setting("PROFILE")).serve_ui else {}
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopped.set)
        server = await start_server(None, port, assets=assets, app=app)
        async with server:
            address = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            print(f"Conclave hosted adapter: {address}/api/health", flush=True)
            if assets:
                print(f"Studio: {address}", flush=True)
            if app.login is not None:
                print(f"Google sign-in: {address}{LOGIN_START}", flush=True)
                if settings_origins:
                    print(f"Page origins allowed to call this API: {' '.join(settings_origins)}", flush=True)
                else:
                    # An unnamed origin may read but not write, which is a
                    # confusing way to discover this setting.
                    print(f"Page origins allowed to call this API: none. "
                          f"Set CONCLAVE_UI_ORIGINS={address} so this page may write.", flush=True)
            else:
                print("Google sign-in: off. Set CONCLAVE_GOOGLE_LOGIN_FILE and CONCLAVE_SESSION_KEY.", flush=True)
            await stopped.wait()
        return

    api = configured_api(workspace=Path.cwd())
    token = setting("API_TOKEN")
    # Serving the page from this process is a local convenience: a hosted
    # deployment ships the bundle separately, and a bearer token cannot be
    # presented by a browser page anyway.
    assets = studio_assets() if api.host.profile.serve_ui and not token else {}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    try:
        server = await start_server(api, port, token=token, assets=assets)
        async with server:
            address = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            for line in startup_lines(api, address, assets, token):
                print(line, flush=True)
            await stopped.wait()
    finally:
        await api.host.close()
