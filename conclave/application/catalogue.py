"""Host-owned catalog data and starter generation, independent of Studio."""
import copy
import json
from pathlib import Path

from .definition import validate_definition, validate_fields, OUTPUT_KINDS
from .service import setting


def configured_data(kind):
    names = {"defaults": "DEFAULTS_FILE", "catalogue": "CATALOGUE_FILE"}
    return json.loads(Path(setting(names[kind], Path(__file__).parent / "data" / (kind + ".json"))).read_text())


def starter_definitions(catalogue, defaults):
    roles = {role["id"]: role for role in catalogue["rolePresets"]}
    kinds = {kind["id"]: kind for kind in catalogue["outputTypes"]}
    result = []
    for app in catalogue["applicationTypes"]:
        definition = copy.deepcopy(defaults)
        definition.update(name=app["name"], description=app["purpose"], catalogue_type=app["id"],
                          inputs=app.get("inputs", [{"id": "context", "label": "Context and source references", "type": "textarea", "required": False},
                                                    {"id": "constraints", "label": "Constraints and acceptance criteria", "type": "text", "required": False}]))
        definition["agents"] = [{"id": member["id"], "name": member["name"], "kind": roles[member["preset"]]["kind"], "role": member["preset"],
                                 "instructions": member.get("instructions", roles[member["preset"]]["instructions"]),
                                 "tools": [] if roles[member["preset"]]["kind"] == "human" else member.get("tools", roles[member["preset"]].get("tools", []))} for member in app["members"]]
        definition["state"] = [{"key": output["key"], "label": output["name"], "kind": output["kind"], "type": kinds[output["kind"]]["type"],
                                "merge": "replace" if kinds[output["kind"]]["type"] == "text" else "upsert",
                                "statuses": app.get("output_statuses", {}).get(output["key"], kinds[output["kind"]]["statuses"])} for output in app["outputs"]]
        definition["workflow"] = app.get("plan", [{"id": "step_" + str(index + 1), "name": output["name"], "agent": output["owner"], "output": output["key"], "task": output["task"]} for index, output in enumerate(app["outputs"])])
        definition["views"] = [{"id": output["key"] + "_view", "title": output["name"], "collection": output["key"], "layout": kinds[output["kind"]]["layout"]} for output in app["outputs"]]
        for key in ("connections", "review_checks", "research", "connector_requirements", "integrations"):
            if key in app:
                definition[key] = app[key]
        validate_definition(definition)
        result.append({"id": app["id"], "definition": copy.deepcopy(definition)})
    return result


def validate_member(member):
    import re
    if not isinstance(member, dict):
        raise ValueError("Provide a member definition")
    if not isinstance(member.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", member["id"]) or member["id"] in {"constructor", "prototype", "__proto__"}:
        raise ValueError("Invalid member identifier")
    for key, limit in (("name", 100), ("instructions", 8000)):
        if not isinstance(member.get(key), str) or not member[key].strip() or len(member[key]) > limit:
            raise ValueError("Invalid member " + key)
    tools = member.get("tools", [])
    kind = member.get("kind", "ai")
    if kind not in {"ai", "human"} or not isinstance(tools, list) or len(tools) > 100 or any(not isinstance(tool, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,99}", tool) for tool in tools) or len(set(tools)) != len(tools) or kind == "human" and tools:
        raise ValueError("Invalid member kind or tools")
    return {"id": member["id"], "name": member["name"], "kind": kind, "role": str(member.get("role", "custom"))[:100], "instructions": member["instructions"], "tools": tools}


def validate_catalogue(value, bundled):
    import re
    if not isinstance(value, dict) or set(value) - {"version", "roles", "starters", "outputs"} or value.get("version") != 1:
        raise ValueError("Use version 1 workspace catalog fields")
    for key, source in (("roles", "rolePresets"), ("starters", "applicationTypes"), ("outputs", "outputPresets")):
        entries = value.get(key)
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError("Use up to 100 " + key)
        reserved = {entry["id"] for entry in bundled[source]}
        seen = set()
        for entry in entries:
            identifier = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier) or identifier in reserved | seen | {"constructor", "prototype", "__proto__"}:
                raise ValueError("Use unique custom identifiers for " + key)
            seen.add(identifier)
            if not isinstance(entry.get("name"), str) or not entry["name"].strip() or len(entry["name"]) > 100:
                raise ValueError("A bounded catalog name is required")
            if key == "roles":
                validate_member(entry)
            elif key == "starters":
                validate_definition(entry["definition"])
            elif key == "outputs":
                validate_fields(entry.get("fields", []))
                if entry.get("kind") not in OUTPUT_KINDS or not isinstance(entry.get("task"), str) or not entry["task"].strip() or len(entry["task"]) > 4000 or entry["kind"] == "document" and entry.get("fields"):
                    raise ValueError("Invalid output preset")
    return copy.deepcopy(value)
