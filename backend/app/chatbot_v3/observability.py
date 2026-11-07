"""Opt-in, redacted recording. Export is explicit; env presence never sends data."""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import os
from urllib.parse import urlsplit
import uuid
import httpx


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class TraceRecorder:
    def __init__(self, capacity=1000):
        if capacity < 1:raise ValueError('capacity must be positive')
        self.events=deque(maxlen=capacity)
        self.dropped=0

    def record(self, *, trace_id, name, kind, start, status, prompt_version=None, usage=None):
        # No question, answer, vehicle, session/request IDs, API URLs or errors.
        metadata={'status':status}
        if prompt_version is not None:metadata['prompt_sha256']=prompt_version
        safe_usage={k:v for k,v in (usage or {}).items() if k in ('prompt_tokens','completion_tokens','total_tokens','cost')
                    and type(v) in (int,float) and math.isfinite(v) and v>=0}
        if len(self.events)==self.events.maxlen:self.dropped+=1
        self.events.append(dict(trace_id=trace_id,name=name,kind=kind,start=start,end=utc_now(),metadata=metadata,usage=safe_usage))

    def snapshot(self):
        from copy import deepcopy
        return deepcopy(list(self.events))


@dataclass(frozen=True)
class LangfuseSettings:
    base_url:str
    public_key:str=field(repr=False)
    secret_key:str=field(repr=False)

    @classmethod
    def load(cls, values=None):
        values=os.environ if values is None else values
        base=(values.get('LANGFUSE_BASE_URL') or '').strip().rstrip('/')
        host=(values.get('LANGFUSE_HOST') or '').strip().rstrip('/')
        if base and host and base!=host:raise ValueError('Langfuse URL settings conflict')
        url=base or host
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid Langfuse base URL')
        if parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Remote Langfuse requires HTTPS')
        public=(values.get('LANGFUSE_PUBLIC_KEY') or '').strip()
        secret=(values.get('LANGFUSE_SECRET_KEY') or '').strip()
        if not public or not secret:raise ValueError('Langfuse public and secret keys required')
        return cls(url,public,secret)


class LangfuseExporter:
    """Public ingestion adapter. Caller explicitly opts in and supplies the client.

    No background thread, auto-retry, env-triggered connection, or answer-path export.
    Uses at most 100 events per request; reports partial acceptance without resending.
    """
    def __init__(self,client,settings):self.client,self.settings=client,settings

    def export(self,events):
        if len(events)>100:raise ValueError('Export at most 100 events per batch')
        batch=[]
        for event in events:
            body={'name':event['name'],'metadata':event['metadata']}
            if event['kind']=='trace':
                typ='trace-create';body.update(id=event['trace_id'],timestamp=event['start'])
            else:
                typ='generation-create' if event['kind']=='generation' else 'span-create'
                body.update(id=uuid.uuid4().hex,traceId=event['trace_id'],startTime=event['start'],endTime=event['end'])
                if event['kind']=='generation':
                    usage=event['usage']
                    body['usageDetails']={target:usage[source] for source,target in [('prompt_tokens','input'),('completion_tokens','output'),('total_tokens','total')] if source in usage}
                    if 'cost' in usage:body['costDetails']={'total':usage['cost']}
            batch.append({'id':uuid.uuid4().hex,'timestamp':event['end'],'type':typ,'body':body})
        if not batch:return {'status':'empty','accepted':0}
        try:
            response=self.client.post(self.settings.base_url+'/api/public/ingestion',json={'batch':batch},
                auth=(self.settings.public_key,self.settings.secret_key),timeout=3,follow_redirects=False)
            if response.status_code not in (200,207):return {'status':'unavailable','http_status':response.status_code}
            result=response.json()
            if not isinstance(result,dict) or not isinstance(result.get('successes'),list) or not isinstance(result.get('errors'),list):
                return {'status':'invalid_response'}
            successes=result['successes'];errors=result['errors']
            if any(not isinstance(row,dict) or not isinstance(row.get('id'),str) for row in successes):
                return {'status':'invalid_response'}
            expected={row['id'] for row in batch}
            acknowledged={row['id'] for row in successes}
            complete=acknowledged==expected and len(successes)==len(expected)
            return {'status':'ok' if not errors and complete else 'partial',
                    'accepted':len(expected & acknowledged),'rejected':len(errors)}
        except (httpx.RequestError,ValueError):
            return {'status':'unavailable'}
