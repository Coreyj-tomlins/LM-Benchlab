#!/usr/bin/env python3
"""LM Benchlab — configurable, general-purpose LLM benchmarks.
Python 3.10+, with cryptography for optional encrypted API key storage.
"""
import concurrent.futures
import csv
import io
import ipaddress
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit
from config import APP_NAME, APP_SLUG, ConfigurationError, VERSION, load_config
from complex_cases import AGENTIC_CASES, CONVERSATION_CASES, DEEP_CASES as LEGACY_DEEP_CASES
from prompt_catalog import (CATEGORY_IDS, EXTRA_DEEP_CASES, EXTRA_DEEP_SYSTEM_PROMPTS,
                            MIXED_CASES, PROMPT_TYPES, SYSTEM_PROMPTS)
DEEP_CASES = [case for case in LEGACY_DEEP_CASES if case['category'] in ('Interface', 'SIEM')]
try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception

try:
    CONFIG = load_config()
except ConfigurationError as exc:
    if __name__ == '__main__':
        raise SystemExit(f'Configuration error: {exc}') from None
    raise

APP_HOST = CONFIG.host
APP_PORT = CONFIG.port
DEFAULT_ENGINE_HOST = CONFIG.default_engine_host
MAX_REQUESTS = 500
DB_PATH = CONFIG.db_path
DB_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
JOBS = {}
ALLOWED_HOST = re.compile(r'^[A-Za-z0-9.-]{1,253}$')
MASTER_KEY = CONFIG.master_key

def cipher():
    if not Fernet or not MASTER_KEY:
        raise RuntimeError('Encrypted key storage is unavailable; configure LM_BENCHLAB_MASTER_KEY')
    try:
        return Fernet(MASTER_KEY.encode('ascii'))
    except Exception as exc:
        raise RuntimeError('LM_BENCHLAB_MASTER_KEY must be a valid Fernet key') from exc

def valid_host(name):
    if not isinstance(name, str) or not ALLOWED_HOST.fullmatch(name) or '..' in name:
        raise ValueError('Host must be a hostname or IP without http:// or a port')
    return name

def endpoint_url(host, port, resource):
    raw=str(host or '').strip()
    if '://' in raw:
        parsed=urlsplit(raw)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Endpoint must be an http:// or https:// URL without credentials, query, or fragment')
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError('Endpoint has an invalid port') from exc
        base=raw.rstrip('/')
        if not parsed.path.rstrip('/').endswith('/v1'):
            base+='/v1'
        return base+'/'+resource.lstrip('/')
    valid_host(raw)
    if not isinstance(port,int) or not 1<=port<=65535:
        raise ValueError('A port from 1 to 65535 is required for hostname endpoints')
    return f'http://{raw}:{port}/v1/{resource.lstrip("/")}'


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        def origin(url):
            parts = urlsplit(url)
            return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)
        if origin(req.full_url) != origin(newurl):
            raise ValueError('Inference endpoint redirected to a different origin; configure its final URL directly')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_endpoint(request, timeout):
    return urllib.request.build_opener(SameOriginRedirectHandler()).open(request, timeout=timeout)



@contextmanager
def connect_db():
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=30000')
        with db:
            yield db
    finally:
        db.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
            engine TEXT NOT NULL, port INTEGER NOT NULL, host TEXT NOT NULL DEFAULT 'host.docker.internal', model TEXT NOT NULL,
            concurrency INTEGER NOT NULL, max_tokens INTEGER NOT NULL,
            timeout INTEGER NOT NULL, temperature REAL NOT NULL,
            wall_seconds REAL NOT NULL, aggregate_tokens_per_second REAL,
            succeeded INTEGER NOT NULL, total INTEGER NOT NULL,
            avg_request_seconds REAL, output_tokens INTEGER NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS requests (
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            request_index INTEGER NOT NULL, category TEXT NOT NULL,
            scenario TEXT NOT NULL, system_prompt TEXT NOT NULL, user_prompt TEXT NOT NULL,
            ok INTEGER NOT NULL, seconds REAL NOT NULL, completion_tokens INTEGER,
            prompt_tokens INTEGER, tokens_per_second REAL, finish_reason TEXT,
            content TEXT, reasoning TEXT, error TEXT,
            review TEXT NOT NULL DEFAULT 'unreviewed'
                CHECK(review IN ('unreviewed','correct','incorrect')),
            PRIMARY KEY (run_id, request_index)
        )''')
        if 'host' not in [r[1] for r in db.execute('PRAGMA table_info(runs)')]:
            db.execute("ALTER TABLE runs ADD COLUMN host TEXT NOT NULL DEFAULT 'host.docker.internal'")
        if 'max_inflight' not in [r[1] for r in db.execute('PRAGMA table_info(runs)')]:
            db.execute('ALTER TABLE runs ADD COLUMN max_inflight INTEGER NOT NULL DEFAULT 0')
        if 'workload' not in [r[1] for r in db.execute('PRAGMA table_info(runs)')]:
            db.execute("ALTER TABLE runs ADD COLUMN workload TEXT NOT NULL DEFAULT 'mixed'")
        if 'enabled_categories' not in [r[1] for r in db.execute('PRAGMA table_info(runs)')]:
            # NULL distinguishes historical runs whose selection was not recorded.
            db.execute('ALTER TABLE runs ADD COLUMN enabled_categories TEXT')
        req_cols=[r[1] for r in db.execute('PRAGMA table_info(requests)')]
        if 'reasoning_tokens' not in req_cols:
            db.execute('ALTER TABLE requests ADD COLUMN reasoning_tokens INTEGER')
        if 'review_guide' not in req_cols:
            db.execute("ALTER TABLE requests ADD COLUMN review_guide TEXT NOT NULL DEFAULT ''")
        if 'judge_score' not in req_cols:
            db.execute('ALTER TABLE requests ADD COLUMN judge_score INTEGER')
        if 'judge_reason' not in req_cols:
            db.execute("ALTER TABLE requests ADD COLUMN judge_reason TEXT NOT NULL DEFAULT ''")
        db.execute('''CREATE TABLE IF NOT EXISTS engine_keys (
            profile_id TEXT PRIMARY KEY, encrypted_key TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_run_date ON runs(timestamp DESC)')


def engine_name(port, specified=''):
    if specified:
        return specified
    if port == 1234: return 'LM Studio'
    if port == 8000: return 'vLLM'
    return f'Custom :{port}'

def save_engine_key(profile_id, api_key):
    encrypted=cipher().encrypt(api_key.encode('utf-8')).decode('ascii')
    with DB_LOCK, connect_db() as db:
        db.execute('INSERT INTO engine_keys(profile_id,encrypted_key,updated_at) VALUES(?,?,?) ON CONFLICT(profile_id) DO UPDATE SET encrypted_key=excluded.encrypted_key,updated_at=excluded.updated_at', (profile_id,encrypted,datetime.now(timezone.utc).isoformat(timespec='seconds')))

def load_engine_key(profile_id):
    if not profile_id: return ''
    with connect_db() as db:
        row=db.execute('SELECT encrypted_key FROM engine_keys WHERE profile_id=?',(profile_id,)).fetchone()
    if not row: return ''
    try: return cipher().decrypt(row['encrypted_key'].encode('ascii')).decode('utf-8')
    except InvalidToken as exc: raise RuntimeError('Stored engine key cannot be decrypted; check LM_BENCHLAB_MASTER_KEY') from exc

def delete_engine_key(profile_id):
    with DB_LOCK, connect_db() as db: db.execute('DELETE FROM engine_keys WHERE profile_id=?',(profile_id,))

def judge_result(result, judge):
    if not judge.get('enabled') or not result.get('ok') or not result.get('content'):
        return result
    rubric=result.get('review_guide') or 'Accuracy, completeness, safety, and usefulness for the user request.'
    payload={'model':judge['model'],'messages':[{'role':'system','content':'You are a strict LLM evaluator. Return only valid JSON with integer score from 0 to 5 and a concise reason.'},{'role':'user','content':f'User task:\n{result["user_prompt"]}\n\nModel answer:\n{result["content"]}\n\nEvaluation criteria:\n{rubric}\n\nReturn JSON: {{"score": 0, "reason": "..."}}'}],'temperature':0,'max_tokens':300,'stream':False}
    try:
        response=invoke_model(judge['host'],judge['port'],payload,judge.get('timeout',120),judge.get('api_key',''))
        text=((response.get('choices') or [{}])[0].get('message') or {}).get('content','').strip()
        match=re.search(r'\{.*\}',text,re.S)
        parsed=json.loads(match.group(0) if match else text)
        score=max(0,min(5,int(parsed.get('score'))))
        result['judge_score']=score;result['judge_reason']=str(parsed.get('reason',''))[:2000]
    except Exception as exc:
        result['judge_reason']='Judge error: '+str(exc)[:500]
    return result
PROMPTS = [
    ('Juniper WAN link down', 'Juniper MX480', 'xe-0/0/1', 'Administratively up, operationally down. Link lost 9 minutes ago. No CRC errors. Customer primary WAN.'),
    ('Cisco core uplink flapping', 'Cisco C9500', 'TenGigabitEthernet1/0/1', 'Interface flapped six times in 20 minutes. CRC rising. Optical receive power near low warning.'),
    ('Juniper customer handoff', 'Juniper EX4300', 'ge-0/0/12', 'Interface down/down. Remote customer router recently rebooted. No maintenance recorded.'),
    ('Arista LAG member down', 'Arista 7280R', 'Ethernet5', 'LAG Port-Channel10 has lost one member. Other members operational. No interface errors.'),
    ('Cisco WAN errors', 'Cisco ISR4431', 'GigabitEthernet0/0/0', 'Operationally up. Heavy CRC/input errors and packet discards since 08:30 UTC.'),
    ('Juniper 100G optics', 'Juniper QFX5120', 'et-0/0/49', 'Interface down; reported Rx optical power is -35 dBm. Tx optical power within normal range.'),
    ('Cisco access trunk', 'Cisco C9300', 'TenGigabitEthernet1/1/1', 'Up/up but VLAN 220 traffic lost after a change. Trunk allowed VLAN list unknown.'),
    ('Juniper high utilisation', 'Juniper MX204', 'xe-0/1/0', 'Link up/up, 98% egress utilisation, output drops increasing, 15-minute average.'),
    ('Fortinet WAN interface', 'FortiGate 200F', 'wan1', 'Physical link up, SLA health check failing. Upstream gateway reachable intermittently.'),
    ('Cisco spanning tree', 'Cisco C9200', 'GigabitEthernet1/0/24', 'Link up/up, port in STP blocking state. User reports service loss after switch addition.'),
    ('Juniper intermittent loss', 'Juniper SRX345', 'ge-0/0/0', 'Interface up/up, 2% packet loss observed, no physical errors; BGP peer intermittently resets.'),
    ('Nokia service port', 'Nokia 7750 SR', '1/1/c1/1', 'Physical port operationally down, transport provider ticket not yet raised; local admin status up.'),
    ('Cisco port security', 'Cisco C2960X', 'GigabitEthernet1/0/8', 'Interface err-disabled after port-security violation. No approved change recorded.'),
    ('Juniper BGP transport', 'Juniper MX960', 'ae3', 'Bundle operationally up with only one of four member links active. BGP adjacency unstable.'),
    ('Arista EVPN uplink', 'Arista 7050X', 'Ethernet49/1', 'Uplink up/up but intermittent discards. EVPN peer reachability impacted during peak traffic.'),
]

# Domain-specific scenarios remain available alongside the general catalog.
SIEM_CASES = [
 ('Impossible travel', 'Entra ID sign-ins from London and Singapore within 8 minutes; MFA succeeded on one session.'),
 ('Password spray', '146 failed Azure AD sign-ins for 42 users from 203.0.113.44; 2 successful logins.'),
 ('Malicious PowerShell', 'EDR shows encoded PowerShell spawned by WINWORD.EXE on workstation FIN-042.'),
 ('Suspicious DNS', 'Endpoint repeatedly queries newly registered domains with TXT responses over 600 bytes.'),
 ('Lateral movement', 'SMB authentication attempts to 18 servers from user workstation after hours.'),
 ('Outbound exfiltration', 'Firewall saw 3.4 GB outbound HTTPS to previously unseen ASN from fileserver.'),
 ('Privilege escalation', 'New domain admin membership followed by remote logon to domain controller.'),
 ('Brute-force VPN', 'VPN gateway logged 230 failed logins and 1 success for contractor account.'),
 ('Beaconing', 'Proxy logs show 60-second interval connections to unknown external host.'),
 ('Defender alert', 'Endpoint quarantined suspicious executable; user reports downloading an invoice.'),
 ('Data access spike', 'Service account read 85k SharePoint files outside normal business hours.'),
 ('WAF SQL injection', 'WAF blocked 400 SQLi attempts; 7 HTTP 200 responses need investigation.'),
 ('Phishing campaign', 'Multiple mailboxes received attachment; one user clicked the URL.'),
 ('Cloud access key', 'CloudTrail reports access key used from unfamiliar region and unusual API calls.'),
 ('RDP exposure', 'Windows event logs show successful RDP sign-in via jump host with no ticket.'),
 ('Suspicious scheduled task', 'EDR detected persistence via scheduled task under AppData path.'),
 ('Endpoint ransomware indicators', 'Rapid file rename activity and shadow copy deletion command detected.'),
 ('Firewall deny burst', 'Palo Alto observed 900 denies to management VLAN from test endpoint.'),
 ('Unusual OAuth consent', 'User authorised unfamiliar app requesting mail.read and offline_access.'),
 ('Kerberoasting indicator', 'Large volume of service ticket requests using RC4 from one endpoint.'),
 ('Web shell suspicion', 'IIS worker spawned cmd.exe after POST to unusual .aspx resource.'),
 ('Linux SSH anomaly', 'Linux host successful SSH login from unfamiliar source following brute force.'),
 ('Token replay', 'Same session token observed from distinct ASNs with overlapping timestamps.'),
 ('Rare process execution', 'Sysmon records rundll32 launching unexpected DLL from Downloads.'),
 ('Email forwarding rule', 'New inbox forwarding rule sends finance mail to external domain.'),
]

def normalize_categories(value=None):
    """Validate selection and use catalog order for reproducible interleaving."""
    if value is None:
        return list(CATEGORY_IDS)
    if not isinstance(value, list) or not value:
        raise ValueError('Select at least one prompt type')
    if any(not isinstance(item, str) or item not in CATEGORY_IDS for item in value):
        raise ValueError('Unknown prompt type in enabled_categories')
    if len(value) != len(set(value)):
        raise ValueError('Prompt type selection contains duplicates')
    return [category for category in CATEGORY_IDS if category in value]


def prompt_types():
    """Expose the same catalog used by generation to the Settings page."""
    return {'categories': [
        {'id': category, 'label': label, 'description': description,
         'system_prompt': prompt,
         'mixed_cases': len(PROMPTS) if category == 'Interface' else
                        len(SIEM_CASES) if category == 'SIEM' else len(MIXED_CASES[category]),
         'deep_cases': sum(case['category'] == category for case in DEEP_CASES + EXTRA_DEEP_CASES)}
        for category, label, description, prompt in PROMPT_TYPES],
        'default_enabled': list(CATEGORY_IDS)}


def scenario_for(index, workload='mixed', enabled_categories=None):
    if workload == 'chat20':
        case=CONVERSATION_CASES[index % len(CONVERSATION_CASES)]
        return case['category'], case['title'], case['prompt'], case['checks']
    if workload == 'agentic12':
        case=AGENTIC_CASES[index % len(AGENTIC_CASES)]
        return case['category'], case['title'], case['prompt'], case['checks']
    selected = normalize_categories(enabled_categories)
    category = selected[index % len(selected)]
    variant = index // len(selected)
    if workload == 'deep20':
        pool = [case for case in DEEP_CASES + EXTRA_DEEP_CASES if case['category'] == category]
        case = pool[variant % len(pool)]
        return category, case['title'], case['prompt'], case['checks']
    if category == 'Interface':
        title, device, interface, evidence = PROMPTS[variant % len(PROMPTS)]
        question = (f'Customer site {variant+1:02d}. Alert: {title}\nDevice: {device}\nInterface: {interface}\n'
                    f'Evidence: {evidence}\nGive triage, commands, next actions, escalation and a brief ServiceNow note.')
    else:
        pool = SIEM_CASES if category == 'SIEM' else MIXED_CASES[category]
        title, evidence = pool[variant % len(pool)]
        question = f'Case {variant+1:02d}: {evidence}'
    return category, title, question, []



def invoke_model(host, port, payload, timeout, api_key=''):
    headers={'Content-Type':'application/json'}
    if api_key:
        headers['Authorization']='Bearer '+api_key
    req = urllib.request.Request(
        endpoint_url(host,port,'chat/completions'),
        data=json.dumps(payload).encode('utf-8'),
        headers=headers, method='POST')
    with open_endpoint(req, timeout=timeout) as res:
        return json.load(res)


DEEP_SYSTEM_PROMPTS = {
 'Interface': 'You are a senior MSSP network engineer. Analyse multi-layer evidence and contradictory clues. Produce a clear structured answer covering observations, ranked hypotheses with confidence, discriminating read-only checks, a time-ordered safe action plan, escalation and a factual ServiceNow update. Do not invent measurements, change device state, or assert unverified root causes. Explain key decision points in your final answer.',
 'SIEM': 'You are a senior SOC incident responder. Separate observed events from inference; account for false positives, identity uncertainty and business impact. Produce a timeline, a ranked hypothesis assessment, precise investigation pivots, containment options with approvals, and a concise stakeholder update. Never claim unproven compromise or exfiltration. Explain the critical evidence and decision points in your final answer.',
 'Chat': 'You are an experienced enterprise IT support engineer. Respond to the latest turn of the provided conversation, take account of previous messages and user constraints, and provide a safe and practical step-by-step resolution with a concise explanation and clear escalation or rollback conditions. Never fabricate actions already performed.',
 'Sales': 'You are a rigorous MSSP solutions consultant. Work through numerical assumptions accurately; distinguish calculations from unknown commercial inputs, avoid invented prices, and develop a realistic decision-ready recommendation with explicit caveats and measurable next steps. Explain the important assumptions and checks in the final answer.'
}
DEEP_SYSTEM_PROMPTS.update(EXTRA_DEEP_SYSTEM_PROMPTS)
# Generic Chat/Sales roles also apply to their reasoning cases; keep the richer
# evidence/check requirements rather than accidentally replacing them with the
# unchanged regular prompt sent by older clients.
DEEP_SYSTEM_PROMPTS['Chat'] = SYSTEM_PROMPTS['Chat'] + ' Analyze the supplied constraints and conversation, explain important decision points and provide a practical answer without inventing facts.'
DEEP_SYSTEM_PROMPTS['Sales'] = SYSTEM_PROMPTS['Sales'] + ' Check numerical assumptions carefully and give a decision-ready recommendation with explicit caveats and measurable next steps.'
AGENTIC_SYSTEM_PROMPT = '''You are a senior Python engineer working through a stateful, multi-phase software task. Treat prior phase outputs as working project context, but verify them instead of blindly trusting them. Make concrete, executable recommendations and code when requested. Preserve acceptance criteria, surface contradictions, do not claim to have run commands you did not run, and leave a clear handoff for the next phase.'''
CONVERSATION_SYSTEM_PROMPT = '''You are a helpful IT support assistant in an ongoing chat. Use the complete prior conversation as context, respond naturally to the latest user message, and do not repeat questions that were already answered. Give one or two practical next steps, distinguish evidence from guesses, protect secrets, and keep the reply concise enough for a real-time support conversation. Do not claim to have performed actions.'''


def investigate(index, host, port, model, max_tokens, temperature, timeout, gate, workload='mixed', api_key='', prompt_overrides=None, conversation_context='', enabled_categories=None):
    category, scenario, question, checks = scenario_for(index, workload, enabled_categories)
    defaults = CONVERSATION_SYSTEM_PROMPT if workload=='chat20' else (AGENTIC_SYSTEM_PROMPT if workload=='agentic12' else (DEEP_SYSTEM_PROMPTS if workload=='deep20' else SYSTEM_PROMPTS))
    override = (prompt_overrides or {}).get(category)
    if workload == 'deep20' and override == SYSTEM_PROMPTS.get(category):
        override = None
    system_prompt = override or (defaults if isinstance(defaults,str) else defaults[category])
    if conversation_context:
        question = 'PROJECT CONTEXT FROM EARLIER PHASES:\n'+conversation_context+'\n\nCURRENT PHASE:\n'+question
    payload = {'model':model, 'messages':[{'role':'system','content':system_prompt},
                                         {'role':'user','content':question}],
               'max_tokens':max_tokens, 'temperature':temperature, 'stream':False}
    gate.wait()
    start = time.perf_counter()
    try:
        response = invoke_model(host,port,payload,timeout,api_key)
        secs = time.perf_counter()-start
        choice = (response.get('choices') or [{}])[0]
        msg = choice.get('message') or {}
        usage = response.get('usage') or {}
        completion = usage.get('completion_tokens')
        prompt_tokens = usage.get('prompt_tokens')
        content = msg.get('content') or ''
        reasoning = msg.get('reasoning') or msg.get('reasoning_content') or ''
        reasoning_details = usage.get('completion_tokens_details') or usage.get('output_token_details') or {}
        if not isinstance(reasoning_details, dict): reasoning_details = {}
        reasoning_tokens = reasoning_details.get('reasoning_tokens', usage.get('reasoning_tokens'))
        # Some GGUF runtimes embed the reasoning inside content rather than a separate field.
        # Preserve the raw text for auditability while making the final-answer segment viewable.
        if not reasoning and isinstance(content,str) and '</think>' in content:
            raw_thought, tail = content.split('</think>',1)
            if '<think>' in raw_thought:
                raw_thought = raw_thought.split('<think>',1)[1]
            reasoning = raw_thought.strip()
            content = tail.strip()

        return {'index':index+1, 'category':category, 'scenario':scenario,
                'system_prompt':system_prompt, 'user_prompt':question,
                'ok':True, 'seconds':round(secs,3),
                'completion_tokens':completion if isinstance(completion,int) else None,
                'prompt_tokens':prompt_tokens if isinstance(prompt_tokens,int) else None,
                'tokens_per_second':round(completion/secs,2) if isinstance(completion,int) and secs else None,
                'finish_reason':str(choice.get('finish_reason') or ''),
                'reasoning_tokens':reasoning_tokens if isinstance(reasoning_tokens,int) and reasoning_tokens>=0 else None,
                'review_guide':'\n'.join('• '+item for item in checks),
                'content':str(content), 'reasoning':str(reasoning), 'error':'',
                'review':'unreviewed'}
    except Exception as exc:
        secs=time.perf_counter()-start
        if isinstance(exc,urllib.error.HTTPError):
            try: error=f'HTTP {exc.code}: {exc.read(1500).decode("utf-8","replace")}'
            except Exception: error=f'HTTP {exc.code}'
        else: error=str(exc)
        return {'index':index+1,'category':category,'scenario':scenario,
                'system_prompt':system_prompt,'user_prompt':question,
                'ok':False,'seconds':round(secs,3), 'completion_tokens':None,
                'prompt_tokens':None,'tokens_per_second':None,'finish_reason':'',
                'content':'','reasoning':'','reasoning_tokens':None,
                'review_guide':'\n'.join('• '+item for item in checks),
                'error':error,'review':'unreviewed'}


def grouped_results(results):
    groups=[]
    for category in dict.fromkeys(r['category'] for r in results):
        sub=[r for r in results if r['category']==category]
        good=[r for r in sub if r['ok']]
        groups.append({'category':category,'total':len(sub), 'succeeded':len(good),
                       'avg_seconds':round(sum(r['seconds'] for r in good)/len(good),3) if good else None,
                       'output_tokens':sum(r['completion_tokens'] or 0 for r in good),
                       'truncated':sum(r['finish_reason']=='length' for r in sub),
                       'reasoning_tokens':sum(r['reasoning_tokens'] or 0 for r in sub if r.get('reasoning_tokens') is not None)})
    return groups


def save_run(summary, results, settings):
    with DB_LOCK, connect_db() as db:
        db.execute('''INSERT INTO runs (id,timestamp,engine,port,host,model,concurrency,max_tokens,timeout,temperature,wall_seconds,aggregate_tokens_per_second,succeeded,total,avg_request_seconds,output_tokens,max_inflight,workload,enabled_categories) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            summary['run_id'],summary['timestamp'],summary['engine'],settings['port'],settings['host'],
            settings['model'], settings['concurrency'],settings['max_tokens'],
            settings['timeout'],settings['temperature'], summary['wall_seconds'],
            summary['aggregate_tokens_per_second'],summary['succeeded'],summary['total'],
            summary['avg_request_seconds'],summary['output_tokens'],settings['max_inflight'],settings['workload'],
            json.dumps(settings['enabled_categories'])))
        db.executemany('''INSERT INTO requests (
          run_id,request_index,category,scenario,system_prompt,user_prompt,ok,seconds,
                    completion_tokens,prompt_tokens,tokens_per_second,finish_reason,content,reasoning,error,review,reasoning_tokens,review_guide,judge_score,judge_reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',[(
            summary['run_id'],r['index'],r['category'],r['scenario'],r['system_prompt'],
            r['user_prompt'],int(r['ok']),r['seconds'],r['completion_tokens'],r['prompt_tokens'],
            r['tokens_per_second'],r['finish_reason'],r['content'],r['reasoning'],r['error'],r['review'],
            r['reasoning_tokens'],r['review_guide'],r.get('judge_score'),r.get('judge_reason',''))
            for r in results])


def rowdict(row):
    d=dict(row)
    if 'ok' in d: d['ok']=bool(d['ok'])
    if 'request_index' in d: d['index']=d.pop('request_index')
    if d.get('enabled_categories') is not None:
        d['enabled_categories'] = json.loads(d['enabled_categories'])
    return d


def load_history():
    with connect_db() as db:
        rows=db.execute('''SELECT r.*,
            COALESCE(SUM(CASE WHEN q.review='correct' THEN 1 ELSE 0 END),0) AS correct,
            COALESCE(SUM(CASE WHEN q.review='incorrect' THEN 1 ELSE 0 END),0) AS incorrect,
            COALESCE(SUM(CASE WHEN q.review='unreviewed' THEN 1 ELSE 0 END),0) AS unreviewed
            FROM runs r LEFT JOIN requests q ON q.run_id=r.id
            GROUP BY r.id ORDER BY r.timestamp DESC, r.rowid DESC LIMIT 300''').fetchall()
    return [rowdict(x) for x in rows]


def load_run(run_id):
    with connect_db() as db:
        run=db.execute('''SELECT * FROM runs WHERE id=?''',(run_id,)).fetchone()
        if not run: return None
        requests=db.execute('SELECT * FROM requests WHERE run_id=? ORDER BY request_index',(run_id,)).fetchall()
    rlist=[rowdict(q) for q in requests]
    reason_reports=[x['reasoning_tokens'] for x in rlist if x.get('reasoning_tokens') is not None]
    return {**rowdict(run),'results':rlist,'groups':grouped_results(rlist),
            'reported_reasoning_tokens':sum(reason_reports) if reason_reports else None,
            'reasoning_reported_requests':len(reason_reports),
            'truncated':sum(x['finish_reason']=='length' for x in rlist),
            'correct':sum(x['review']=='correct' for x in rlist),
            'incorrect':sum(x['review']=='incorrect' for x in rlist),
            'unreviewed':sum(x['review']=='unreviewed' for x in rlist)}


def set_review(run_id, index, verdict):
    with DB_LOCK, connect_db() as db:
        cur=db.execute('UPDATE requests SET review=? WHERE run_id=? AND request_index=?',
                       (verdict,run_id,index))
        return cur.rowcount>0


def csv_cell(value):
    """Keep untrusted prompts/model text from becoming spreadsheet formulas."""
    if isinstance(value, str) and (value.startswith(('\t', '\r', '\n')) or
                                   value.lstrip().startswith(('=', '+', '-', '@'))):
        return "'" + value
    return value


def export_csv(run_id):
    run=load_run(run_id)
    if run is None: return None
    cols=['timestamp','engine','model','host','port','concurrency','max_inflight','wall_seconds',
          'aggregate_tokens_per_second','succeeded','total','workload','enabled_categories','correct','incorrect','unreviewed',
          'index','category','scenario','ok','review','seconds','completion_tokens',
          'prompt_tokens','tokens_per_second','finish_reason','system_prompt','user_prompt',
          'content','reasoning','reasoning_tokens','review_guide','judge_score','judge_reason','error']
    buf=io.StringIO(newline='')
    writer=csv.DictWriter(buf,fieldnames=cols)
    writer.writeheader()
    for r in run['results']:
        writer.writerow({k:csv_cell(json.dumps(run['enabled_categories']) if k == 'enabled_categories'
                                  else r[k] if k in r else run.get(k,'')) for k in cols})
    return buf.getvalue().encode('utf-8-sig')
HTML = (Path(__file__).parent / 'index.html').read_text(encoding='utf-8')

def run_benchmark(run_id, settings):
    """Runs independently from the HTTP connection, so browser refreshes cannot cancel a run."""
    try:
        n=settings['concurrency']
        gate=threading.Event()
        started=time.perf_counter(); results=[]
        if settings['workload'] in ('agentic12','chat20'):
            gate.set(); context=''
            for i in range(n):
                r=investigate(i,settings['host'],settings['port'],settings['model'],settings['max_tokens'],settings['temperature'],settings['timeout'],gate,settings['workload'],settings['api_key'],settings['prompt_overrides'],context)
                r=judge_result(r,settings['judge']); results.append(r)
                context=(context+'\n\nPHASE '+str(i+1)+' MODEL OUTPUT:\n'+(r.get('content') or r.get('error') or ''))[-60000:]
                with JOB_LOCK:
                    j=JOBS[run_id];j['completed']+=1;j['successes']+=int(r['ok']);j['errors']+=int(not r['ok'])
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(n,settings['max_inflight']),thread_name_prefix='llm') as pool:
                futures=[pool.submit(investigate,i,settings['host'],settings['port'],settings['model'],settings['max_tokens'],settings['temperature'],settings['timeout'],gate,settings['workload'],settings['api_key'],settings['prompt_overrides'],enabled_categories=settings['enabled_categories']) for i in range(n)]
                gate.set()
                for f in concurrent.futures.as_completed(futures):
                    r=judge_result(f.result(),settings['judge']); results.append(r)
                    with JOB_LOCK:
                        j=JOBS[run_id];j['completed']+=1;j['successes']+=int(r['ok']);j['errors']+=int(not r['ok'])
        wall=time.perf_counter()-started
        results.sort(key=lambda r:r['index'])
        good=[r for r in results if r['ok']]
        output_tokens=sum(r['completion_tokens'] or 0 for r in good)
        summary={'run_id':run_id,'timestamp':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 'engine':engine_name(settings['port'],settings['specified_engine']),
                 'total':n,'succeeded':len(good),
                 'wall_seconds':round(wall,3),
                 'aggregate_tokens_per_second':round(output_tokens/wall,2) if wall and output_tokens else None,
                 'avg_request_seconds':round(sum(r['seconds'] for r in good)/len(good),3) if good else None,
                 'output_tokens':output_tokens}
        save_run(summary,results,settings)
        with JOB_LOCK:
            JOBS[run_id]['state']='done'
        print(f"Saved {summary['engine']} {settings['model']} · {len(good)}/{n} · {summary['wall_seconds']}s · run {run_id[:8]}",flush=True)
    except Exception as exc:
        print('Benchmark error:',repr(exc),flush=True)
        with JOB_LOCK:
            JOBS[run_id]['state']='error'
            JOBS[run_id]['error']=str(exc)
    finally:
        RUN_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        super().end_headers()

    def trusted_request(self):
        """Keep browser requests on the local dashboard's own origin.

        Host validation also prevents a DNS-rebinding page from reaching saved
        API keys or using the backend as an authenticated inference proxy.
        """
        try:
            hosts = self.headers.get_all('Host', [])
            if len(hosts) != 1:
                raise ValueError('Expected one Host header')
            raw = hosts[0]
            if any(char.isspace() for char in raw):
                raise ValueError('Invalid Host header')
            target = urlsplit('http://' + raw)
            hostname = target.hostname
            if not hostname or target.username or target.password or target.path or target.query or target.fragment:
                raise ValueError('Invalid Host header')
            allowed = hostname.lower() == 'localhost'
            try:
                allowed = allowed or ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                pass
            bind_host = str(self.server.server_address[0]).lower()
            if bind_host not in ('0.0.0.0', '::', ''):
                allowed = allowed or hostname.lower() == bind_host
            if CONFIG.host not in ('0.0.0.0', '::', ''):
                allowed = allowed or hostname.lower() == CONFIG.host.lower()
            # Docker may publish this service on a different host port. Trust
            # the local Host authority and compare Origin against that address.
            port = target.port if target.port is not None else 80
            if not allowed or not 1 <= port <= 65535:
                raise ValueError('Use the configured local dashboard address')
            dashboard_navigation = (self.command == 'GET' and urlparse(self.path).path == '/' and
                                    self.headers.get('Sec-Fetch-Mode') == 'navigate' and
                                    self.headers.get('Sec-Fetch-Dest') == 'document')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site' and not dashboard_navigation:
                raise ValueError('Cross-site requests are not allowed')
            origins = self.headers.get_all('Origin', [])
            if len(origins) > 1:
                raise ValueError('Expected one Origin header')
            if origins:
                origin = urlsplit(origins[0])
                if (origin.scheme != 'http' or origin.hostname != hostname or
                        (origin.port or 80) != port or origin.path or origin.query or origin.fragment or
                        origin.username or origin.password):
                    raise ValueError('Cross-origin requests are not allowed')
            return True
        except ValueError as exc:
            self.json_response(403, {'error': str(exc)})
            return False

    def json_response(self, code, obj):
        body=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        try:
            self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def do_GET(self):
        if not self.trusted_request():
            return
        parsed=urlparse(self.path)
        path=parsed.path
        if path=='/':
            body=HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store, max-age=0')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path=='/api/config':
            self.json_response(200, CONFIG.public_config())
        elif path=='/api/prompt-types':
            self.json_response(200,prompt_types())
        elif path=='/api/models':
            try:
                args=parse_qs(parsed.query)
                host=args.get('host',[DEFAULT_ENGINE_HOST])[0]
                port_text=args.get('port',['1234'])[0].strip()
                port=int(port_text) if port_text else 0
                profile_id=args.get('profile_id',[''])[0]
                api_key=load_engine_key(profile_id) if profile_id else self.headers.get('X-LLM-API-Key','')
                req=urllib.request.Request(endpoint_url(host,port,'models'),headers={'Authorization':'Bearer '+api_key} if api_key else {})
                with open_endpoint(req,timeout=12) as res:
                    data=json.load(res)
                self.json_response(200,{'models':[m['id'] for m in data.get('data',[])]})
            except Exception as exc:
                self.json_response(502,{'error':str(exc)})
        elif path.startswith('/api/job/'):
            job_id=path.rsplit('/',1)[-1]
            with JOB_LOCK:
                j=JOBS.get(job_id)
                state=dict(j) if j else None
            if state:
                state['elapsed_seconds']=round(time.perf_counter()-state['started_at'],2)
                state.pop('started_at',None)
                self.json_response(200,state)
            else:
                self.json_response(404,{'error':'Unknown job'})
        elif path=='/api/history':
            self.json_response(200,{'runs':load_history()})
        elif path.startswith('/api/history/'):
            run_id=path.split('/')[-1]
            data=load_run(run_id)
            self.json_response(200,data) if data else self.json_response(404,{'error':'No such saved run'})
        elif path.startswith('/api/export/'):
            run_id=path.split('/')[-1]
            csv_content=export_csv(run_id)
            if csv_content is None:
                self.json_response(404,{'error':'No such saved run'});return
            self.send_response(200)
            self.send_header('Content-Type','text/csv; charset=utf-8')
            self.send_header('Content-Disposition',f'attachment; filename="{APP_SLUG}-{run_id[:8]}.csv"')
            self.send_header('Content-Length',str(len(csv_content)))
            self.end_headers()
            self.wfile.write(csv_content)
        else:
            self.json_response(404,{'error':'Unknown endpoint'})

    def do_POST(self):
        if not self.trusted_request():
            return
        if self.path not in ('/api/run','/api/review','/api/engine-key','/api/engine-key/delete','/api/test-connection'):
            self.json_response(404,{'error':'Unknown endpoint'});return
        if self.headers.get_content_type() != 'application/json':
            self.json_response(415, {'error': 'Content-Type must be application/json'});return
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
            self.json_response(400, {'error': 'Expected one Content-Length header and no Transfer-Encoding'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=131072: raise ValueError('Invalid request size (maximum 128 KiB)')
            args=json.loads(self.rfile.read(length))
            if not isinstance(args,dict): raise ValueError('Expected JSON object')
        except (ValueError,TypeError,json.JSONDecodeError) as exc:
            self.json_response(400,{'error':str(exc)});return
        if self.path=='/api/test-connection':
            try:
                host=str(args.get('host','')).strip()
                port_text=str(args.get('port','')).strip()
                port=int(port_text) if port_text else 0
                profile_id=str(args.get('profile_id','')).strip()
                api_key=str(args.get('api_key','')).strip()
                if profile_id and api_key:
                    save_engine_key(profile_id,api_key)
                elif profile_id:
                    api_key=load_engine_key(profile_id)
                req=urllib.request.Request(endpoint_url(host,port,'models'),headers={'Authorization':'Bearer '+api_key} if api_key else {})
                with open_endpoint(req,timeout=12) as res: data=json.load(res)
                models=[m.get('id') for m in data.get('data',[]) if m.get('id')]
                self.json_response(200,{'connected':True,'models':models,'model_count':len(models)})
            except Exception as exc:
                self.json_response(502,{'connected':False,'error':str(exc)})
            return
        if self.path in ('/api/engine-key','/api/engine-key/delete'):
            try:
                profile_id=str(args['profile_id'])
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',profile_id): raise ValueError('Invalid engine profile id')
                if self.path.endswith('/delete'):
                    delete_engine_key(profile_id)
                else:
                    api_key=str(args.get('api_key','')).strip()
                    if not api_key: raise ValueError('API key is required')
                    save_engine_key(profile_id,api_key)
                self.json_response(200,{'stored':True})
            except (ValueError,RuntimeError,KeyError) as exc:
                self.json_response(400,{'error':str(exc)})
            return
        if self.path=='/api/review':
            try:
                run_id=str(args['run_id'])
                index=int(args['index'])
                review=str(args['review'])
                if review not in ('unreviewed','correct','incorrect') or not 1<=index<=MAX_REQUESTS or len(run_id)>100:
                    raise ValueError('Invalid review')
                exists=set_review(run_id,index,review)
                if not exists:
                    self.json_response(404,{'error':'Request not found'});return
                summary=load_run(run_id)
                self.json_response(200,{k:summary[k] for k in ('correct','incorrect','unreviewed')})
            except (ValueError,KeyError,TypeError) as exc:
                self.json_response(400,{'error':str(exc)})
            return

        try:
            host=str(args.get('host',DEFAULT_ENGINE_HOST)).strip()
            port=int(args.get('port',0) or 0)
            n=int(args['concurrency'])
            max_inflight=int(args.get('max_inflight',n))
            model=str(args['model'])
            max_tokens=int(args['max_tokens'])
            timeout=int(args['timeout'])
            temperature=float(args['temperature'])
            endpoint_url(host,port,'models')
            if not (1<=n<=MAX_REQUESTS and 1<=max_inflight<=MAX_REQUESTS and 64<=max_tokens<=8192 and
                    15<=timeout<=3600 and 0<=temperature<=2 and 0<len(model)<=300):
                raise ValueError('One or more settings are outside allowed limits')
            workload=str(args.get('workload','mixed'))
            if workload not in ('mixed','deep20','agentic12','chat20'):
                raise ValueError('Unknown benchmark suite')
            if workload=='deep20' and n!=20:
                raise ValueError('Deep reasoning suite requires exactly 20 requests')
            if workload=='agentic12' and n!=12:
                raise ValueError('Agentic Python suite requires exactly 12 sequential phases')
            if workload=='chat20' and n!=20:
                raise ValueError('Conversation speed suite requires exactly 20 sequential messages')
            if workload in ('mixed', 'deep20'):
                if 'enabled_categories' in args and args['enabled_categories'] is None:
                    raise ValueError('Prompt type selection must be a non-empty array')
                enabled_categories = normalize_categories(args.get('enabled_categories'))
            else:
                # Fixed sequential narratives retain every phase/message.
                enabled_categories = []
            specified=str(args.get('engine',''))
            profile_id=str(args.get('profile_id','')).strip()
            api_key=str(args.get('api_key','')).strip()
            if profile_id and api_key:
                save_engine_key(profile_id,api_key)
            elif profile_id:
                api_key=load_engine_key(profile_id)
            prompt_overrides=args.get('prompt_overrides',{})
            if not isinstance(prompt_overrides,dict):
                raise ValueError('Prompt overrides must be an object')
            if any(k not in CATEGORY_IDS or not isinstance(v, str) or len(v) > 10000
                   for k, v in prompt_overrides.items()):
                raise ValueError('Prompt overrides require known prompt types and strings of at most 10000 characters')
            prompt_overrides={k:v.strip() for k,v in prompt_overrides.items() if v.strip()}
            judge=args.get('judge',{})
            if not isinstance(judge,dict): raise ValueError('Judge settings must be an object')
            judge={'enabled':bool(judge.get('enabled',False)),'host':str(judge.get('host','')).strip(),
                   'port':int(judge.get('port',0) or 0),'model':str(judge.get('model','')).strip(),
                   'api_key':str(judge.get('apiKey','')),'timeout':120}
            if judge['enabled']:
                endpoint_url(judge['host'],judge['port'],'chat/completions')
                if not 0 < len(judge['model']) <= 300:
                    raise ValueError('Enabled judge requires a model')
        except (ValueError,TypeError,KeyError,RuntimeError) as exc:
            self.json_response(400,{'error':str(exc)});return
        if not RUN_LOCK.acquire(blocking=False):
            self.json_response(409,{'error':'Another benchmark is already running'});return
        run_id=uuid.uuid4().hex
        settings={'host':host,'port':port,'model':model,'concurrency':n,'max_inflight':max_inflight,'max_tokens':max_tokens,'timeout':timeout,'temperature':temperature,'specified_engine':specified,'workload':workload,'api_key':api_key,'prompt_overrides':prompt_overrides,'judge':judge,'enabled_categories':enabled_categories}
        with JOB_LOCK:
            JOBS[run_id]={'run_id':run_id,'state':'running','started_at':time.perf_counter(),
                          'completed':0,'successes':0,'errors':0,'total':n,'error':''}
            # Keep at most the most recent 20 completed job statuses in memory.
            if len(JOBS)>25:
                for old_id in list(JOBS):
                    if old_id!=run_id and JOBS[old_id]['state']!='running':
                        del JOBS[old_id]
                        if len(JOBS)<=20: break
        threading.Thread(target=run_benchmark, args=(run_id,settings),daemon=True).start()
        self.json_response(202,{'run_id':run_id,'state':'running','total':n})

    def log_message(self,*args):
        pass


def main():
    try:
        init_db()
        server=ThreadingHTTPServer((APP_HOST,APP_PORT),Handler)
    except (OSError, sqlite3.Error) as exc:
        raise SystemExit(f'Unable to start {APP_NAME}: {exc}') from None
    browser_host = '127.0.0.1' if APP_HOST == '0.0.0.0' else APP_HOST
    dashboard_url = f'http://{browser_host}:{APP_PORT}'
    print(f'{APP_NAME} v{VERSION} — Configurable benchmarks + history — {dashboard_url}')
    print(f'History database: {DB_PATH}')
    print('Press Ctrl+C to stop.')
    if CONFIG.open_browser:
        webbrowser.open(dashboard_url)
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nStopped.')
    finally: server.server_close()


if __name__=='__main__':
    main()
