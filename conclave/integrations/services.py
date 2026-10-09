"""Service API contracts: fixed routes and explicit operation authority."""
from dataclasses import dataclass, replace
import base64
import re
from urllib.parse import quote, urlsplit
from .transport import IntegrationError, endpoint, public_addresses


@dataclass(frozen=True)
class Operation:
    name: str
    description: str
    method: str
    path: str
    capability: str
    parameters: dict
    scopes: tuple = ()


@dataclass(frozen=True)
class Integration:
    id: str
    name: str
    base_url: str
    auth: str
    operations: tuple
    # How the catalogue groups a service for someone choosing what to connect.
    category: str = "Other services"


# One place to say where a service appears in the catalogue, so a new service
# states its home once instead of every call site inventing a heading.
CATEGORIES = {
    'web_search': 'Web and research', 'firecrawl': 'Web and research', 'rss': 'Web and research',
    'reddit': 'Web and research',
    'google_drive': 'Documents and knowledge', 'google_docs': 'Documents and knowledge',
    'google_sheets': 'Documents and knowledge', 'confluence': 'Documents and knowledge',
    'notion': 'Documents and knowledge',
    'slack': 'Communication', 'gmail': 'Communication',
    'zendesk': 'Customer support', 'hubspot': 'Customer support',
    'jira': 'Engineering', 'github': 'Engineering', 'linear': 'Engineering', 'trello': 'Engineering',
    'postgres': 'Data and storage', 'mysql': 'Data and storage', 's3': 'Data and storage',
    'http_openapi': 'Automation', 'mcp': 'Automation', 'webhooks': 'Automation',
}


def op(name, method, path, fields=(), *, required=(), scope=(), description=None):
    properties = {field: {'type': 'string', 'maxLength': 10000} for field in fields}
    for field in ('values', 'properties', 'payload'):
        if field in properties:
            properties[field] = {'type': 'array' if field == 'values' else 'object'}
    return Operation(name, description or name.replace('_', ' ').capitalize(), method, path,
                     'read' if method == 'GET' else 'write',
                     {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}, tuple(scope))


def integrations():
    items = [
        Integration('web_search', 'Web Search (Brave)', 'https://api.search.brave.com', 'api_key', (
            op('search', 'GET', '/res/v1/web/search', ['query'], required=['query']),)),
        Integration('firecrawl', 'Firecrawl', 'https://api.firecrawl.dev', 'api_key', (
            Operation('extract', 'Extract approved public page content', 'POST', '/v1/scrape', 'read', op('x','GET','',['url'],required=['url']).parameters),)),
        Integration('google_drive', 'Google Drive', 'https://www.googleapis.com', 'google_oauth', (
            op('search_files','GET','/drive/v3/files',['query','page_token'],scope=['https://www.googleapis.com/auth/drive.readonly']),
            op('file_metadata','GET','/drive/v3/files/{file_id}',['file_id'],required=['file_id'],scope=['https://www.googleapis.com/auth/drive.readonly']),
            op('read_file','GET','/drive/v3/files/{file_id}',['file_id'],required=['file_id'],scope=['https://www.googleapis.com/auth/drive.readonly']),)),
        Integration('google_docs','Google Docs','https://docs.googleapis.com','google_oauth',(
            op('read_document','GET','/v1/documents/{document_id}',['document_id'],required=['document_id'],scope=['https://www.googleapis.com/auth/documents.readonly']),
            op('create_document','POST','/v1/documents',['title','text'],required=['title','text'],scope=['https://www.googleapis.com/auth/documents']),)),
        Integration('google_sheets','Google Sheets','https://sheets.googleapis.com','google_oauth',(
            op('read_spreadsheet','GET','/v4/spreadsheets/{spreadsheet_id}',['spreadsheet_id'],required=['spreadsheet_id'],scope=['https://www.googleapis.com/auth/spreadsheets.readonly']),
            op('read_range','GET','/v4/spreadsheets/{spreadsheet_id}/values/{range}',['spreadsheet_id','range'],required=['spreadsheet_id','range'],scope=['https://www.googleapis.com/auth/spreadsheets.readonly']),
            op('update_range','PUT','/v4/spreadsheets/{spreadsheet_id}/values/{range}',['spreadsheet_id','range','values'],required=['spreadsheet_id','range','values'],scope=['https://www.googleapis.com/auth/spreadsheets']),)),
        Integration('slack','Slack','https://slack.com','bearer',(
            op('search_messages','GET','/api/search.messages',['query'],required=['query'],scope=['search:read']),
            op('read_conversation','GET','/api/conversations.history',['channel','cursor'],required=['channel'],scope=['channels:history']),
            op('send_message','POST','/api/chat.postMessage',['channel','text'],required=['channel','text'],scope=['chat:write']),)),
        Integration('gmail','Gmail','https://gmail.googleapis.com','google_oauth',(
            op('search_messages','GET','/gmail/v1/users/me/messages',['query','page_token'],scope=['https://www.googleapis.com/auth/gmail.readonly']),
            op('read_message','GET','/gmail/v1/users/me/messages/{message_id}',['message_id'],required=['message_id'],scope=['https://www.googleapis.com/auth/gmail.readonly']),
            op('create_draft','POST','/gmail/v1/users/me/drafts',['raw'],required=['raw'],scope=['https://www.googleapis.com/auth/gmail.compose']),
            op('send_email','POST','/gmail/v1/users/me/messages/send',['raw'],required=['raw'],scope=['https://www.googleapis.com/auth/gmail.send']),)),
        Integration('confluence','Confluence','','atlassian',(
            op('search_pages','GET','/wiki/rest/api/content/search',['cql','start'],required=['cql']),
            op('read_page','GET','/wiki/rest/api/content/{page_id}',['page_id'],required=['page_id']),)),
        Integration('notion','Notion','https://api.notion.com','bearer',(
            Operation('search','Search accessible pages and databases','POST','/v1/search','read',op('x','GET','',['query','start_cursor']).parameters),
            op('read_page','GET','/v1/pages/{page_id}',['page_id'],required=['page_id']),
            op('read_content','GET','/v1/blocks/{block_id}/children',['block_id','start_cursor'],required=['block_id']),)),
        Integration('zendesk','Zendesk','','zendesk',(
            op('search_tickets','GET','/api/v2/search.json',['query'],required=['query']),
            op('read_ticket','GET','/api/v2/tickets/{ticket_id}.json',['ticket_id'],required=['ticket_id']),
            op('read_history','GET','/api/v2/tickets/{ticket_id}/comments.json',['ticket_id'],required=['ticket_id']),
            op('prepare_reply','PUT','/api/v2/tickets/{ticket_id}.json',['ticket_id','text'],required=['ticket_id','text']),)),
        Integration('hubspot','HubSpot CRM','https://api.hubapi.com','bearer',tuple(
            Operation('search_'+kind, 'Search '+kind,'POST',f'/crm/v3/objects/{kind}/search','read',op('x','GET','',['query'],required=['query']).parameters)
            for kind in ['companies','contacts','deals']) + (
            op('read_record','GET','/crm/v3/objects/{object_type}/{record_id}',['object_type','record_id'],required=['object_type','record_id']),
            op('update_record','PATCH','/crm/v3/objects/{object_type}/{record_id}',['object_type','record_id','properties'],required=['object_type','record_id','properties']),)),
        Integration('jira','Jira','','atlassian',(
            op('search_issues','GET','/rest/api/3/search/jql',['jql','nextPageToken'],required=['jql']),
            op('read_issue','GET','/rest/api/3/issue/{issue_id}',['issue_id'],required=['issue_id']),
            op('create_issue','POST','/rest/api/3/issue',['project','summary','description','issue_type'],required=['project','summary','issue_type']),
            op('update_issue','PUT','/rest/api/3/issue/{issue_id}',['issue_id','summary'],required=['issue_id','summary']),)),
        Integration('github','GitHub','https://api.github.com','bearer',(
            op('search_repositories','GET','/search/repositories',['query'],required=['query']),
            op('search_issues','GET','/search/issues',['query'],required=['query']),
            op('read_content','GET','/repos/{owner}/{repo}/contents/{path}',['owner','repo','path'],required=['owner','repo','path']),
            op('read_issue','GET','/repos/{owner}/{repo}/issues/{issue_number}',['owner','repo','issue_number'],required=['owner','repo','issue_number']),
            op('read_pull_request','GET','/repos/{owner}/{repo}/pulls/{number}',['owner','repo','number'],required=['owner','repo','number']),
            op('create_issue','POST','/repos/{owner}/{repo}/issues',['owner','repo','title','body'],required=['owner','repo','title']),)),
        Integration('linear','Linear','https://api.linear.app','bearer',(
            Operation('search_issues','Search issues','POST','/graphql','read',op('x','GET','',['query'],required=['query']).parameters),
            Operation('read_projects','Read projects','POST','/graphql','read',op('x','GET','').parameters),
            op('create_issue','POST','/graphql',['team_id','title','description'],required=['team_id','title']),)),
        Integration('trello','Trello','https://api.trello.com','trello',(
            op('read_boards','GET','/1/members/me/boards'),
            op('read_lists','GET','/1/boards/{board_id}/lists',['board_id'],required=['board_id']),
            op('read_cards','GET','/1/lists/{list_id}/cards',['list_id'],required=['list_id']),
            op('create_card','POST','/1/cards',['list_id','name','description'],required=['list_id','name']),
            op('update_card','PUT','/1/cards/{card_id}',['card_id','name','description'],required=['card_id','name']),)),
        Integration('reddit','Reddit','https://oauth.reddit.com','bearer',(
            op('read_posts','GET','/r/{subreddit}/new',['subreddit','after'],required=['subreddit']),
            op('read_comments','GET','/comments/{post_id}',['post_id'],required=['post_id']),
            op('read_community','GET','/r/{subreddit}/about',['subreddit'],required=['subreddit']),)),
        Integration('rss','RSS / Atom','','none',(
            op('read_feed','GET','',['keyword','since']),)),
        Integration('webhooks','Webhooks','','bearer',(
            op('send','POST','',['payload'],required=['payload']),
            Operation('receive','Receive a signed inbound delivery','POST','', 'inbound',
                      {'type':'object','properties':{},'required':[],'additionalProperties':False}),)),
    ]
    for identifier, name in [('mcp','MCP'),('http_openapi','HTTP / OpenAPI'),('postgres','PostgreSQL'),('mysql','MySQL'),('s3','Amazon S3')]:
        items.append(Integration(identifier,name,'','host_adapter',()))
    return {item.id: replace(item, category=CATEGORIES.get(item.id, item.category)) for item in items}


async def invoke(service, operation, config, secrets, arguments, transport):
    if operation.capability == 'inbound':
        # An inbound delivery arrives with a signature; it is never sent out.
        raise IntegrationError('Inbound triggers are received, not called')
    args = dict(arguments)
    base = config.get('base_url', service.base_url).rstrip('/')
    headers = {}
    if service.auth in ('google_oauth','bearer','api_key'):
        token = secrets.get('access_token') or secrets.get('token') or secrets.get('api_key')
        if not token:
            raise IntegrationError('Configure a provider access token or API key')
        if service.id == 'web_search': headers['X-Subscription-Token'] = token
        elif service.id == 'linear': headers['Authorization'] = token
        else: headers['Authorization'] = 'Bearer ' + token
    elif service.auth in ('atlassian','zendesk'):
        email, token = secrets.get('email'), secrets.get('token')
        if not email or not token: raise IntegrationError('Configure account email and API token')
        if service.auth == 'zendesk': email += '/token'
        headers['Authorization'] = 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode()
    elif service.auth == 'trello':
        if not secrets.get('api_key') or not secrets.get('token'): raise IntegrationError('Configure Trello key and token')
    path = operation.path
    for key in re.findall(r'\{(\w+)\}', path):
        value = args.pop(key)
        if value in ('.','..') or '/' in value or '\\' in value:
            raise IntegrationError('Resource identifier cannot contain path segments')
        if key == 'object_type' and value not in ('companies','contacts','deals'):
            raise IntegrationError('Unsupported CRM object type')
        path = path.replace('{'+key+'}',quote(value,safe=''))
    params, body = {}, None
    if operation.method == 'GET': params = args
    else: body = args
    if service.id == 'web_search': params = {'q': args['query'], 'count': 10}
    if service.id == 'google_drive':
        if operation.name == 'search_files': params = {'q':args.get('query','trashed = false'),'pageSize':100,'fields':'files(id,name,mimeType,modifiedTime),nextPageToken',**({'pageToken':args['page_token']} if args.get('page_token') else {})}
        elif operation.name == 'file_metadata': params = {'fields':'id,name,mimeType,size,modifiedTime,webViewLink'}
        else:
            data=await transport.request('GET',base+path,headers=headers,params={'alt':'media'},raw=True)
            return {'text':data.decode('utf-8')}
    if service.id == 'gmail':
        if operation.name == 'search_messages': params = {'q':args.get('query',''),'maxResults':100,**({'pageToken':args['page_token']} if args.get('page_token') else {})}
        elif operation.name == 'create_draft': body = {'message':{'raw':args['raw']}}
    if service.id == 'google_sheets' and operation.name == 'update_range':
        params={'valueInputOption':'RAW'};body={'values':args['values']}
    if service.id == 'notion': headers['Notion-Version']='2022-06-28'
    if service.id == 'confluence' and operation.name == 'read_page':params={'expand':'body.storage,version'}
    if service.id == 'slack':params={**params,'limit':100} if operation.name=='read_conversation' else params
    if service.id == 'hubspot' and operation.name.startswith('search_'):body={'query':args['query'],'limit':100}
    if service.id == 'zendesk' and operation.name == 'prepare_reply':
        # Internal note, not a public reply; still an external write requiring approval.
        body={'ticket':{'comment':{'body':args['text'],'public':False}}}
    if service.id == 'jira':
        if operation.name == 'create_issue':
            fields={'project':{'key':args['project']},'summary':args['summary'],'issuetype':{'name':args['issue_type']}}
            if args.get('description'): fields['description']={'type':'doc','version':1,'content':[{'type':'paragraph','content':[{'type':'text','text':args['description']}]}]}
            body={'fields':fields}
        elif operation.name == 'update_issue':body={'fields':{'summary':args['summary']}}
    if service.id == 'github':
        headers.update({'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        if operation.name.startswith('search_'):params={'q':args['query'],'per_page':100}
    if service.id == 'linear':
        if operation.name == 'search_issues': body={'query':'query($term:String!){issues(filter:{title:{containsIgnoreCase:$term}},first:50){nodes{id identifier title description} pageInfo{hasNextPage endCursor}}}', 'variables':{'term':args['query']}}
        elif operation.name == 'read_projects':body={'query':'query{projects(first:50){nodes{id name description} pageInfo{hasNextPage endCursor}}}'}
        else:body={'query':'mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier title}}}', 'variables':{'input':{'teamId':args['team_id'],'title':args['title'],'description':args.get('description','')}}}
    if service.id == 'trello':
        params.update(key=secrets['api_key'],token=secrets['token'])
        if operation.name == 'create_card':body={'idList':args['list_id'],'name':args['name'],'desc':args.get('description','')}
        elif operation.name == 'update_card':body={'name':args['name'],'desc':args.get('description','')}
    if service.id == 'reddit':headers['User-Agent']='Conclave/0.1 (authorized API integration)';params.update(limit=100)
    if service.id == 'firecrawl':
        target=args['url'];approved=config.get('target_hosts',[])
        parsed=endpoint(target,approved);public_addresses(parsed.hostname)
        body={'url':target,'formats':['markdown'],'onlyMainContent':True}
    if service.id == 'webhooks':body=args['payload']
    if service.id == 'rss':
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime
        from datetime import datetime
        data=await transport.request('GET',base,raw=True)
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise IntegrationError('Feed entities are forbidden')
        root=ET.fromstring(data)
        entries=[]
        for item in list(root.findall('.//item'))+list(root.findall('{http://www.w3.org/2005/Atom}entry')):
            def text(name):return item.findtext(name) or item.findtext('{http://www.w3.org/2005/Atom}'+name) or ''
            title=text('title');summary=text('description') or text('summary');date=text('pubDate') or text('updated')
            link=text('link')
            if not link:
                node=item.find('{http://www.w3.org/2005/Atom}link');link=node.get('href','') if node is not None else ''
            if args.get('keyword') and args['keyword'].lower() not in (title+' '+summary).lower():continue
            if args.get('since'):
                try:dt=parsedate_to_datetime(date) if ',' in date else datetime.fromisoformat(date.replace('Z','+00:00'));since=datetime.fromisoformat(args['since'].replace('Z','+00:00'))
                except ValueError:continue
                if dt.timestamp()<since.timestamp():continue
            entries.append({'title':title,'url':link,'snippet':summary,'date':date})
            if len(entries)>=100:break
        return {'entries':entries}
    if service.id=='google_docs' and operation.name=='create_document':
        created=await transport.request('POST',base+path,headers=headers,body={'title':args['title']})
        document_id=created['documentId']
        await transport.request('POST',base+'/v1/documents/'+quote(document_id,safe='')+':batchUpdate',headers=headers,body={'requests':[{'insertText':{'location':{'index':1},'text':args['text']}}]})
        return {'document_id':document_id,'status':'draft_created'}
    result=await transport.request(operation.method,base+path,headers=headers,body=body,params=params)
    if service.id=='slack' and not result.get('ok'):raise IntegrationError('Slack rejected the operation')
    if service.id=='linear' and result.get('errors'):raise IntegrationError('Linear rejected the operation')
    if service.id=='web_search':return {'results':[{'url':item.get('url'),'title':item.get('title'),'snippet':item.get('description')} for item in result.get('web',{}).get('results',[])]}
    return result
