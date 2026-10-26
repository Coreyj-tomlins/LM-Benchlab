#!/usr/bin/env python3
"""ModelRig v7.2 — 500-load and 20-case deep-thinking benchmarks.
Python 3.9+, standard library only. Windows / Linux, localhost-only API and UI.
"""
import concurrent.futures
import csv
import io
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from complex_cases import DEEP_CASES
try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception

APP_HOST = os.getenv('APP_HOST', '0.0.0.0')
APP_PORT = int(os.getenv('APP_PORT', '8768'))
MAX_REQUESTS = 500
DB_PATH = Path(os.getenv('LLM_LAB_DB', '/data/llm_concurrency_history.sqlite3'))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DB_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
JOBS = {}
ALLOWED_HOST = re.compile(r'^[A-Za-z0-9.-]{1,253}$')
MASTER_KEY = os.getenv('LLM_LAB_MASTER_KEY', '').strip()

def cipher():
    if not Fernet or not MASTER_KEY:
        raise RuntimeError('Encrypted key storage is unavailable; configure LLM_LAB_MASTER_KEY')
    try:
        return Fernet(MASTER_KEY.encode('ascii'))
    except Exception as exc:
        raise RuntimeError('LLM_LAB_MASTER_KEY must be a valid Fernet key') from exc

def valid_host(name):
    if not isinstance(name, str) or not ALLOWED_HOST.fullmatch(name) or '..' in name:
        raise ValueError('Host must be a hostname or IP without http:// or a port')
    return name



def connect_db():
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


def init_db():
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
    except InvalidToken as exc: raise RuntimeError('Stored engine key cannot be decrypted; check LLM_LAB_MASTER_KEY') from exc

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

# Each request uses a distinct synthetic scenario. Four categories are interleaved
# so any request count from 1 to 100 includes a representative mix.
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
CHAT_CASES = [
 ('VPN connection', 'My VPN connects but I cannot reach the internal file server. What should I check?'),
 ('Wi-Fi instability', 'My laptop Wi-Fi disconnects every few minutes; can you help me troubleshoot?'),
 ('Password manager', 'How do I move to a password manager safely for my small team?'),
 ('Slow video call', 'Teams calls lag when working from home; what diagnostics should I try?'),
 ('MFA setup', 'I have a new phone. How should I move my MFA accounts without locking myself out?'),
 ('Printer VLAN', 'I can ping my printer but printing fails across VLANs. What next?'),
 ('Small office network', 'Explain the difference between a router, switch and firewall simply.'),
 ('Phishing advice', 'I clicked a suspicious link but did not enter credentials. What should I do?'),
 ('DNS troubleshooting', 'Some websites work and others fail for my laptop only. What could be wrong?'),
 ('Patch scheduling', 'How should we schedule security patches for servers with limited downtime?'),
 ('Guest Wi-Fi', 'What is a safe way to offer guest Wi-Fi in an office?'),
 ('Workstation backup', 'Explain 3-2-1 backups for a nontechnical business owner.'),
 ('Access request', 'Draft a friendly reply asking for manager approval for elevated access.'),
 ('Network monitoring', 'How can monitoring tell me the difference between an outage and slowness?'),
 ('Public IP explanation', 'Why does my home network have a public IP and private IPs?'),
 ('Certificate warning', 'My browser reports an invalid certificate on an internal portal. What now?'),
 ('File share permissions', 'How do I request access to a team file share appropriately?'),
 ('Two-factor methods', 'Compare authenticator apps with SMS codes for a small business.'),
 ('Zero trust overview', 'What does zero trust mean in plain English?'),
 ('Home router security', 'What are five safe settings to review on a home router?'),
 ('Network diagram', 'Explain what information a useful network diagram should contain.'),
 ('Meeting recap', 'Write a concise meeting recap for a discussion about a firewall upgrade.'),
 ('Remote work policy', 'What are practical network security recommendations for remote workers?'),
 ('IT onboarding', 'Outline a first-day IT onboarding checklist for a new employee.'),
 ('Incident status', 'Write a clear nontechnical status update for an intermittent WAN outage.'),
]
SALES_CASES = [
 ('MSSP discovery', 'Create five discovery questions for a 150-user business evaluating managed SOC.'),
 ('SOC proposal', 'Draft a short managed SOC value proposition for a manufacturing company.'),
 ('Firewall refresh', 'Write an outreach email proposing a next-generation firewall assessment.'),
 ('Service tiers', 'Explain a simple three-tier managed network monitoring service offering.'),
 ('Renewal objection', 'Customer says managed monitoring is too expensive; propose a consultative response.'),
 ('Customer follow-up', 'Draft a friendly follow-up after a first network security discovery call.'),
 ('Executive summary', 'Summarise benefits of 24/7 alert triage for a nontechnical CFO.'),
 ('Cross-sell', 'Suggest ethical opportunities to discuss SIEM with an existing network monitoring customer.'),
 ('Proposal scope', 'Create a scope outline for firewall configuration review and recommendations.'),
 ('ROI questions', 'List questions needed to quantify the value of automated incident investigations.'),
 ('Competitive differentiation', 'Explain non-hype differentiators for a mid-sized MSSP.'),
 ('Onboarding timeline', 'Draft a high-level 30-day onboarding plan for managed monitoring.'),
 ('Customer objections', 'Answer a prospect worried about false positives in a managed SIEM.'),
 ('Quarterly review', 'Suggest useful KPIs for a quarterly managed security service review.'),
 ('Risk reduction', 'Translate vulnerability remediation into an executive business benefit.'),
 ('Service description', 'Write a 120-word description of an interface-down investigation service.'),
 ('SLA language', 'Explain the difference between response SLA and resolution target.'),
 ('Requirements checklist', 'Create a short customer questionnaire for managed network monitoring.'),
 ('Stakeholder pitch', 'Write a 1-minute pitch for an IT director considering SOC outsourcing.'),
 ('Pricing framework', 'Describe inputs for a managed SIEM pricing model without inventing prices.'),
 ('RFP response', 'Draft a concise response to an RFP question about incident escalation.'),
 ('Customer success', 'Suggest three proactive touchpoints during the first 60 days of service.'),
 ('Partner referral', 'Draft an introductory email to a potential managed security channel partner.'),
 ('Board presentation', 'Create a short outline explaining cyber monitoring investment to a board.'),
 ('Upsell timing', 'Suggest when to raise a backup monitoring service in a customer conversation.'),
]
SYSTEM_PROMPTS = {
 'Interface': 'You are a senior MSSP network engineer. Use only supplied evidence; never invent test results. Respond with likely causes, three vendor-specific commands, next steps, and escalation criteria. Keep it concise.',
 'SIEM': 'You are a senior SOC analyst. Use only evidence supplied, do not assert compromise without proof. Give severity with justification, investigation pivots, containment considerations and ticket summary. Keep it concise.',
 'Chat': 'You are a helpful IT support assistant. Reply directly, safely and clearly with practical steps, and one targeted clarifying question when appropriate. Keep it concise.',
 'Sales': 'You are an experienced ethical MSSP account consultant. Produce useful, practical business writing appropriate to the task; avoid unsubstantiated claims, invented prices or guarantees. Keep it concise.',
}

def scenario_for(index, workload='mixed'):
    if workload == 'deep20':
        case=DEEP_CASES[index % len(DEEP_CASES)]
        return case['category'], case['title'], case['prompt'], case['checks']
    category = ('Interface', 'SIEM', 'Chat', 'Sales')[index % 4]
    variant = index // 4
    if category == 'Interface':
        title, device, interface, evidence = PROMPTS[variant % len(PROMPTS)]
        question = (f'Customer site {variant+1:02d}. Alert: {title}\nDevice: {device}\nInterface: {interface}\n'
                    f'Evidence: {evidence}\nGive triage, commands, next actions, escalation and a brief ServiceNow note.')
    else:
        pool = {'SIEM': SIEM_CASES, 'Chat': CHAT_CASES, 'Sales': SALES_CASES}[category]
        title, evidence = pool[variant % len(pool)]
        question = f'Case {variant+1:02d}: {evidence}'
    return category, title, question, []



def invoke_model(host, port, payload, timeout, api_key=''):
    headers={'Content-Type':'application/json'}
    if api_key:
        headers['Authorization']='Bearer '+api_key
    req = urllib.request.Request(
        f'http://{host}:{port}/v1/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


DEEP_SYSTEM_PROMPTS = {
 'Interface': 'You are a senior MSSP network engineer. Analyse multi-layer evidence and contradictory clues. Produce a clear structured answer covering observations, ranked hypotheses with confidence, discriminating read-only checks, a time-ordered safe action plan, escalation and a factual ServiceNow update. Do not invent measurements, change device state, or assert unverified root causes. Explain key decision points in your final answer.',
 'SIEM': 'You are a senior SOC incident responder. Separate observed events from inference; account for false positives, identity uncertainty and business impact. Produce a timeline, a ranked hypothesis assessment, precise investigation pivots, containment options with approvals, and a concise stakeholder update. Never claim unproven compromise or exfiltration. Explain the critical evidence and decision points in your final answer.',
 'Chat': 'You are an experienced enterprise IT support engineer. Respond to the latest turn of the provided conversation, take account of previous messages and user constraints, and provide a safe and practical step-by-step resolution with a concise explanation and clear escalation or rollback conditions. Never fabricate actions already performed.',
 'Sales': 'You are a rigorous MSSP solutions consultant. Work through numerical assumptions accurately; distinguish calculations from unknown commercial inputs, avoid invented prices, and develop a realistic decision-ready recommendation with explicit caveats and measurable next steps. Explain the important assumptions and checks in the final answer.'
}


def investigate(index, host, port, model, max_tokens, temperature, timeout, gate, workload='mixed', api_key='', prompt_overrides=None):
    category, scenario, question, checks = scenario_for(index, workload)
    defaults = DEEP_SYSTEM_PROMPTS if workload=='deep20' else SYSTEM_PROMPTS
    system_prompt = (prompt_overrides or {}).get(category) or defaults[category]
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
    for category in SYSTEM_PROMPTS:
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
        db.execute('''INSERT INTO runs (id,timestamp,engine,port,host,model,concurrency,max_tokens,timeout,temperature,wall_seconds,aggregate_tokens_per_second,succeeded,total,avg_request_seconds,output_tokens,max_inflight,workload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            summary['run_id'],summary['timestamp'],summary['engine'],settings['port'],settings['host'],
            settings['model'], settings['concurrency'],settings['max_tokens'],
            settings['timeout'],settings['temperature'], summary['wall_seconds'],
            summary['aggregate_tokens_per_second'],summary['succeeded'],summary['total'],
            summary['avg_request_seconds'],summary['output_tokens'],settings['max_inflight'],settings['workload']))
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
    return d


def load_history():
    with connect_db() as db:
        rows=db.execute('''SELECT r.*,
            COALESCE(SUM(CASE WHEN q.review='correct' THEN 1 ELSE 0 END),0) AS correct,
            COALESCE(SUM(CASE WHEN q.review='incorrect' THEN 1 ELSE 0 END),0) AS incorrect,
            COALESCE(SUM(CASE WHEN q.review='unreviewed' THEN 1 ELSE 0 END),0) AS unreviewed
            FROM runs r LEFT JOIN requests q ON q.run_id=r.id
            GROUP BY r.id ORDER BY r.timestamp DESC, r.rowid DESC LIMIT 300''').fetchall()
    return [dict(x) for x in rows]


def load_run(run_id):
    with connect_db() as db:
        run=db.execute('''SELECT * FROM runs WHERE id=?''',(run_id,)).fetchone()
        if not run: return None
        requests=db.execute('SELECT * FROM requests WHERE run_id=? ORDER BY request_index',(run_id,)).fetchall()
    rlist=[rowdict(q) for q in requests]
    reason_reports=[x['reasoning_tokens'] for x in rlist if x.get('reasoning_tokens') is not None]
    return {**dict(run),'results':rlist,'groups':grouped_results(rlist),
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


def export_csv(run_id):
    run=load_run(run_id)
    if run is None: return None
    cols=['timestamp','engine','model','host','port','concurrency','max_inflight','wall_seconds',
          'aggregate_tokens_per_second','succeeded','total','workload','correct','incorrect','unreviewed',
          'index','category','scenario','ok','review','seconds','completion_tokens',
          'prompt_tokens','tokens_per_second','finish_reason','system_prompt','user_prompt',
          'content','reasoning','reasoning_tokens','review_guide','judge_score','judge_reason','error']
    buf=io.StringIO(newline='')
    writer=csv.DictWriter(buf,fieldnames=cols)
    writer.writeheader()
    for r in run['results']:
        writer.writerow({k:(r[k] if k in r else run.get(k,'')) for k in cols})
    return buf.getvalue().encode('utf-8-sig')
HTML = (Path(__file__).parent / 'index.html').read_text(encoding='utf-8')

def run_benchmark(run_id, settings):
    """Runs independently from the HTTP connection, so browser refreshes cannot cancel a run."""
    try:
        n=settings['concurrency']
        gate=threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(n,settings['max_inflight']),thread_name_prefix='llm') as pool:
            futures=[pool.submit(investigate,i,settings['host'],settings['port'],
                                 settings['model'],settings['max_tokens'],settings['temperature'],
                                 settings['timeout'],gate,settings['workload'],settings['api_key'],settings['prompt_overrides']) for i in range(n)]
            started=time.perf_counter()
            gate.set()
            results=[]
            for f in concurrent.futures.as_completed(futures):
                r=judge_result(f.result(),settings['judge'])
                results.append(r)
                with JOB_LOCK:
                    j=JOBS[run_id]
                    j['completed']+=1
                    j['successes']+=int(r['ok'])
                    j['errors']+=int(not r['ok'])
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
        elif path=='/api/models':
            try:
                args=parse_qs(parsed.query)
                port=int(args.get('port',['1234'])[0])
                host=valid_host(args.get('host',['host.docker.internal'])[0])
                if not 1<=port<=65535: raise ValueError('Invalid port')
                profile_id=args.get('profile_id',[''])[0]
                api_key=load_engine_key(profile_id) if profile_id else self.headers.get('X-LLM-API-Key','')
                req=urllib.request.Request(f'http://{host}:{port}/v1/models',headers={'Authorization':'Bearer '+api_key} if api_key else {})
                with urllib.request.urlopen(req,timeout=12) as res:
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
            self.send_header('Content-Disposition',f'attachment; filename="benchmark-{run_id[:8]}.csv"')
            self.send_header('Content-Length',str(len(csv_content)))
            self.end_headers()
            self.wfile.write(csv_content)
        else:
            self.json_response(404,{'error':'Unknown endpoint'})

    def do_POST(self):
        if self.path not in ('/api/run','/api/review','/api/engine-key','/api/engine-key/delete','/api/test-connection'):
            self.json_response(404,{'error':'Unknown endpoint'});return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=12000: raise ValueError('Invalid request size')
            args=json.loads(self.rfile.read(length))
            if not isinstance(args,dict): raise ValueError('Expected JSON object')
        except (ValueError,TypeError,json.JSONDecodeError) as exc:
            self.json_response(400,{'error':str(exc)});return
        if self.path=='/api/test-connection':
            try:
                host=valid_host(str(args.get('host','')))
                port=int(args.get('port'))
                if not 1<=port<=65535: raise ValueError('Invalid port')
                profile_id=str(args.get('profile_id','')).strip()
                api_key=str(args.get('api_key','')).strip()
                if profile_id and api_key:
                    save_engine_key(profile_id,api_key)
                elif profile_id:
                    api_key=load_engine_key(profile_id)
                req=urllib.request.Request(f'http://{host}:{port}/v1/models',headers={'Authorization':'Bearer '+api_key} if api_key else {})
                with urllib.request.urlopen(req,timeout=12) as res: data=json.load(res)
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
            port=int(args['port'])
            host=valid_host(args.get('host','host.docker.internal'))
            n=int(args['concurrency'])
            max_inflight=int(args.get('max_inflight',n))
            model=str(args['model'])
            max_tokens=int(args['max_tokens'])
            timeout=int(args['timeout'])
            temperature=float(args['temperature'])
            if not (1<=port<=65535 and 1<=n<=MAX_REQUESTS and 1<=max_inflight<=MAX_REQUESTS and 64<=max_tokens<=8192 and
                    15<=timeout<=3600 and 0<=temperature<=2 and 0<len(model)<=300):
                raise ValueError('One or more settings are outside allowed limits')
            workload=str(args.get('workload','mixed'))
            if workload not in ('mixed','deep20'):
                raise ValueError('Unknown benchmark suite')
            if workload=='deep20' and n!=20:
                raise ValueError('Deep reasoning suite requires exactly 20 requests')
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
            prompt_overrides={str(k):str(v)[:10000] for k,v in prompt_overrides.items() if str(v).strip()}
            judge=args.get('judge',{})
            if not isinstance(judge,dict): raise ValueError('Judge settings must be an object')
            judge={'enabled':bool(judge.get('enabled',False)),'host':valid_host(str(judge.get('host','host.docker.internal'))),'port':int(judge.get('port',1234)),'model':str(judge.get('model','')),'api_key':str(judge.get('apiKey','')),'timeout':120}
            if judge['enabled'] and (not judge['model'] or not 1<=judge['port']<=65535):
                raise ValueError('Enabled judge requires a model and valid port')
        except (ValueError,TypeError,KeyError) as exc:
            self.json_response(400,{'error':str(exc)});return
        if not RUN_LOCK.acquire(blocking=False):
            self.json_response(409,{'error':'Another benchmark is already running'});return
        run_id=uuid.uuid4().hex
        settings={'host':host,'port':port,'model':model,'concurrency':n,'max_inflight':max_inflight,'max_tokens':max_tokens,'timeout':timeout,'temperature':temperature,'specified_engine':specified,'workload':workload,'api_key':api_key,'prompt_overrides':prompt_overrides,'judge':judge}
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


if __name__=='__main__':
    init_db()
    server=ThreadingHTTPServer((APP_HOST,APP_PORT),Handler)
    print(f'ModelRig v7.2 — Deep reasoning + history — http://{APP_HOST}:{APP_PORT}')
    print(f'History database: {DB_PATH}')
    print('Press Ctrl+C to stop.')
    if os.environ.get('LLM_LAB_NO_BROWSER','1')!='1':
        webbrowser.open(f'http://{APP_HOST}:{APP_PORT}')
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nStopped.')
    finally: server.server_close()
