"""Host-owned source resources, bound independently for each application run."""
from pathlib import Path
import json
import re
import hashlib

from conclave import Capability, Tool, ToolRegistry

REPOSITORY_TOOLS = frozenset({"git_status", "git_log", "git_diff", "git_show", "github_issue", "github_pr",
                              "github_search_issues", "github_search_prs"})
SOURCE_TOOLS = frozenset({"list_files", "read_file", "search_files", "knowledge_search"}) | REPOSITORY_TOOLS


def load_resources(filename, source=None, *, configured=None):
    resources = configured if configured is not None else json.loads(Path(filename).read_text()) if filename else {}
    if not isinstance(resources, dict):
        raise ValueError("Source settings must contain a resource object")
    if source:
        resources.setdefault("default", {"path": source, "name": "Local source folder"})
    result = {}
    for identifier, value in resources.items():
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier):
            raise ValueError("Resource identifiers must be valid")
        if not isinstance(value, dict) or set(value) - {"path", "name", "applications"}:
            raise ValueError("Invalid host resource settings")
        if not isinstance(value.get("path"), str):
            raise ValueError("Resources require a source folder")
        root = Path(value["path"]).resolve()
        if not root.is_dir():
            raise ValueError("Resources require an existing source folder")
        name = value.get("name", identifier)
        allowed = value.get("applications")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Resources require a bounded name")
        if allowed is not None and (not isinstance(allowed, list) or any(not isinstance(item, str) or not item for item in allowed)):
            raise ValueError("Resource applications must be application IDs")
        result[identifier] = {"path": root, "name": name, "applications": allowed,
                              "revision": hashlib.sha256(str(root).encode()).hexdigest()}
    return result


def request_resources(request) -> dict:
    """The resources a request pins: its own, or the ones its definition names.

    One definition of "the source this run uses", so binding the tools and
    granting the sandbox cannot disagree.
    """

    return request.get('resources', request['definition'].get('resources', {}))


def working_folder(workspace) -> dict:
    """The folder this host was started in, as one authorized source.

    A first run should not need a setting to read the folder the operator
    is already standing in. Containment, hidden-file denial and the
    document filters still apply inside it, exactly as for a folder
    authorized by name.
    """

    return load_resources(
        None,
        configured={"default": {"path": str(workspace), "name": "This folder"}},
    )


class SourceTools:
    def __init__(self, root, extensions):
        from conclave.tools.local import LocalTools
        self.local = LocalTools(root)
        self.root, self.extensions = root, extensions

    def safe_path(self, path):
        # Check both the requested path and its target: symlinks must not expose
        # hidden credentials, even when the visible alias has an allowed suffix.
        requested = Path(path)
        if requested.is_absolute() or any(part.startswith('.') and part not in {'.', '..'} for part in requested.parts):
            raise ValueError("Choose a plain-text source document in the source folder")
        resolved = self.local._safe_path(path)
        if any(part.startswith('.') for part in resolved.relative_to(self.root).parts):
            raise ValueError("Choose a plain-text source document in the source folder")
        return resolved

    def document(self, path):
        resolved = self.safe_path(path)
        if resolved.suffix.lower() not in self.extensions:
            raise ValueError("Choose a plain-text source document in the source folder")
        return resolved

    def files(self, path):
        root = self.safe_path(path)
        for item in sorted(root.rglob('*')):
            relative = item.relative_to(self.root).as_posix()
            try:
                target = self.document(relative)
            except (ValueError, RuntimeError):
                continue
            if target.is_file():
                yield relative, target

    async def read_file(self, path, start_line=1, end_line=200):
        self.document(path)
        return await self.local.read_file(path, start_line=start_line, end_line=end_line)

    async def list_files(self, path='.', max_entries=200):
        limit = max(1, min(max_entries if max_entries is not None else 200, 1000))
        result = []
        for relative, _ in self.files(path):
            result.append(relative)
            if len(result) >= limit:
                break
        return '\n'.join(result)

    async def search_files(self, query, path='.', max_results=50):
        from conclave.tools.local import read_text_or_none
        pattern = re.compile(query, re.IGNORECASE)
        limit = max(1, min(max_results if max_results is not None else 50, 1000))
        result = []
        for relative, target in self.files(path):
            text = read_text_or_none(target)
            if text is None:
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    result.append(f'{relative}:{line_number}: {line}')
                    if len(result) >= limit:
                        return '\n'.join(result)
        return '\n'.join(result)

    async def knowledge_search(self, query, max_results=20):
        terms = query.split()
        if not terms:
            return ''
        return await self.search_files('|'.join(re.escape(term) for term in terms), max_results=max_results)


def register_source_tools(registry, source=None):
    async def unbound(**kwargs):
        raise ValueError("Select a source resource for this request")

    descriptions = {
        "list_files": ("List supplied source documents", {"path": "string, optional", "max_entries": "integer, optional"}),
        "read_file": ("Read a supplied source document", {"path": "string", "start_line": "integer, optional", "end_line": "integer, optional"}),
        "search_files": ("Regex search supplied source documents", {"query": "string", "path": "string, optional", "max_results": "integer, optional"}),
        "knowledge_search": ("Keyword search supplied knowledge documents (not semantic search)", {"query": "string", "max_results": "integer, optional"}),
    }
    for name, (description, parameters) in descriptions.items():
        registry.register(Tool(name, description, parameters, getattr(source, name) if source else unbound, frozenset({Capability.READ})))

    from conclave.tools import build_tool_registry
    bundled = build_tool_registry(source.root if source else Path.cwd(), registry.max_output_chars)

    def scoped(func):
        async def execute(**kwargs):
            return await func(**kwargs)
        return execute

    for name in sorted(REPOSITORY_TOOLS):
        tool = bundled.tool(name)
        registry.register(Tool(name, tool.description, tool.parameters,
                               scoped(tool.func) if source else unbound, tool.capabilities))


def bind_resources(tools, resources, request, extensions):
    """The tools one request may use, with source tools bound to its source."""

    requested = {name for agent in request['definition']['agents'] for name in agent['tools']
                 if tools.tool(name) is not None and getattr(tools.tool(name).func, '__module__', None) == __name__}
    binding = request_resources(request).get('source')
    if binding is None and requested & SOURCE_TOOLS and 'default' in resources:
        binding = 'default'
    if binding is None:
        if requested & SOURCE_TOOLS:
            raise ValueError("This application reads source documents, but no source folder is authorized"
                             " for it. Set CONCLAVE_SOURCE_DIR, pass --source-dir, or add a source in Studio's"
                             " source settings.")
        return tools
    resource = resources.get(binding)
    if resource is None:
        raise ValueError("Selected source resource is unavailable")
    allowed = resource['applications']
    if allowed is not None and request['application_id'] not in allowed:
        raise ValueError("This application is not authorized for the selected source resource")
    if not resource['path'].is_dir():
        raise ValueError("Selected source resource is unavailable")
    revision = request.get('resources', {}).get('source_revision')
    if revision is not None and revision != resource['revision']:
        raise ValueError("Selected source resource changed; start a new request")
    bound = tools.copy()
    scoped = ToolRegistry(tools.max_output_chars)
    register_source_tools(scoped, SourceTools(resource['path'], extensions))
    for name, tool in scoped.tools.items():
        original = tools.tool(name)
        # Trusted extensions may override a bundled name. Do not replace their
        # implementation or capability/approval declaration with a READ tool.
        if original is not None and getattr(original.func, '__module__', None) == __name__:
            bound.register(tool)
    return bound
