from datetime import datetime, timedelta
from uuid import UUID

from app.models.alert import AlertCreate
from app.models.knowledge_base import KnowledgeBasePageCreate
from app.models.task import TaskCreate, TaskStatus


def demo_alert_specs(now: datetime) -> list[AlertCreate]:
    return [
        AlertCreate(
            type="edr",
            source="CrowdStrike",
            source_ref="AL-9123",
            title="Possible ransomware staging — mass file rename on FILESRV-AU02",
            description="CrowdStrike detected >4,000 file renames with appended extension .0rgn on FILESRV-AU02 within 90 seconds, initiated by svchost.exe spawned from an unsigned binary in C:\\PerfLogs\\. Shadow copies deletion attempted (blocked).",
            severity=4,
            tlp=3,
            date=now - timedelta(minutes=14),
        ),
        AlertCreate(
            type="identity",
            source="Defender XDR",
            source_ref="AL-9119",
            title="OAuth consent grant to unverified app for 3 privileged users",
            description="Defender XDR raised this alert after an unverified multi-tenant application was granted Mail.ReadWrite and offline_access by three privileged users within 11 minutes.",
            severity=4,
            tlp=2,
            date=now - timedelta(minutes=42),
        ),
        AlertCreate(
            type="identity",
            source="Defender XDR",
            source_ref="AL-9111",
            title="Impossible travel: AU → RO sign-in, MFA fatigue pattern",
            description="Microsoft sign-in telemetry shows a successful AU login followed by repeated MFA prompts and a Romania login attempt from a new device fingerprint.",
            severity=3,
            tlp=2,
            date=now - timedelta(minutes=67),
        ),
        AlertCreate(
            type="network",
            source="Splunk ES",
            source_ref="AL-9108",
            title="Outbound beaconing to rare domain from OT jump host",
            description="Splunk ES detected periodic outbound TLS traffic from the OT jump host to a rare domain with no prior enterprise reputation.",
            severity=3,
            tlp=2,
            date=now - timedelta(minutes=88),
        ),
        AlertCreate(
            type="phishing",
            source="Proofpoint",
            source_ref="AL-9102",
            title="Credential-phish campaign targeting retail billing team (38 rcpts)",
            description="Proofpoint identified a credential-harvesting campaign impersonating the retail billing portal and delivered to 38 recipients.",
            severity=3,
            tlp=1,
            date=now - timedelta(minutes=121),
        ),
        AlertCreate(
            type="endpoint",
            source="Splunk ES",
            source_ref="AL-9097",
            title="New local admin created outside change window — WKS-4471",
            description="A local administrator account was created on WKS-4471 outside the approved change window and without a matching service desk ticket.",
            severity=2,
            tlp=1,
            date=now - timedelta(minutes=163),
        ),
        AlertCreate(
            type="intel",
            source="MISP",
            source_ref="AL-9093",
            title="MISP IOC match: known C2 IP touched perimeter (blocked)",
            description="MISP matched a known command-and-control IP in blocked perimeter traffic. No successful session was observed.",
            severity=2,
            tlp=2,
            date=now - timedelta(minutes=201),
        ),
        AlertCreate(
            type="dns",
            source="Splunk ES",
            source_ref="AL-9090",
            title="Unusual volume of DNS TXT queries from CI runner pool",
            description="DNS telemetry shows elevated TXT query volume from CI runners, including repeated lookups for encoded subdomains.",
            severity=2,
            tlp=0,
            date=now - timedelta(minutes=240),
        ),
        AlertCreate(
            type="hygiene",
            source="CrowdStrike",
            source_ref="AL-9084",
            title="EDR sensor offline > 72h on 6 corporate laptops",
            description="CrowdStrike reports that six corporate laptops have not checked in for more than 72 hours and are missing current sensor health data.",
            severity=1,
            tlp=1,
            date=now - timedelta(minutes=355),
        ),
        AlertCreate(
            type="hygiene",
            source="Splunk ES",
            source_ref="AL-9080",
            title="TLS certificate for partner API expires in 9 days",
            description="Certificate monitoring reports the partner API TLS certificate will expire in 9 days without a replacement certificate observed.",
            severity=1,
            tlp=0,
            date=now - timedelta(minutes=402),
        ),
    ]


def demo_alert_tags() -> dict[str, list[str]]:
    return {
        "AL-9123": ["ransomware", "T1486", "finance"],
        "AL-9119": ["identity", "oauth", "privileged-access"],
        "AL-9111": ["identity", "mfa-fatigue", "impossible-travel"],
        "AL-9108": ["ot", "beaconing", "network"],
        "AL-9102": ["phishing", "retail", "credential-theft"],
        "AL-9097": ["endpoint", "local-admin", "change-control"],
        "AL-9093": ["misp", "c2", "blocked"],
        "AL-9090": ["dns", "ci-runner", "exfiltration"],
        "AL-9084": ["endpoint", "sensor-health"],
        "AL-9080": ["certificate", "partner-api", "hygiene"],
    }


def demo_knowledge_base_pages() -> list[KnowledgeBasePageCreate]:
    return [
        KnowledgeBasePageCreate(
            title="Phishing response runbook",
            summary="Standard operating procedure for credential-harvesting phishing reported via the abuse mailbox or detected by Proofpoint TAP.",
            tags=["runbook", "phishing"],
            content=(
                "## 1 · Triage\n\n"
                "- Pull the raw .eml via M365 message trace.\n"
                "- Capture headers, body, attachments and links.\n"
                "- Confirm the lure is real and in-scope.\n\n"
                "## 2 · Scope\n\n"
                "- Query mail flow for all recipients.\n"
                "- Correlate proxy/URL telemetry for clickers.\n"
                "- Identify any credential submissions.\n\n"
                "## 3 · Contain\n\n"
                "- Block sender, domain and landing URLs.\n"
                "- Reset credentials + revoke sessions for submitters.\n"
                "- Purge the message from mailboxes.\n\n"
                "Apply the case template to auto-create these tasks.\n\n"
                "```\nPhishing / credential harvesting\n```"
            ),
        ),
        KnowledgeBasePageCreate(
            title="BEC investigation guide",
            summary="For confirmed or suspected business email compromise — mailbox rule abuse, payment redirection, executive impersonation.",
            tags=["runbook", "bec", "finance"],
            content=(
                "## Immediate actions\n\n"
                "- Audit inbox rules and forwarding on impacted mailboxes.\n"
                "- Revoke sessions and reset credentials.\n"
                "- Notify finance to freeze at-risk payments.\n\n"
                "## Evidence to preserve\n\n"
                "- Unified audit log for the access window.\n"
                "- Rule definitions (as added evidence).\n"
                "- Invoice + payment activity."
            ),
        ),
        KnowledgeBasePageCreate(
            title="TLP & PAP handling policy",
            summary="Traffic Light Protocol governs who may see an artifact; Permissible Actions Protocol governs what may be done with it.",
            tags=["policy"],
            content=(
                "- WHITE (0) — unrestricted.\n"
                "- GREEN (1) — community.\n"
                "- AMBER (2) — limited distribution (default).\n"
                "- RED (3) — named recipients only.\n\n"
                "Analyzers and responders must respect an observable's PAP — egress is blocked when an action would exceed the permitted level."
            ),
        ),
        KnowledgeBasePageCreate(
            title="Analyst onboarding checklist",
            summary="",
            tags=["onboarding"],
            content=(
                "- Account provisioned with the analyst profile.\n"
                "- MFA enrolled (phishing-resistant).\n"
                "- Read the phishing, BEC and malware runbooks.\n"
                "- Shadow a shift on the triage queue.\n"
                "- Complete a supervised case end-to-end."
            ),
        ),
    ]


def oauth_demo_tasks(now: datetime, analyst_id: UUID) -> list[tuple[TaskCreate, TaskStatus]]:
    return [
        (
            TaskCreate(
                title="Remove mailbox rules and check forwarding",
                group="Eradicate",
                description="OAuth consent grant — privileged account compromise",
                assignee_id=analyst_id,
                order=5,
                due_date=now + timedelta(hours=2),
            ),
            TaskStatus.in_progress,
        ),
        (
            TaskCreate(
                title="User comms + phishing-resistant MFA enrolment",
                group="Recover",
                description="OAuth consent grant — privileged account compromise",
                order=6,
                due_date=now + timedelta(days=2),
            ),
            TaskStatus.waiting,
        ),
        (
            TaskCreate(
                title="Publish executive situation summary",
                group="Comms",
                description="OAuth consent grant — privileged account compromise",
                assignee_id=analyst_id,
                order=7,
                due_date=now + timedelta(hours=5),
            ),
            TaskStatus.waiting,
        ),
        (
            TaskCreate(
                title="Collect SaaS audit logs for legal hold",
                group="Evidence",
                description="OAuth consent grant — privileged account compromise",
                assignee_id=analyst_id,
                order=8,
                due_date=now + timedelta(days=3),
            ),
            TaskStatus.waiting,
        ),
        (
            TaskCreate(
                title="Archive mailbox investigation artefacts",
                group="Closeout",
                description="OAuth consent grant — privileged account compromise",
                assignee_id=analyst_id,
                order=9,
                due_date=now + timedelta(days=4),
            ),
            TaskStatus.waiting,
        ),
        (
            TaskCreate(
                title="Check privileged inbox delegates",
                group="Review",
                description="OAuth consent grant — privileged account compromise",
                assignee_id=analyst_id,
                order=10,
                due_date=now + timedelta(days=4, hours=4),
            ),
            TaskStatus.waiting,
        ),
    ]


def ransomware_demo_tasks(
    now: datetime, analyst_id: UUID
) -> list[tuple[TaskCreate, TaskStatus]]:
    return [
        (
            TaskCreate(
                title="Identify ransomware family and variant",
                group="Identify",
                description="Ransomware precursor activity — lateral movement detected",
                assignee_id=analyst_id,
                order=4,
                due_date=now + timedelta(hours=1),
            ),
            TaskStatus.in_progress,
        ),
        (
            TaskCreate(
                title="Snapshot affected hypervisor cluster",
                group="Contain",
                description="Ransomware precursor activity — lateral movement detected",
                assignee_id=analyst_id,
                order=5,
                due_date=now + timedelta(hours=3),
            ),
            TaskStatus.waiting,
        ),
        (
            TaskCreate(
                title="Pull EDR timeline for initial access",
                group="Forensics",
                description="Ransomware precursor activity — lateral movement detected",
                assignee_id=analyst_id,
                order=6,
                due_date=now + timedelta(days=1),
            ),
            TaskStatus.in_progress,
        ),
        (
            TaskCreate(
                title="Confirm backups are clean and restorable",
                group="Recover",
                description="Ransomware precursor activity — lateral movement detected",
                assignee_id=analyst_id,
                order=7,
                due_date=now + timedelta(days=1, hours=5),
            ),
            TaskStatus.waiting,
        ),
    ]


def data_exfiltration_demo_tasks(
    now: datetime, analyst_id: UUID
) -> list[tuple[TaskCreate, TaskStatus]]:
    return [
        (
            TaskCreate(
                title="Prepare customer impact statement",
                group="Comms",
                description="Data exfiltration via unapproved SaaS application",
                assignee_id=analyst_id,
                order=5,
                due_date=now + timedelta(days=2, hours=2),
            ),
            TaskStatus.waiting,
        ),
    ]
