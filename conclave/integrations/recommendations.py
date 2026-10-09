"""Integration suggestions are planning data, never automatic tool grants."""
import re

KEYWORDS = {
    'web_search': ('research', 'competitor', 'web'), 'firecrawl': ('web', 'website', 'research'),
    'google_drive': ('document', 'drive'), 'google_docs': ('report', 'document'),
    'google_sheets': ('spreadsheet', 'billing', 'data'), 'slack': ('slack', 'notification'),
    'gmail': ('email', 'gmail'), 'confluence': ('policy', 'knowledge'), 'notion': ('notion', 'knowledge'),
    'zendesk': ('ticket', 'support', 'complaint'), 'hubspot': ('customer', 'crm', 'sales'),
    'jira': ('engineering', 'issue', 'jira'), 'github': ('repository', 'code', 'github'),
    'linear': ('linear', 'engineering'), 'trello': ('trello', 'card'),
    'postgres': ('database', 'postgres'), 'mysql': ('database', 'mysql'),
    's3': ('bucket', 'storage', 's3'), 'reddit': ('reddit', 'community'), 'rss': ('feed', 'monitor'),
    'http_openapi': ('api',), 'mcp': ('mcp',), 'webhooks': ('webhook',),
}


def recommend(goal, connections=(), application=None, *, catalogue=None):
    from .services import integrations
    result = []
    for identifier, words in KEYWORDS.items():
        if not any(re.search(r'\b' + re.escape(word) + r'\b', goal, re.I) for word in words):
            continue
        service = (catalogue or integrations())[identifier]
        available = [connection['id'] for connection in connections if connection['integration'] == identifier
                     and application in connection['applications']]
        result.append({'integration': identifier, 'name': service.name,
                       'status': 'connected' if available else 'not_connected' if service.operations else 'adapter_unavailable',
                       'connections': available, 'access_granted': False,
                       'reason': 'Select an authorized connection and explicitly grant operations before running'})
    return result
