"""Conservative capture sanitization; never capture provider transcripts or tools."""
import re

_SENSITIVE = re.compile(r'password|secret|credential|authorization|cookie|access.?token|api.?key|private.?key|reasoning|chain.?of.?thought|headers', re.I)
_VALUE = re.compile(r'(?i)(bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|(?:password|api_key|access_token|secret)\s*[:=]\s*[^\s,;]+)')


def redact(value):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if _SENSITIVE.search(key) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _VALUE.sub('[REDACTED]', value)
    return value
