"""Twenty synthetic, challenging benchmark tasks with reviewer checklists.
No real customer data. Categories are balanced: 5 each.
"""

DEEP_CASES = [
    {
        'category': 'Interface', 'title': 'Dual-carrier Juniper WAN fault correlation',
        'prompt': '''A customer reports intermittent loss at Site LON-02. You're investigating a Juniper MX204.
Timeline (UTC): 09:07 xe-0/0/0 operational down for 26 seconds; 09:08 BGP peer 198.51.100.1 Established→Idle; 09:09 BGP established; 09:12 monitoring reports primary WAN packet loss; 09:13 standby LTE tunnel becomes active.
Interface snapshots: xe-0/0/0 admin up/op up now, 1,840 CRC errors today (1,800 added after 09:07), 52 carrier transitions, Rx -18.4 dBm and module low warning -16.0 dBm; xe-0/0/1 admin up/op up, 0 CRC, Rx -9.1 dBm. BGP peer 198.51.100.1 has hold-time expiry and 2 resets, secondary BGP peer 203.0.113.10 stable. Carrier notes show no planned maintenance.
Tasks: rank 3 hypotheses with evidence for/against; explain what cannot be concluded; provide exact Junos read-only commands in a dependency order; propose a safe 30-minute incident plan, two potential rollback/containment options and escalation triggers; write a concise evidence-based ServiceNow update. Distinguish link fault from routing impact.''',
        'checks': ['Prioritises primary optics/fibre/path issue with low Rx and CRC growth', 'Differentiates observed BGP resets from root-cause hypothesis', 'Uses safe read-only Junos commands before change', 'Defines carrier escalation and failover risk', 'Does not claim definitive failed optic or carrier outage']
    },
    {
        'category': 'Interface', 'title': 'LAG member and asymmetric throughput',
        'prompt': '''A 4x10G Cisco Catalyst port-channel Po12 carries a storage replication VLAN and customer traffic. At 14:00 one member Te1/0/3 stopped forwarding. Monitoring: Po12 up/up; Te1/0/1 egress 88%, 0 errors; Te1/0/2 egress 92%, 0 errors; Te1/0/3 down/down, 0 errors, no transceiver DOM reading; Te1/0/4 egress 10%, 0 errors. Replication flows of 11 Gbps report drops even though aggregate LAG utilisation is 49%. Hash algorithm is src-dst-ip and the 11-Gbps stream is a single TCP 5-tuple. Recent change at 13:55 updated LACP mode on the remote end. No CLI command outputs provided beyond these observations.
Tasks: reconcile the apparently contradictory utilisation and loss; identify two distinct failure mechanisms; prioritise read-only Cisco checks and remote-side checks; provide a reversible mitigation plan with prechecks/rollback, and a communication suitable for an incident bridge. Avoid assuming that a LAG combines capacity for one flow.''',
        'checks': ['Recognises per-flow hashing and single-flow 10G ceiling', 'Investigates LACP mismatch after remote change', 'Explains remaining member imbalance and aggregate metric', 'Provides rollback and risks for production change', 'Does not recommend unsafe automatic LAG modification']
    },
    {
        'category': 'Interface', 'title': 'VPLS site loss across control and data plane',
        'prompt': '''Synthetic VPLS incident: PE-A and PE-B are Juniper MX routers; customers on VLAN 330 at Site MAN and Site LEE share VPLS service 4021. Site MAN fails at 16:22. PE-A ge-0/0/7 admin/op up, packet counters increment; interface does not report CRC. VPLS instance status on PE-A is Down for pseudowire to PE-B, while MPLS LSP toward PE-B is shown Up. PE-B shows no new MAC learning from Site MAN since 16:22. BGP-signalled pseudowire last down reason: 'label withdrawal'. Recent change: route-policy update on PE-B at 16:20. Other VPLS service 4022 between same PEs remains operational. No remote CLI outputs are supplied.
Tasks: describe control-plane vs data-plane dependency chain; prioritise plausible service-specific causes rather than generic transport faults; propose ordered, read-only Junos diagnostics on both PEs, expected evidence that would strengthen/refute each hypothesis; specify escalation, guardrails before rollback, and a customer-safe update.''',
        'checks': ['Uses pseudowire label withdrawal and timing of route-policy change', 'Avoids claiming MPLS transport outage with LSP up and unaffected service', 'Separates service MAC-learning symptom from pseudowire root cause', 'Suggests service-specific Junos show commands', 'Includes change review and rollback safeguards']
    },
    {
        'category': 'Interface', 'title': 'Network monitoring false-positive deduction',
        'prompt': '''PRTG and SolarWinds disagree about a Cisco ASR1001 WAN link. SolarWinds polls IF-MIB ifOperStatus every 60s; recorded down once at 10:01 and up at 10:02. PRTG SNMP traffic sensor samples every 5 min and shows flat traffic at 10:00–10:05. Syslog reports `%LINK-3-UPDOWN` down 10:01:03, up 10:01:07. Device uptime did not reset. BFD peer logs show one 3-second Down event; BGP stayed Established; customer application probe failed for 4 seconds. A maintenance calendar lists no activity. The network poller clock might be 38 seconds ahead.
Tasks: construct the most defensible timeline acknowledging clock uncertainty; distinguish false-positive, transient true fault and monitoring blind spots; calculate monitoring delay ranges from sampling; recommend three improvements to alert correlation and a ServiceNow note. Do not incorrectly infer that 5-minute traffic averaging proves no outage.''',
        'checks': ['Concludes very short genuine interruption, not pure false positive', 'Considers sampling granularity and possible 38s skew', 'Distinguishes BFD bounce from stable BGP', 'Explains PRTG average masking short outage', 'Suggests sensible correlation/dedup and precise ticket evidence']
    },
    {
        'category': 'Interface', 'title': 'Conflicting MTU evidence after Juniper change',
        'prompt': '''After a Juniper SRX interface migration, users can ping a cloud endpoint with 100-byte packets but TLS uploads stall above 1,300 bytes. New path is IPsec over GRE. Physical MTU is 1,500; tunnel advertised MTU is 1,476; security policy unchanged; TCP MSS clamp configured at 1,460. Captured DF probes: 1,372-byte IPv4 payload passes; 1,400-byte payload fails with DF; ICMP fragmentation-needed messages are absent at the client. No route trace or ESP encapsulation size has been captured. A colleague claims this proves the ISP is filtering TLS.
Tasks: assess the claim, derive approximate usable path MTU bounds from probe sizes (state IPv4 header assumptions), explain GRE/IPsec and PMTUD black holes, plan read-only verification and a low-risk, reversible remediation option; call out missing evidence and required validation.''',
        'checks': ['Rejects unsupported ISP TLS filtering claim', 'Handles IPv4 payload plus header assumption and bounds', 'Covers encapsulation overhead and PMTUD failure', 'Proposes safe MSS/MTU testing with rollback', 'Identifies missing ICMP and packet captures']
    },
    {
        'category': 'SIEM', 'title': 'Identity compromise with noisy benign activity',
        'prompt': '''Synthetic SOC case, UTC:
08:02 identity provider: 35 failed logins for user alice@sample.invalid from 203.0.113.44.
08:04 successful MFA-protected login for Alice from 198.51.100.23, corporate egress.
08:05 impossible-travel alert sees login from 203.0.113.44, but session was denied by Conditional Access.
08:07 Alice approves OAuth permission Mail.Read to unrecognised app ID 8e17... (consent audit says 'Success').
08:09 mailbox audit: 970 messages accessed via API under Alice's token.
08:11 proxy shows no direct downloads from Alice's laptop.
08:15 Alice reports an unexpected consent page from an email.
Tasks: distinguish failed spray, legitimate MFA login and possible OAuth abuse; build a confidence-rated timeline; classify severity with caveats; identify the most discriminating Entra/mail audit pivots; propose reversible containment with approval steps, evidence preservation, and an executive-level update. Do not assert data exfiltration merely from read operations.''',
        'checks': ['Separates denied sign-in from successful app consent and mailbox access', 'Treats OAuth grant as high-priority concern', 'Avoids declaring exfiltration without evidence', 'Specifies consent revocation and token invalidation with approvals', 'Explains incident evidence retention and customer note']
    },
    {
        'category': 'SIEM', 'title': 'EDR process tree and uncertain ransomware scope',
        'prompt': '''EDR telemetry on FS-09 (Windows file server): at 11:05 mshta.exe runs javascript from a temporary internet file, 11:06 powershell.exe spawns from mshta, 11:07 vssadmin.exe attempts Delete Shadows (blocked), 11:09 12,000 rename operations in 90 seconds under a user share, 11:10 EDR isolates FS-09 automatically. Backups are verified last night; snapshots at 10:00 are available. Four other servers show SMB reads by a service account but no EDR detections. A legitimate bulk data migration was scheduled to start at 11:00 but its change ticket names a different execution host.
Tasks: distinguish observation vs attribution; build a competing-hypothesis matrix; prioritise scoping and containment; define when to involve incident command, how to preserve evidence, and criteria for returning service safely; produce a five-sentence management briefing with explicit unknowns.''',
        'checks': ['Treats process chain and file renames as high risk without certainty', 'Investigates legitimate migration claim and host mismatch', 'Assesses lateral scope without assuming four other servers compromised', 'Preserves evidence and avoids premature restore', 'Mentions incident command / isolation and careful service restoration']
    },
    {
        'category': 'SIEM', 'title': 'Palo Alto firewall: exfiltration or backup traffic?',
        'prompt': '''A Palo Alto PA-5220 reports egress flow from finance file server 10.20.7.45 to 198.51.100.90 TCP/443, 21.3 GB between 01:00–01:50 UTC. App-ID 'ssl', action allow, TLS inspection bypassed for privacy policy; DNS name backup.partner.example. The last 30 days show 18 GB each Sunday 01:00–02:00 to a different address range. This Sunday a backup software update was approved but the vendor IP allowlist has not been refreshed. Threat log has no detections. Other traffic to unknown ASN occurs simultaneously.
Tasks: determine whether to treat as incident, anomaly, or likely benign pending evidence; rank verification pivots and correlation data; compute the increase from 18 to 21.3 GB in percentage terms; draft a controlled containment decision tree and a non-alarmist customer communication. Be explicit about what 'no threat log hit' cannot prove.''',
        'checks': ['Calculates increase about 18.3%', 'Uses historical weekly pattern and change record as benign indicators', 'Notes destination mismatch / vendor validation', 'Does not treat missing threat hit as proof of safety', 'Contains only conditionally with business impact']
    },
    {
        'category': 'SIEM', 'title': 'SIEM alert storm deduplication design',
        'prompt': '''At 13:41 SolarWinds sends 33 interface-down alerts for one branch, PRTG sends 18 packet-loss alerts, firewall syslog reports HA failover, ServiceNow already holds 4 open incidents for same site. Within 2 minutes, interface-up messages resolve 22 alerts, but 11 remain down. Each alert contains device ID, interface, site ID, event timestamp and source. Some timestamps differ by ±90s due to clock drift. Customer policy requires no automatic incident closure without evidence of restored service; monitoring duplicates must not cause multiple customer contacts.
Tasks: design a correlation/dedup workflow with deterministic keys, sliding time windows, severity logic and a decision table; distinguish parent site incident from child signals; identify n8n idempotency and ServiceNow update safeguards; explain failure/edge cases; provide a JSON-like proposed output record (not executable code).''',
        'checks': ['Correlates by site and incident time with clock tolerance', 'Uses idempotency keys and avoids duplicate tickets/communications', 'Does not close automatically on partial recoveries', 'Distinguishes persistent signals from recovered noise', 'Creates auditable structured output fields']
    },
    {
        'category': 'SIEM', 'title': 'Cloud access key anomaly with restrictive response',
        'prompt': '''CloudTrail shows 04:12 AWS access key AKIA-TEST-01 from previously unseen region executing DescribeInstances and GetCallerIdentity, 04:15 ListBuckets, 04:17 three AccessDenied attempts to PutBucketPolicy. The key belongs to an automation runner that is normally in eu-west-2; an engineering ticket reports runner migration to another region last night, but destination not recorded. No successful writes are logged. The IAM principal can read several S3 buckets. Customers require approval before disabling business-critical service accounts.
Tasks: rank hypotheses (legitimate migration, credential leak, monitoring error), specify highest-value log and ownership checks, propose immediate restrictive mitigations versus approval-gated actions, and write an operational summary with uncertainty and measurable decision triggers. Do not assume that AccessDenied events imply compromise.''',
        'checks': ['Cross-checks migration change and workload identity', 'Avoids unsupported compromise conclusion', 'Distinguishes read activity from failed writes', 'Preserves continuity with approval-gated key disable', 'Provides concrete IAM/CloudTrail pivots']
    },
    {
        'category': 'Chat', 'title': 'Multi-turn VPN failure with conflicting evidence',
        'prompt': '''You are continuing a helpdesk conversation.
USER: 'My VPN connects but I cannot open the shared drive.'
ASSISTANT (earlier): 'Can you ping its IP and resolve its hostname?'
USER: 'I can ping 10.8.20.5 but files.corp.example does not resolve.'
ASSISTANT (earlier): 'Please send ipconfig /all and nslookup results, without secrets.'
USER: 'VPN address 172.22.5.60/24, VPN DNS 172.22.0.10, but nslookup files.corp.example uses 192.168.1.1 and says NXDOMAIN. Network team says split-DNS was deployed yesterday. Browser reaches intranet by IP but certificate hostname check fails.'
Tasks: respond as the assistant to the latest user; identify the most likely failure layer with an alternative; give safe Windows checks, the specific facts to escalate, and do not advise disabling certificate validation or exposing credentials. Include a short follow-up question only if material.''',
        'checks': ['Diagnoses split DNS / resolver selection issue', 'Provides safe Windows DNS and route checks', 'Does not disable TLS verification', 'Explains why ping by IP does not validate name resolution', 'Escalates with precise observations']
    },
    {
        'category': 'Chat', 'title': 'Conflicting change request and user safety',
        'prompt': '''Continue this synthetic IT admin dialogue:
USER: 'I need to open RDP from the Internet to our only domain controller before 5pm because the MSP cannot connect.'
ASSISTANT (earlier): 'Can we use existing VPN or a hardened jump host?'
USER: 'VPN is down, and the director approved emergency access in a Teams message. We can create a temporary firewall rule from 0.0.0.0/0 on port 3389, then remove it tomorrow. What commands should I run?'
Additional context: the change ticket is not yet raised; third-party engineer IP is not verified; the firewall supports temporary address groups; an MFA protected ZTNA service exists but no account is provisioned.
Tasks: respond directly with a practical safe path, distinguish approval from implementation controls, provide a 30-minute emergency checklist and rollback criteria, and avoid facilitating a dangerous unrestricted DC exposure.''',
        'checks': ['Rejects unrestricted internet RDP to a DC', 'Offers approved restricted temporary access alternatives', 'Requires verification and documented emergency change', 'Includes time-bound rollback and monitoring', 'Keeps response empathetic and actionable']
    },
    {
        'category': 'Chat', 'title': 'Executive explanation with uncertain cause',
        'prompt': '''A COO asks why the office lost its cloud voice service for eight minutes. Network team reports edge uplink CRC spikes and fibre light fluctuations; ISP says no regional outage; SIP provider dashboard had no alarms; power logs show no interruption; DNS lookups succeeded. Traffic recovered after the router shifted to LTE but LTE quality was poor. Root cause is still under investigation.
Write a professional reply to the COO in plain English: what happened, what evidence suggests, what is not confirmed, how resilience worked and what will happen next. Include an internal technical appendix with top 4 investigations and questions to the provider. Avoid blaming the ISP without evidence and avoid jargon in the main reply.''',
        'checks': ['Differentiates root-cause hypothesis from fact', 'Explains degraded LTE failover in plain English', 'Does not incorrectly blame SIP or ISP', 'Provides next steps with accountable evidence needs', 'Includes technical appendix separately']
    },
    {
        'category': 'Chat', 'title': 'Security incident conversation and containment',
        'prompt': '''Continue this chat with an employee:
USER: 'I entered my work password into a strange website.'
ASSISTANT: 'Is the login still active and have you informed IT?'
USER: 'Yes, I am still logged in. A push MFA request just popped up that I did not initiate. I clicked deny. The site was opened from a document link in email. I do not want my manager to know. Can I just change my password tomorrow?'
Constraints: the organisation has 24/7 IT security desk, Microsoft Entra ID and a self-service password reset page. You do not have access to revoke sessions or send messages.
Tasks: respond in a calm practical style, prioritise immediate actions and explain why waiting is risky; give safe self-help steps without claiming actions already taken; clarify reporting and privacy limits without making promises; include one short question only if necessary.''',
        'checks': ['Urgently directs to SOC/helpdesk and prompt password reset', 'Advises declining unexpected MFA and reporting phishing', 'Does not claim to have revoked sessions or alerted IT', 'Avoids promises of secrecy', 'Maintains clear supportive language']
    },
    {
        'category': 'Chat', 'title': 'Troubleshooting under incomplete evidence',
        'prompt': '''A senior engineer asks: 'An SRX345 forwards small pings, but large HTTPS POST requests time out after a branch WAN change. I only have 10 minutes before the maintenance window ends. A junior engineer suggests disabling all security policies for one hour. What should we do?'
Evidence: IPsec tunnel shows stable phase 1/2; route reachability works; packet capture has not been taken; MSS clamp is 1460 despite tunnel overhead; old config backup exists. The application is a finance upload system.
Tasks: propose a time-boxed evidence and decision plan with exact safe checks, rank hypotheses, and describe criteria for reverting the change. Explain why bypassing security policy is inappropriate and what should be documented in the ticket.''',
        'checks': ['Recognises possible MTU/MSS PMTUD issue', 'Recommends time-boxed targeted checks over broad policy disable', 'Includes rollback if not verified before window', 'Avoids claiming unobserved packet drops', 'Records change/audit and customer impact']
    },
    {
        'category': 'Sales', 'title': 'MSSP proposal with capacity and margin assumptions',
        'prompt': '''Prepare a decision memo for an MSSP considering a 24x7 managed monitoring customer. Assumptions (illustrative): 420 network devices, 3 alerts/device/day, 8% actionable, 12 minutes manual triage per actionable alert, automation reduces actionable-alert handling time by 60%, staff productive availability 135 hours/month per FTE. Coverage is 24x7 but FTE here represents productive triage hours only. Existing tooling cost and shift coverage overhead are unknown.
Tasks: calculate monthly raw/actionable alerts using 30-day month, manual and post-automation triage hours, estimated productive triage FTE for each; make a scoped recommendation, identify uncosted staffing requirements, risks and assumptions; give an executive-ready conclusion without fabricating price or margin.''',
        'checks': ['Calculates 37,800 raw and 3,024 actionable alerts/month', 'Calculates 604.8 manual triage hours', 'Calculates 241.92 automated triage hours', 'Computes about 4.48 vs 1.79 productive triage FTE', 'States 24x7 cover and tooling costs are excluded']
    },
    {
        'category': 'Sales', 'title': 'RFP: SLA tension versus real incident process',
        'prompt': '''You are drafting an MSSP RFP response. Prospect asks for '100% detection of all incidents and 5-minute resolution for every critical alert'. Proposed service uses SolarWinds, PRTG, ServiceNow, n8n, local AI triage and human review. Operational targets are 5-minute alert acknowledgement at P1, 15-minute initial engineer action and a case-dependent restoration plan. Synthetic example: primary WAN fibre cut needing telecom carrier repair.
Tasks: write a polished but truthful RFP answer that distinguishes detection, acknowledgement, action and resolution; propose measurable SLAs/SLOs, exclusions and dependencies; describe human oversight/AI controls; give a concise example using the fibre cut; avoid guaranteeing universal detection or resolution.''',
        'checks': ['Rejects impossible 100% detection and 5-minute resolution', 'Defines measurable acknowledgement and response milestones', 'Explains external carrier repair dependency', 'Includes human oversight for AI output', 'Makes realistic contractual distinctions']
    },
    {
        'category': 'Sales', 'title': 'Customer-facing SOC incident pricing model',
        'prompt': '''A prospect has 2,000 endpoints, 180 servers and 8 firewalls across 6 sites. SIEM ingest is estimated 140 GB/day, retention 90 days hot plus 365 days archive. Estimated alerts: 200/day, 15% need investigation, 12 minutes per investigation. A security review adds an average 8 hours/week. Staffing cost, ingestion unit price and cloud storage charges have not been provided.
Tasks: derive daily investigation count, monthly investigation hours (30 days), monthly review hours (4.33 weeks), monthly approximate raw ingest and 90-day hot retention capacity before indexing overhead. Propose a pricing spreadsheet structure with variables, assumptions, scaling thresholds and commercial risks; explicitly decline to give a dollar or pound price without rates.''',
        'checks': ['Calculates 30 investigations/day and 180 hours/month', 'Estimates 34.64 review hours/month', 'Calculates approx 4.2 TB monthly ingest, 12.6 TB 90-day raw', 'Notes overhead and compression not included', 'Does not fabricate price or margin']
    },
    {
        'category': 'Sales', 'title': 'Executive QBR: mixed service outcomes',
        'prompt': '''Create an executive-ready quarterly business review for a fictitious customer. Quarter 1 vs Quarter 2: monitored devices 900→1,050, critical alerts 610→840, repeat incident rate 14%→9%, median acknowledgement 7.5→4.2 minutes, major outages 6→4, customer CSAT 4.1→4.4/5. An automation pilot handled 320 interface alerts but 37 needed human rework. One P1 incident breached SLA due to stale device ownership data. The customer wants to expand to hardware investigation next quarter.
Tasks: calculate relevant percentage changes and rework rate; tell a balanced outcome story that includes the SLA miss and rising alert load; prioritise a realistic next-quarter action plan with owners and success criteria; avoid attributing all improvements to automation without evidence.''',
        'checks': ['Calculates device +16.7% and critical alerts +37.7%', 'Calculates repeat rate drop 5 percentage points', 'Calculates rework 11.56%', 'Mentions P1 SLA miss and ownership correction', 'Does not claim causal certainty for automation']
    },
    {
        'category': 'Sales', 'title': 'Objection handling: private AI governance',
        'prompt': '''A regulated financial-services prospect is evaluating an MSSP proposal. The proposed network-alert assistant runs n8n in a dedicated automation zone and a self-hosted LLM behind a LiteLLM gateway in an AI zone; external ServiceNow and Slack webhooks go through a DMZ API proxy. The prospect asks whether customer data can leak to external models, whether automation can change firewalls, whether AI decisions are auditable, and whether the proof-of-concept’s accuracy is guaranteed. You have no confirmed policy for retention, vendor processing or disaster recovery.
Tasks: prepare an honest security questionnaire response with a control-to-risk mapping, implementation evidence still required, change-approval gates, audit/data-handling controls, segregation assumptions and a limited-scope pilot plan. Explicitly identify unanswered assurance questions instead of inventing certifications.''',
        'checks': ['Explains segmentation, gateway routing and egress control as conditional designs', 'Avoids guarantee of no leaks absent verification', 'Includes workflow approvals for network changes', 'Calls out retention, vendor/DR unknowns', 'Proposes reviewable limited-scope pilot metrics']
    },
]

assert len(DEEP_CASES) == 20
assert all(sum(1 for c in DEEP_CASES if c['category'] == category) == 5 for category in ['Interface', 'SIEM', 'Chat', 'Sales'])
