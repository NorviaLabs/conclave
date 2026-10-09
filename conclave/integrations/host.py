"""Deployment-owned protocol instances; browser payloads cannot choose endpoints."""
import asyncio
import importlib.util
from urllib.parse import urlsplit

from .services import CATEGORIES, Integration, Operation
from .connections import redact, validate_arguments
from .transport import IntegrationError, Transport, endpoint


class HostAdapters:
    def __init__(self, configuration, *, transport_factory=Transport):
        from .protocols import MCP, OpenAPI, SQL
        from .s3 import S3
        self.services, self.clients, self.secrets, self.instances = {}, {}, {}, {}
        for instance, config in configuration.items():
            identifier = config.get('integration', instance)
            if identifier not in ('mcp', 'http_openapi', 'postgres', 'mysql', 's3'):
                raise IntegrationError('Unsupported deployment adapter')
            name = {'mcp':'MCP','http_openapi':'HTTP / OpenAPI','postgres':'PostgreSQL','mysql':'MySQL','s3':'Amazon S3'}[identifier]
            operations = []
            if identifier in ('mcp', 'http_openapi'):
                url = config['endpoint']
                endpoint(url, config['hosts'])
                transport = transport_factory(config['hosts'])
                if identifier == 'mcp':
                    approved = config['approved']
                    client = MCP(url, transport, approved)
                    for operation, metadata in approved.items():
                        if metadata['capability'] not in ('read', 'write'):
                            raise IntegrationError('MCP authority must be explicitly classified')
                        operations.append(Operation(operation, metadata['description'], 'POST', '', metadata['capability'], metadata['parameters']))
                else:
                    client = OpenAPI(url, config['spec'], config['approved'], transport, config.get('headers'))
                    for operation, metadata in client.operations.items():
                        operations.append(Operation(operation, operation.replace('_',' '), metadata['method'], metadata['path'], metadata['capability'], metadata['parameters']))
            elif identifier in ('postgres', 'mysql'):
                driver = 'psycopg' if identifier == 'postgres' else 'pymysql'
                if not importlib.util.find_spec(driver):
                    continue
                credentials = dict(config['credentials'])
                credentials.setdefault('connect_timeout', 5)
                if identifier == 'mysql':
                    credentials.setdefault('read_timeout', 10)
                client = SQL(identifier, credentials, config['views'])
                operations.append(Operation('query','Query authorized read views','GET','', 'read',
                    {'properties':{'query':{'type':'string','maxLength':10000},'parameters':{'type':'array','maxItems':1}},'required':['query']}))
            else:
                if not importlib.util.find_spec('boto3'):
                    continue
                client = S3(bucket=config['bucket'], prefix=config['prefix'], region=config.get('region'), role_arn=config.get('role_arn'))
                for operation, required in [('list_objects',[]),('metadata',['key']),('read_object',['key'])]:
                    fields = {'key':{'type':'string'}} if required else {'continuation_token':{'type':'string'}}
                    operations.append(Operation(operation, operation.replace('_',' '), 'GET','', 'read', {'properties':fields,'required':required}))
            if not operations:
                raise IntegrationError('Host adapter must approve at least one operation')
            import re
            if any(not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,50}', op.name) for op in operations):
                raise IntegrationError('Adapter operation names must be bounded identifiers')
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', instance):
                raise IntegrationError('Host instance must have a bounded identifier')
            self.instances[instance] = Integration(identifier, config.get('name', name), '', 'host_adapter',
                                                   tuple(operations), CATEGORIES.get(identifier, 'Automation'))
            self.services[identifier] = self.instances[instance]
            self.clients[instance] = client
            self.secrets[instance] = [str(value) for value in config.get('headers', {}).values()]
            self.secrets[instance] += [str(value) for key, value in config.get('credentials', {}).items()
                                        if key in ('password', 'token', 'secret')]

    async def invoke(self, instance, operation, arguments):
        client = self.clients[instance]
        service = self.instances[instance]
        identifier = service.id
        spec = next((op for op in service.operations if op.name == operation), None)
        if spec is None:
            raise IntegrationError('Host operation is not authorized')
        validate_arguments(arguments, spec.parameters)
        try:
            if identifier in ('postgres','mysql'):
                result = await asyncio.wait_for(asyncio.to_thread(client.query, **arguments), 15)
            elif identifier == 's3':
                result = await client.invoke(operation, **arguments)
            else:
                if identifier == 'mcp':
                    discovered = await client.discover()
                    if not any(item['name'] == operation for item in discovered):
                        raise IntegrationError('Approved MCP operation is not available on the server')
                result = await client.invoke(operation, arguments)
            return redact(result, self.secrets[instance])
        except IntegrationError:
            raise
        except Exception:
            raise IntegrationError('Host integration request failed') from None
