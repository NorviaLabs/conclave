"""Owned recorded fixtures, separate from production run history."""
import copy
import difflib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from .capture import redact
from .definition import (_dependency_state, _execution_order, _validated_output,
                         plan_connections, validate_definition, validate_request_inputs)


class StepTestStore:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS step_test_objects (id TEXT PRIMARY KEY, application TEXT NOT NULL, owner TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)')

    def save(self, application, owner, kind, value):
        value = copy.deepcopy(value)
        value.setdefault('id', uuid.uuid4().hex)
        value.setdefault('created_at', datetime.now(timezone.utc).isoformat())
        with sqlite3.connect(self.path) as db:
            existing = db.execute('SELECT application, owner, kind FROM step_test_objects WHERE id=?', (value['id'],)).fetchone()
            if existing is not None and existing != (application, owner, kind):
                raise KeyError(value['id'])
            db.execute('INSERT OR REPLACE INTO step_test_objects VALUES (?, ?, ?, ?, ?)',
                       (value['id'], application, owner, kind, json.dumps(redact(value), allow_nan=False)))
        return self.get(application, owner, kind, value['id'])

    def get(self, application, owner, kind, identifier):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT payload FROM step_test_objects WHERE id=? AND application=? AND owner=? AND kind=?', (identifier, application, owner, kind)).fetchone()
        if row is None:
            raise KeyError(identifier)
        return json.loads(row[0])

    def list(self, application, owner, kind):
        with sqlite3.connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM step_test_objects WHERE application=? AND owner=? AND kind=? ORDER BY rowid DESC', (application, owner, kind))]

    def delete(self, application, owner, kind, identifier):
        self.get(application, owner, kind, identifier)
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM step_test_objects WHERE id=? AND application=? AND owner=? AND kind=?', (identifier, application, owner, kind))


def fixture_preview(record, step_id):
    definition = record['request']['definition']
    step = next((item for item in definition['workflow'] if item['id'] == step_id), None)
    if step is None:
        raise ValueError('Unknown step')
    events = [item['event'] for item in record['events']]
    started = next((item for item in reversed(events) if item.get('stage') == step_id and item['type'] == 'StageStarted'), None)
    finished = next((item for item in reversed(events) if item.get('stage') == step_id and item['type'] == 'ApplicationStateChanged'), None)
    if not started or 'effective_input' not in started or not finished:
        raise ValueError('This step has no recorded input snapshot; run it again before pinning')
    patches = {item['stage']: item.get('patch', item.get('checkpoint', {}).get('patches', {}).get(item['stage'])) for item in events if item['type'] == 'ApplicationStateChanged'}
    if any(value is None for value in patches.values()):
        raise ValueError('Source patches are unavailable')
    captured = started['effective_input']
    if redact(_dependency_state(definition, step_id, patches)) != captured['state']:
        raise ValueError('Source inputs cannot be reconstructed exactly')
    return redact({'version': 1, 'application_id': record['request']['application_id'], 'step_id': step_id,
                   'source_run': record['id'], 'revision': record['request']['revision'],
                   'definition': definition, 'task': record['request']['task'], 'inputs': captured['inputs'],
                   'input_state': captured['state'], 'patches': patches,
                   'reference': patches[step_id], 'provider': started.get('provider', ''), 'model': started.get('model', ''),
                   'recorded': True, 'reference_recorded': True})


def authored_fixture(definition, application, revision, step_id, *, inputs=None, upstream=None,
                     reference=None, reference_recorded=False, task=''):
    """A fixture a person writes, for a step that has never run.

    Nothing here is recorded from a run, so the caller supplies what the step
    would have received: the request inputs the application declares, and the
    output of every step this one depends on. A missing prerequisite is refused
    rather than filled with an empty value, because an invented upstream result
    would make the test pass or fail for a reason that never happened.
    """

    validate_definition(definition)
    step = next((item for item in definition['workflow'] if item['id'] == step_id), None)
    if step is None:
        raise ValueError('Unknown step')
    request_inputs = validate_request_inputs(definition, inputs if inputs is not None else {})
    ancestors = _ancestors(definition, {step_id})
    provided = upstream if upstream is not None else {}
    if not isinstance(provided, dict) or set(provided) - ancestors:
        raise ValueError('Prerequisite values must name the steps this one depends on')
    missing = [item['id'] for item in _execution_order(definition) if item['id'] in ancestors - set(provided)]
    if missing:
        raise ValueError('Provide the output of every prerequisite step: ' + ', '.join(missing))
    patches = {}
    for item in _execution_order(definition):
        if item['id'] not in provided:
            continue
        collection = next(entry for entry in definition['state'] if entry['key'] == item['output'])
        value = provided[item['id']]
        _validated_output(SimpleNamespace(data={'text': value} if collection['type'] == 'text' else {'records': value}),
                          collection, {'agent': item['agent'], 'stage': item['id']})
        # Validation must not rewrite what the person supplied, exactly as seeds
        # are not rewritten: the test shows their values, not a normalized copy.
        patches[item['id']] = copy.deepcopy(value)
    task = task if isinstance(task, str) and task.strip() else step['task']
    return redact({'version': 1, 'application_id': application, 'step_id': step_id, 'source_run': None,
                   'revision': revision, 'definition': definition, 'task': task, 'inputs': request_inputs,
                   'input_state': _dependency_state(definition, step_id, patches), 'patches': patches,
                   'reference': reference, 'reference_recorded': bool(reference_recorded),
                   'provider': '', 'model': '', 'recorded': False, 'authored': True})


def _ancestors(definition, stages):
    """Every stage that must have finished before any of `stages` can start."""

    links = plan_connections(definition)
    found = set()
    pending = list(stages)
    while pending:
        for edge in links:
            if edge['to'] == pending[0] and edge['from'] not in found:
                found.add(edge['from'])
                pending.append(edge['from'])
        pending.pop(0)
    return found


def test_scope(fixture, definition, scope):
    validate_definition(definition)
    source = fixture['definition']
    if source['state'] != definition['state'] or plan_connections(source) != plan_connections(definition):
        raise ValueError('Fixture schema or dependencies changed; capture a new fixture')
    original = {item['id']: item for item in source['workflow']}
    current = {item['id']: item for item in definition['workflow']}
    if set(original) != set(current) or any(original[key]['output'] != current[key]['output'] for key in original):
        raise ValueError('Fixture step/output assignments changed')
    selected = {fixture['step_id']}
    if scope == 'downstream':
        while True:
            expanded = selected | {edge['to'] for edge in plan_connections(definition) if edge['from'] in selected}
            if expanded == selected:
                break
            selected = expanded
    elif scope != 'step':
        raise ValueError('Choose step or downstream')
    seeds = {key: value for key, value in fixture['patches'].items() if key not in selected and key in _ancestors(definition, selected)}
    warnings = ['Agent assignment changed' for key in selected if original[key]['agent'] != current[key]['agent']]
    return selected, seeds, warnings


def compare(reference, output):
    if isinstance(reference, str) and isinstance(output, str):
        return {'kind': 'text', 'equal': reference == output, 'diff': ''.join(difflib.unified_diff(reference.splitlines(True), output.splitlines(True), fromfile='reference', tofile='test'))}
    if isinstance(reference, list) and isinstance(output, list):
        old, new = {item['id']: item for item in reference}, {item['id']: item for item in output}
        return {'kind': 'records', 'equal': reference == output, 'changes': [{'id': key, 'reference': old.get(key), 'output': new.get(key)} for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]}
    return {'kind': 'value', 'equal': reference == output, 'reference': reference, 'output': output}
