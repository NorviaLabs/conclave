"""Host-approved protocol operations; discovery never constitutes a grant."""
import re
from urllib.parse import quote
from .connections import validate_arguments
from .transport import IntegrationError


class MCP:
    def __init__(self, endpoint, transport, approved):
        # approved is host configuration mapping names to schema + capability.
        self.endpoint, self.transport, self.approved = endpoint, transport, approved
        self.counter = 0

    async def rpc(self, method, params):
        self.counter += 1
        request_id = self.counter
        response = await self.transport.request('POST', self.endpoint, body={'jsonrpc':'2.0','id':request_id,'method':method,'params':params})
        if response.get('id') != request_id or response.get('error'):
            raise IntegrationError('MCP server rejected the request')
        return response.get('result', {})

    async def discover(self):
        await self.rpc('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'conclave','version':'0.1'}})
        await self.transport.request('POST', self.endpoint, body={'jsonrpc':'2.0','method':'notifications/initialized'})
        result = await self.rpc('tools/list', {})
        # Ignore remote descriptions and claimed authority; host metadata is trusted.
        return [{'name':name,**metadata} for name,metadata in self.approved.items()
                if any(tool.get('name') == name for tool in result.get('tools', []))]

    async def invoke(self, name, arguments):
        if name not in self.approved:
            raise IntegrationError('MCP operation has not been approved by the host')
        validate_arguments(arguments, self.approved[name]['parameters'])
        return await self.rpc('tools/call', {'name':name,'arguments':arguments})


class OpenAPI:
    def __init__(self, base_url, spec, approved, transport, headers=None):
        self.base_url, self.transport, self.headers = base_url.rstrip('/'), transport, headers or {}
        self.operations = {}
        for path, methods in spec.get('paths', {}).items():
            if not path.startswith('/') or '..' in path or '://' in path:
                raise IntegrationError('Unsafe OpenAPI operation path')
            for method, operation in methods.items():
                identifier = operation.get('operationId') if isinstance(operation, dict) else None
                if identifier not in approved:
                    continue
                if method.upper() not in ('GET','POST','PUT','PATCH','DELETE'):
                    raise IntegrationError('Unsupported OpenAPI method')
                if '$ref' in str(operation):
                    raise IntegrationError('OpenAPI references must be resolved by the trusted host')
                properties, required = {}, []
                for parameter in operation.get('parameters', []):
                    if parameter.get('in') not in ('query','path'):
                        raise IntegrationError('Header and cookie arguments are not allowed')
                    properties[parameter['name']] = parameter['schema']
                    if parameter.get('required'):required.append(parameter['name'])
                request_body = operation.get('requestBody')
                if request_body:
                    schema = request_body.get('content', {}).get('application/json', {}).get('schema')
                    if not schema or schema.get('type') != 'object' or 'body' in properties:
                        raise IntegrationError('Use an explicit JSON object request-body schema')
                    properties['body'] = schema
                    if request_body.get('required'):
                        required.append('body')
                self.operations[identifier] = {'method':method.upper(),'path':path,'parameters':{'properties':properties,'required':required},'capability':'read' if method.upper()=='GET' else 'write'}

    async def invoke(self, identifier, arguments):
        if identifier not in self.operations:
            raise IntegrationError('OpenAPI operation is not authorized')
        operation=self.operations[identifier];validate_arguments(arguments,operation['parameters'])
        path=operation['path'];query={};body=None
        for key,value in arguments.items():
            if key == 'body':
                body=value
                continue
            if '{'+key+'}' in path:
                if str(value) in ('.','..') or '/' in str(value):raise IntegrationError('Invalid resource identifier')
                path=path.replace('{'+key+'}',quote(str(value),safe=''))
            else:query[key]=value
        return await self.transport.request(operation['method'],self.base_url+path,headers=self.headers,params=query,body=body)


def readonly_query(query, views):
    # Deliberately restricted grammar: no functions, literals, comments, joins,
    # CTEs or subqueries. Values use bound parameters, not string concatenation.
    if not isinstance(query,str) or len(query)>10000:
        raise IntegrationError('Use a bounded read-only query')
    match=re.fullmatch(r'\s*SELECT\s+(\*|[a-zA-Z_][a-zA-Z0-9_]*(?:\s*,\s*[a-zA-Z_][a-zA-Z0-9_]*)*)\s+FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s+WHERE\s+[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*%s)?(?:\s+LIMIT\s+([0-9]+))?\s*',query,re.I)
    if not match or match[2] not in views or match[3] and int(match[3])>100:
        raise IntegrationError('Only single-table SELECTs on authorized views are allowed')
    return query if match[3] else query.rstrip() + ' LIMIT 100'


class SQL:
    def __init__(self, dialect, credentials, views, *, connect=None):
        if dialect not in ('postgres','mysql') or not views:
            raise IntegrationError('Configure database type and authorized read views')
        self.dialect,self.credentials,self.views,self.connect=dialect,credentials,set(views),connect

    def query(self, query, parameters=()):
        query = readonly_query(query,self.views)
        if len(parameters)!=(1 if '%s' in query else 0):
            raise IntegrationError('Incorrect bound query parameters')
        connector=self.connect
        if connector is None:
            try:
                if self.dialect=='postgres':import psycopg;connector=psycopg.connect
                else:import pymysql;connector=pymysql.connect
            except ImportError:
                raise IntegrationError('Install the database driver on the trusted host') from None
        db=connector(**self.credentials)
        try:
            with db.cursor() as cursor:
                if self.dialect=='postgres':
                    cursor.execute('BEGIN READ ONLY');cursor.execute('SET LOCAL statement_timeout = 5000')
                else:
                    cursor.execute('SET SESSION MAX_EXECUTION_TIME=5000');cursor.execute('START TRANSACTION READ ONLY')
                cursor.execute(query,parameters)
                rows=cursor.fetchmany(101)
                return {'rows':rows[:100],'truncated':len(rows)>100}
        finally:
            db.rollback();db.close()
