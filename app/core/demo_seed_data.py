from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from app.models.alert import AlertCreate
from app.models.case_ import CaseResolutionStatus
from app.models.knowledge_base import KnowledgeBasePageCreate
from app.models.sla import SlaPolicyUpsert
from app.models.task import TaskCreate, TaskStatus

# Source refs of the alerts that seed each demo case. The local seeder ingests
# these alerts, then promotes them into the matching case (status -> Imported,
# case_id set) so every demo case has a real originating alert in its "Linked
# alerts" panel. Keep these in sync with the AlertCreate specs below.
OAUTH_CASE_ALERT_REF = "AL-9119"
RANSOMWARE_CASE_ALERT_REF = "AL-9123"
EXFILTRATION_CASE_ALERT_REF = "AL-9088"


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
        # Originating alert for the "Data exfiltration via unapproved SaaS" case.
        AlertCreate(
            type="casb",
            source="Netskope",
            source_ref=EXFILTRATION_CASE_ALERT_REF,
            title="Bulk OneDrive download to unapproved file-sharing service",
            description="Netskope CASB flagged ~4.2 GB moved from OneDrive to tempfileshare[.]io over a 45-minute window by marcus.johnson@example.com. Files carried 'PII' and 'Financial' sensitivity labels; transfer occurred from an unmanaged device.",
            severity=3,
            tlp=2,
            date=now - timedelta(hours=8, minutes=15),
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
        "AL-9088": ["casb", "exfiltration", "data-loss", "T1567.002"],
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


# ---------------------------------------------------------------------------
# Dashboard-focused seed data
#
# The three narrative cases above give the case-detail views something rich to
# render. The data below instead exists to make the SOC Overview dashboard
# (GET /overview) look alive on a fresh database: a full 24h of alert ingestion,
# a fortnight of opened/resolved case history, resolution dispositions, SLA
# policies (so breaches compute) and a couple of unassigned cases for the "New"
# pipeline bucket.
# ---------------------------------------------------------------------------


def demo_sla_policies() -> list[SlaPolicyUpsert]:
    """Per-severity acknowledge/resolve targets (1=low … 4=critical). These let
    the dashboard compute SLA breaches for open cases past their resolve
    target."""
    return [
        SlaPolicyUpsert(
            severity=4,
            ack_seconds=15 * 60,
            resolve_seconds=4 * 3600,
            escalation_target="On-call incident lead",
        ),
        SlaPolicyUpsert(
            severity=3,
            ack_seconds=30 * 60,
            resolve_seconds=8 * 3600,
            escalation_target="Shift lead",
        ),
        SlaPolicyUpsert(severity=2, ack_seconds=2 * 3600, resolve_seconds=24 * 3600),
        SlaPolicyUpsert(severity=1, ack_seconds=8 * 3600, resolve_seconds=72 * 3600),
    ]


def demo_recent_alerts(now: datetime) -> tuple[list[AlertCreate], dict[str, list[str]]]:
    """~24h of freshly-dated alerts across every connected feed, so the
    ingestion chart, "new alerts (24h)", severity donut and source breakdown all
    have real shape. Returns the alert specs plus their tags keyed by ref.

    Each tuple is (ref, source, type, severity, tlp, minutes_ago, title, tags)."""
    rows: list[tuple[str, str, str, int, int, int, str, list[str]]] = [
        ("AL-9201", "Defender XDR", "identity", 4, 2, 35, "Token replay from residential proxy on finance admin", ["identity", "token-theft", "T1550.001"]),
        ("AL-9202", "CrowdStrike", "edr", 3, 2, 72, "LSASS access by non-standard process on WKS-2210", ["endpoint", "credential-access", "T1003.001"]),
        ("AL-9203", "Proofpoint", "phishing", 3, 1, 108, "QR-code phish wave to logistics distro (52 rcpts)", ["phishing", "quishing", "T1566.001"]),
        ("AL-9204", "Splunk ES", "network", 2, 2, 141, "Port-scan sweep from contractor VPN pool", ["network", "recon", "T1046"]),
        ("AL-9205", "MISP", "intel", 2, 2, 190, "IOC match: phishing kit domain seen in proxy logs", ["misp", "phishing-kit", "ioc-match"]),
        ("AL-9206", "Netskope", "casb", 3, 2, 233, "Sensitive label file shared to personal Gmail", ["casb", "data-loss", "insider"]),
        ("AL-9207", "Splunk ES", "endpoint", 2, 1, 305, "Scheduled task created under public user profile", ["endpoint", "persistence", "T1053.005"]),
        ("AL-9208", "Defender XDR", "identity", 3, 2, 372, "Impossible travel: US → NG on VP mailbox", ["identity", "impossible-travel", "bec"]),
        ("AL-9209", "CrowdStrike", "edr", 4, 3, 455, "Cobalt Strike beacon signature on SRV-DB03", ["c2", "cobalt-strike", "T1071.001"]),
        ("AL-9210", "Proofpoint", "phishing", 2, 1, 540, "Callback-phish voicemail lure to HR", ["phishing", "callback", "social-engineering"]),
        ("AL-9211", "Splunk ES", "dns", 2, 0, 636, "DGA-like NXDOMAIN burst from marketing subnet", ["dns", "dga", "T1568.002"]),
        ("AL-9212", "MISP", "intel", 1, 1, 742, "New threat-actor TTP bundle ingested (no match)", ["misp", "threat-intel"]),
        ("AL-9213", "Netskope", "casb", 2, 2, 851, "Unsanctioned AI tool upload of source code", ["casb", "shadow-it", "data-loss"]),
        ("AL-9214", "Defender XDR", "endpoint", 3, 2, 968, "Suspicious signed-binary proxy exec (rundll32)", ["endpoint", "defense-evasion", "T1218.011"]),
        ("AL-9215", "Splunk ES", "network", 1, 0, 1105, "Expired internal CA certificate still in rotation", ["hygiene", "certificate"]),
        ("AL-9216", "CrowdStrike", "hygiene", 1, 1, 1290, "3 endpoints missing latest sensor policy", ["endpoint", "sensor-health", "hygiene"]),
    ]
    specs = [
        AlertCreate(
            type=type_,
            source=source,
            source_ref=ref,
            title=title,
            description=title + ".",
            severity=severity,
            tlp=tlp,
            date=now - timedelta(minutes=mins),
        )
        for ref, source, type_, severity, tlp, mins, title, _tags in rows
    ]
    tags = {row[0]: row[-1] for row in rows}
    return specs, tags


@dataclass
class ResolvedCaseSpec:
    """A historical, already-closed case — feeds the case-trend line, MTTR and
    the resolution-disposition donut."""

    title: str
    description: str
    severity: int
    resolution: CaseResolutionStatus
    created_days_ago: float
    resolved_days_ago: float
    tags: list[str]
    #: (observable_type, data, ioc) tuples surfaced in "Latest observables".
    observables: list[tuple[str, str, bool]] = field(default_factory=list)


def demo_resolved_cases() -> list[ResolvedCaseSpec]:
    """Closed cases spread across the last fortnight with mixed dispositions.
    Resolve times are staggered so both the current and prior 7-day MTTR windows
    are populated (giving the tile a delta)."""
    return [
        ResolvedCaseSpec(
            title="Phishing — payroll credential harvest (contained)",
            description="Proofpoint-detected payroll lure; 2 credential submissions reset, sessions revoked. No mailbox rule abuse found.",
            severity=3,
            resolution=CaseResolutionStatus.true_positive,
            created_days_ago=6,
            resolved_days_ago=5,
            tags=["phishing", "credential-theft", "T1566.002"],
            observables=[("domain", "payroll-login-verify.help", True)],
        ),
        ResolvedCaseSpec(
            title="Suspected malware on WKS-3389 — false alarm",
            description="EDR heuristic flagged a signed IT packaging tool. Confirmed legitimate via software inventory and vendor signature.",
            severity=2,
            resolution=CaseResolutionStatus.false_positive,
            created_days_ago=4,
            resolved_days_ago=4,
            tags=["endpoint", "false-positive"],
        ),
        ResolvedCaseSpec(
            title="Brute-force against VPN portal (blocked)",
            description="Splunk detected a distributed password-spray against the SSL-VPN. Source ASNs blocked; no successful auth. Conditional access held.",
            severity=3,
            resolution=CaseResolutionStatus.true_positive,
            created_days_ago=9,
            resolved_days_ago=6,
            tags=["identity", "brute-force", "T1110.003"],
            observables=[("ip", "185.220.101.44", True)],
        ),
        ResolvedCaseSpec(
            title="Anomalous S3 access — determined benign",
            description="GuardDuty flagged cross-region S3 GetObject from a new role. Traced to an approved data-migration job.",
            severity=2,
            resolution=CaseResolutionStatus.indeterminate,
            created_days_ago=11,
            resolved_days_ago=9,
            tags=["cloud", "aws", "data-access"],
        ),
        ResolvedCaseSpec(
            title="Duplicate of ransomware precursor triage",
            description="Second CrowdStrike detection for the same host/session already tracked under the active ransomware case. Merged and closed.",
            severity=4,
            resolution=CaseResolutionStatus.duplicated,
            created_days_ago=3,
            resolved_days_ago=3,
            tags=["ransomware", "duplicate"],
        ),
        ResolvedCaseSpec(
            title="Malvertising redirect chain reported by user",
            description="User-reported browser redirects to a fake update page. URL categorised and blocked at proxy; endpoint scanned clean.",
            severity=2,
            resolution=CaseResolutionStatus.true_positive,
            created_days_ago=13,
            resolved_days_ago=11,
            tags=["web", "malvertising", "T1189"],
            observables=[("url", "hxxps://secure-update-cdn[.]live/patch", True)],
        ),
    ]


@dataclass
class OpenCaseSpec:
    """An open case with no assignee — lands in the dashboard's "New" pipeline
    bucket. An old, high-severity one also trips an SLA breach."""

    title: str
    description: str
    severity: int
    created_days_ago: float
    tags: list[str]
    #: Recent alert ref to promote into this case (drives the ingestion chart's
    #: "promoted" line); None to leave unlinked.
    linked_recent_ref: str | None = None


def demo_new_open_cases() -> list[OpenCaseSpec]:
    return [
        OpenCaseSpec(
            title="Cobalt Strike beacon on SRV-DB03 — awaiting owner",
            description="CrowdStrike beacon signature (AL-9209) on a production DB host. Unassigned in the queue; needs immediate pickup.",
            severity=4,
            created_days_ago=5,  # older than the 4h critical resolve target → SLA breach
            tags=["c2", "cobalt-strike", "unassigned"],
            linked_recent_ref="AL-9209",
        ),
        OpenCaseSpec(
            title="QR-code phishing wave — triage pending",
            description="Proofpoint quishing campaign (AL-9203) to the logistics team. Awaiting analyst assignment.",
            severity=3,
            created_days_ago=0.1,
            tags=["phishing", "quishing", "unassigned"],
            linked_recent_ref="AL-9203",
        ),
    ]


def demo_extra_analysts() -> list[tuple[str, str, str]]:
    """Additional analysts (email, first, last) so the workload panel shows a
    team rather than a single name."""
    return [
        ("priya.nguyen@example.com", "Priya", "Nguyen"),
        ("sam.iyer@example.com", "Sam", "Iyer"),
    ]


def _w(type_: str, size: str) -> dict[str, str]:
    return {"type": type_, "size": size}


@dataclass
class DashboardSpec:
    """A saved dashboard "view": name, sharing flag and its widget layout. Widget
    `type`s must match the frontend catalog (components/Dashboards/widgets.tsx)."""

    name: str
    description: str
    shared: bool
    widgets: list[dict[str, str]]


def demo_dashboards() -> list[DashboardSpec]:
    """Ready-made views owned by the demo analyst. The two shared ones appear
    for every org member (read-only); the private one only for the analyst — so
    a fresh DB shows off both the sharing model and personal views."""
    return [
        DashboardSpec(
            name="SOC operations",
            description="Shared operational overview for the whole team.",
            shared=True,
            widgets=[
                _w("kpi.open_cases", "sm"),
                _w("kpi.new_alerts", "sm"),
                _w("kpi.sla_breaches", "sm"),
                _w("kpi.mttr", "sm"),
                _w("chart.cases_by_status", "md"),
                _w("chart.cases_by_severity", "md"),
                _w("chart.analyst_workload", "md"),
                _w("chart.alerts_by_source", "md"),
                _w("chart.sla_compliance", "md"),
                _w("chart.cases_opened_14d", "md"),
            ],
        ),
        DashboardSpec(
            name="Alert triage",
            description="Live alert pipeline, severity mix and fresh IOCs.",
            shared=True,
            widgets=[
                _w("kpi.new_alerts", "sm"),
                _w("kpi.open_alerts", "sm"),
                _w("kpi.sla_breaches", "sm"),
                _w("kpi.iocs_tracked", "sm"),
                _w("chart.alerts_by_severity", "md"),
                _w("chart.alerts_by_source", "md"),
                _w("chart.ingestion_24h", "lg"),
                _w("list.latest_observables", "md"),
            ],
        ),
        DashboardSpec(
            name="My caseload",
            description="Private view: case pipeline and resolution quality.",
            shared=False,
            widgets=[
                _w("kpi.open_cases", "sm"),
                _w("kpi.mttr", "sm"),
                _w("chart.cases_by_status", "md"),
                _w("chart.resolutions", "md"),
                _w("chart.case_trend", "lg"),
                _w("chart.sla_compliance", "md"),
                _w("chart.analyst_workload", "md"),
            ],
        ),
    ]


def demo_admin_dashboard() -> DashboardSpec:
    """A private board for the local superadmin, so the default admin login also
    has a personal (unshared) view to see."""
    return DashboardSpec(
        name="SOC lead board",
        description="Personal executive view for the SOC lead.",
        shared=False,
        widgets=[
            _w("kpi.open_cases", "sm"),
            _w("kpi.sla_breaches", "sm"),
            _w("kpi.mttr", "sm"),
            _w("kpi.iocs_tracked", "sm"),
            _w("chart.sla_compliance", "md"),
            _w("chart.cases_by_status", "md"),
            _w("chart.case_trend", "lg"),
        ],
    )
