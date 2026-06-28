# new-hive — Plugin Catalog (derived from Cortex Analyzers & Responders)

> The full list of what Cortex ships, so we know which **enrichment plugins** (and later **action/
> responder plugins**) new-hive needs. Enumerated directly from
> [`Cortex-Analyzers`](https://github.com/TheHive-Project/Cortex-Analyzers) flavor JSONs (each
> declares `name`, `dataTypeList`, `description`) — not the marketing page. Pairs with the plugin
> architecture in [`newhive-enrichment-plugins.md`](./newhive-enrichment-plugins.md).
>
> **Totals (current repo):** **155 analyzer integrations / 274 flavors** · **48 responder
> integrations / 156 flavors**. "Flavors" = variants of one integration (e.g. `VirusTotal_GetReport`
> vs `VirusTotal_Scan`). We plan at the **integration** level (one plugin per integration, flavors =
> plugin actions/params).
>
> Observable **data types** seen across analyzers: `ip, domain, fqdn, hostname, url, uri_path, hash,
> imphash, file, filename, mail, mail-subject, email, user, username, user-agent, autonomous-system,
> network, port, registry, certificate_hash, btc_address, crypto_address, ja4-fingerprint, cve/
> vulnerability, mutex, regexp, tag, other`. (Our routing maps each plugin to the types it handles.)

---

## How to read this

Each entry: **Name** (data types) — purpose. A plugin we build implements the
[enrichment contract](./newhive-enrichment-plugins.md) for those data types. Build priority is in
§Plugin-build-plan (free/local first). Some integrations span categories; listed under primary use.

---

## ANALYZERS (enrichment / "check the observable")

### 1. Threat-intelligence platforms & aggregators (multi-type lookup)
- **MISP** (ip, domain, fqdn, hash, mail, url, +) — query MISP instances for events containing an observable
- **MISPWarningLists** (domain, fqdn, hash, ip, url) — check IoCs against MISP warning-lists (false-positive filter)
- **OpenCTI** (multi) — query OpenCTI instances for an observable
- **EclecticIQ** (multi) — query EclecticIQ Intelligence Center
- **Yeti** (domain, fqdn, hash, ip, url) — fetch observable details from a YETI instance
- **SEKOIAIntelligenceCenter** (domain, fqdn, hash, ip, url) — context of an observable
- **RecordedFuture** (domain, fqdn, hash, ip, url) — risk score, AI insights, links
- **GoogleThreatIntelligence** (domain, file, fqdn, hash, ip, url) — latest GTI report
- **IBMXForce** (domain, hash, ip, url) — IBM X-Force threat intel
- **Autofocus** (Palo Alto; hash/domain/ip/url/imphash/mutex/+) — full sample analysis
- **Cluster25** (domain, file, hash, ip, mail, url) — Cluster25 CTI
- **FireEyeiSight** (domain, hash, ip, url) — FireEye iSIGHT intel
- **KasperskyTIP** (domain, hash, ip) — Kaspersky Threat Intelligence Portal
- **Maltiverse** (domain, hash, ip, url) — Maltiverse report
- **Pulsedive** (domain, hash, ip, url) — Pulsedive lookup
- **ThreatResponse** (domain, filename, fqdn, hash, ip, url) — Threat Response
- **SoltraEdge** (multi) — query Soltra Edge
- **StaxxSearch** (Anomali STAXX; domain, fqdn, hash, ip, mail, url) — observable details
- **Gatewatcher_CTI** (domain, fqdn, hash, ip, mail, url) — Gatewatcher CTI report
- **C1fApp** (domain, fqdn, ip, url) — C1fApp OSINT aggregator
- **OTXQuery** (AlienVault OTX; domain, file, hash, ip, url) — OTX pulses
- **Cyberprotect** (domain, hash, ip, url, user-agent) — ThreatScore
- **Onyphe** (autonomous-system, domain, fqdn, hash, ip, other) — ONYPHE riskscan / attack surface
- **Axur** (domain, fqdn, hash, ip, url) — search axur.com
- **CyberCrime-Tracker** (domain, fqdn, ip, other, url) — C2 server search
- **isMalicious** (domain, fqdn, ip) — isMalicious.com risk score
- **Maltiverse / Pulsedive / etc.** — (see above)

### 2. IP & network reputation / geolocation / blocklists
- **MaxMind** (ip) — **geolocate an IP (local DB)** ← our `geoip2` example
- **IPinfo** (ip) — IPinfo details lookup
- **IP-API** (domain, ip) — ip-api.com lookup
- **IPVoid** (ip) — IPVoid feeds
- **AbuseIPDB** (ip) — abuse confidence score / report categories
- **DShield** (ip) — SANS ISC DShield reputation
- **FireHOLBlocklists** (ip) — FireHOL blocklists
- **BackscatterIO** (autonomous-system, ip, network, port) — Backscatter.io enrich
- **Crowdsec** (ip) — Crowdsec API
- **GreyNoise** (ip) — known scanning activity
- **StopForumSpam** (ip, mail) — known spammer check
- **TorProject** (ip) — Tor exit-node check
- **TorBlutmagie** (domain, fqdn, ip) — Tor exit list
- **SinkDB** (domain, fqdn, ip, mail) — is it sinkholed (abuse.ch)
- **DNSSinkhole** (domain) — sinkhole check
- **NERD** (ip) — Network Entity Reputation DB
- **ClusterHawk** (ip) — IP threat-intel via pre-trained models
- **LupovisProwl** (ip) — Lupovis Prowl IP reputation
- **StamusNetworks** (ip) — Scirius Security Platform info
- **Abuse_Finder** (domain, fqdn, ip, mail, url) — find abuse contacts
- **ChainAbuse** (btc_address, crypto_address) — crypto-address abuse reports
- **FoxIO** (ja4-fingerprint, user-agent) — JA4 fingerprint analysis

### 3. Scanning & attack surface
- **Shodan** (domain, fqdn, ip, other) — Shodan host/domain data
- **Censys** (domain, hash, ip) — censys.io
- **IVRE** (asn, certificate_hash, domain, fqdn, ip, network, port, user-agent) — IVRE instance
- **ONYPHEActiveScan** (domain, fqdn, ip) — on-demand active scan
- **Patrowl** (domain, fqdn, ip) — Patrowl report
- **Nessus** (fqdn, ip) — Nessus Professional host scan
- **GRR** (fqdn, ip) — GRR host agent search

### 4. DNS, passive DNS, domains & certificates
- **DNSDB** (domain, fqdn, ip) — Farsight passive DNS history
- **CIRCLPassiveDNS** (domain, ip, url) — CIRCL passive DNS
- **CIRCLPassiveSSL** (certificate_hash, hash, ip) — CIRCL passive SSL
- **CERTatPassiveDNS** (domain, fqdn, ip) — CERT.at passive DNS
- **MnemonicPDNS** (domain, ip) — Mnemonic passive DNS
- **Robtex** (domain, fqdn, ip) — Robtex passive DNS
- **SecurityTrails** (domain, ip) — passive DNS lookup
- **PassiveTotal** (domain, fqdn, hash, ip) — RiskIQ PassiveTotal components
- **RiskIQ** (domain, fqdn, ip) — OSINT articles referencing an indicator
- **GoogleDNS** (domain, fqdn, ip) — Google DoH
- **DNSLookingglass** (domain, fqdn) — SANS ISC DNS lookingglass
- **DNSdumpster** (domain) — DNSdumpster
- **Crtsh** (domain) — crt.sh certificate transparency
- **CiscoUmbrella** (domain, fqdn) — Umbrella recent DNS queries
- **DomainTools** (domain, fqdn, ip, mail, other) — historical WHOIS/NS/IP
- **DomainToolsIris** (domain, hash, ip, mail) — DomainTools Iris
- **Investigate** (Cisco OpenDNS; domain, fqdn, hash) — categorization & security
- **Hunterio** (domain, fqdn) — find emails from a domain
- **Watcher** (domain) — domain monitoring
- **OvhCloud** (domain, fqdn, url) — domain availability
- **SpamhausDBL** (domain, fqdn) — Spamhaus domain blocklist
- **WOT** (domain, fqdn) — Web of Trust reputation
- **ForcepointWebsensePing** (domain, fqdn, ip, url) — URL category
- **AILOnionLookup** (domain, fqdn, url) — Tor hidden-service metadata
- **Zscaler** (domain, fqdn, ip, url) — Zscaler URL category
- **GoogleSafebrowsing** (domain, url) — Google Safe Browsing

### 5. Malware sandboxes (dynamic analysis)
- **CuckooSandbox** (file, url) · **JoeSandbox** (file, url) · **AnyRun** (domain, file, hash, ip, url) ·
  **VMRay** (file, hash, url) · **Triage** (Recorded Future; file, ip, url) · **ThreatGrid** (file, hash, url) ·
  **PayloadSecurity** (file, url) · **PaloAltoWildFire** (file, hash, url) · **IntezerCommunity** (file, hash) ·
  **HybridAnalysis** (domain, file, filename, hash, url) · **CISMCAP** (CIS MCAP; file/hash/url/+) ·
  **MetaDefender** (domain, file, hash, ip, url) · **SophosIntelix** (domain, file, fqdn, hash, url)

### 6. File & static analysis / YARA
- **FileInfo** (file) — OLE/OpenXML parse, VBA macro extract, hashes
- **Capa** (file) — capability detection
- **ClamAV** (file) — clamscan w/ custom rules
- **Yara** (file) — YARA rules (local / GitHub repos)
- **Valhalla** (hash) — matching YARA rules for a SHA256
- **Malpedia** (file) — Malpedia YARA
- **Thunderstorm** (file) — THOR Thunderstorm scan
- **QrDecode** (file) — extract data from QR codes
- **MalwareClustering** (file, hash) — ApiVector similarity
- **CyberChef** (other) — CyberChef server transforms (e.g. Base64)
- **OrionMalware** (file, hash) — OrionMalware analysis
- **SpamAssassin** (file) — spam score (local)
- **GoogleVisionAPI** (file, url) — look-alike image detection

### 7. Hash reputation & lookup
- **VirusTotal** (domain, file, fqdn, hash, ip, url) — VT reports (the flagship)
- **MalwareBazaar** (hash) — abuse.ch MalwareBazaar
- **Virusshare** (file, hash) — Virusshare hash list
- **CIRCLHashlookup** (hash) — known-good/bad hash DB
- **TeamCymruMHR** (hash) — Team Cymru Malware Hash Registry
- **Hashdd** (hash) — good/bad hash
- **NSRL** (filename, hash) — NIST NSRL known-files
- **EchoTrail** (filename, hash) — Windows filename/hash insights
- **Cylance** (hash) — Cylance hash match
- **Proofpoint** (file, hash, url) — Proofpoint forensics
- **Malwares** (domain, file, hash, ip) — Malwares.com report

### 8. URL & phishing / web inspection
- **Urlscan.io** (domain, fqdn, hash, ip, url) · **urlDNA.io** (domain, ip, url) — scan a URL
- **PhishingInitiative** (url) · **PhishTank** (url) · **CheckPhish** (string, url) — verified phishing
- **UnshortenLink** (url) — reveal real URL
- **Lookyloo** (domain, fqdn, ip, url) — screenshot + HTTP redirects
- **URLhaus** (domain, fqdn, hash, ip, url) — abuse.ch URLhaus
- **MSDefenderOffice365** (url) — decode O365 ATP Safe Links
- **GoogleSafebrowsing** — (see §4)

### 9. Email, identity & account intel
- **EmlParser** (file) — parse/visualise .eml
- **MsgParser** (file) — parse Outlook .msg
- **DomainMailSPFDMARC** (domain, file, fqdn) — SPF/DMARC checks
- **EmailRep** (mail) — emailrep.io
- **HIBP** (mail) — haveibeenpwned breach check
- **Inoitsu** (mail) — compromised email check
- **Verifalia** (mail) — email validation
- **Okta** (mail) — Okta user lookup
- **MSEntraID** (hostname, mail, user, username) — Entra ID audit logs / user
- **LdapQuery** (mail, username) — LDAP user harvest
- **CheckPointHEC** (domain, file, ip, mail, url) — Check Point Harmony Email search
- **Hunterio** — (see §4)

### 10. SIEM / EDR / internal data query
- **Splunk** (multi) — run a savedsearch on Splunk
- **Elasticsearch** (multi) — search IoCs in Elasticsearch
- **CrowdstrikeFalcon** (domain, file, hash, hostname, ip, url) — device/IOC context
- **SentinelOne** (domain, fqdn, ip, url) — host DNS-lookup history
- **Jupyter** (multi) — execute a parameterized notebook
- **Cylance** — (see §7)

### 11. Vulnerability / CVE
- **Vulners** (cve, domain, ip, url) — Vulners CVE database
- **CIRCLVulnerabilityLookup** (cve, vulnerability, +) — CIRCL vuln lookup
- **EmergingThreats** (domain, file, fqdn, hash, ip) — ET reputation + related malware/IDS

### 12. Utility / validation
- **ValidateObservable** (multi) — regex/library validity check
- **TestAnalyzer** (multi) — echo input (dev/test)
- **CyberChef** — (see §6)

### Deprecated / empty in the current repo (0 flavors — note, don't build)
Diario, FalconSandbox, Fortiguard, Hippocampe, TalosReputation, Threatcrowd.

---

## RESPONDERS (actions — our future "action plugin" type)

Responders act on a TheHive **case / alert / observable / task** (or a few on `domain/url`). Grouped
by what they do. (`thehive:case_artifact` = an observable.)

### A. Block / contain / deny-list (firewall, proxy, DNS, EDR)
- **PaloAltoNGFW** (case/alert/artifact) — block external IP
- **PaloAltoCortexXDR** (artifact) — add hash to XDR allow/deny list
- **Zscaler** (artifact) — block domains/FQDNs/URLs in ZIA denylist
- **CheckPoint** (artifact) — lock IP on CheckPoint Gaia
- **Cloudflare** (artifact) — block IP at account level
- **CiscoUmbrella** (artifact) — add domain to Umbrella blacklist
- **DNS-RPZ** (artifact) — blackhole/redirect a FQDN via Response Policy Zone
- **Minemeld** (artifact) — submit indicator to Minemeld
- **SentinelOne** (artifact) — add SHA1 to blacklist
- **CrowdstrikeFalcon** (case/alert/artifact) — add IOC to Falcon IoC management
- **MSDefenderEndpoints** (artifact) — automated investigation / contain device
- **MSDefenderOffice365** (artifact) — tenant allow/block list
- **JAMFProtect** (artifact) — custom prevent-list for a hash
- **Wazuh** (case/artifact) — block IP on a host via Wazuh agent
- **AMPforEndpoints** (artifact) — Cisco AMP host isolation
- **FalconCustomIOC** (0 flavors — empty)

### B. Endpoint forensics / collection (DFIR)
- **HarfangLab** (case/alert/artifact, 31 flavors) — dump process memory / isolate / collect
- **BinalyzeAIR** (artifact) — start an acquisition
- **Velociraptor** (artifact) — run an artifact collection

### C. Intel submission / enrichment-back
- **AbuseIPDB** (artifact) — report an IP to AbuseIPDB
- **EclecticIQ** (case/artifact) — submit indicators to the IC
- **RiskIQ** (artifact) — push a case to a RiskIQ project
- **DomainToolsIris** (artifact) — tag risky DNS
- **PaloAltoWildFire** (domain/fqdn/url) — submit URL to WildFire
- **Netcraft** (artifact) — submit URL to Netcraft takedown

### D. Identity / account actions
- **MSEntraID** (artifact) — force password reset at next login
- **Duo_Security** (artifact) — put user into bypass mode
- **KnowBe4** (artifact) — add "clicked event" to a user

### E. Notify / ticket / SOAR-orchestrate
- **Mailer** (case/alert/task) · **SendGrid** (case/alert) — email case/alert info
- **MailIncidentStatus** (case) — mail incident status to tagged recipients
- **Slack** (case) — create a Slack channel for a case
- **Telegram** (case) — send a Telegram message
- **Redmine** (case/task) · **RT4** (case/alert/artifact) · **IBMQRadar** (case, close offense) — ticketing
- **Shuffle** / **n8n** / **AWSLambda** / **Jupyter** (case/alert/artifact/task/log) — SOAR / serverless / notebooks
- **ZEROFOX** (case) — close a ZeroFox alert
- **Gatewatcher_CTI** (case) · **OvhCloud** (artifact) · **Watcher** (artifact)
- **Test** (echo, dev)

### Deprecated / empty responders (0 flavors)
FalconCustomIOC, VirustotalDownloader.

---

## Plugin-build plan (which plugins, in what order)

We do **not** reimplement 155 integrations up front. Strategy:

1. **Ship the Cortex bridge first (covers the whole catalog cheaply).** The built-in **`cortex-proxy`
   plugin** + **cortexutils-compatible runner** (see [`newhive-enrichment-plugins.md`](./newhive-enrichment-plugins.md) §11)
   let *every* existing analyzer/responder run against new-hive on day one. This is the single
   highest-leverage item.
2. **Then build native plugins in priority tiers** (native = no Cortex needed, lighter, fully audited):

| Tier | Why first | Native plugins to build |
|---|---|---|
| **T0 — free / local / keyless** | zero cost, instant value, no egress concerns | **MaxMind/geoip2** (local), **MISP**, **URLhaus**, **MalwareBazaar**, **GreyNoise**, **AbuseIPDB**, **TorProject**, **Crtsh**, **GoogleSafebrowsing/GoogleDNS**, **DShield**, **CIRCLHashlookup/CIRCLPassiveDNS**, **PhishTank**, **FileInfo/EmlParser/MsgParser** (local parsers), **Yara/ClamAV** (local), **ValidateObservable** (local) |
| **T1 — common commercial API (single key)** | most-used enrichment | **VirusTotal**, **Shodan**, **Censys**, **IBMXForce**, **RecordedFuture**, **Pulsedive**, **urlscan.io**, **SecurityTrails**, **DomainTools**, **HybridAnalysis/JoeSandbox/AnyRun** (sandbox), **OTX** |
| **T2 — enterprise EDR / SIEM / sandbox** | your-own-tools integrations | **CrowdstrikeFalcon**, **SentinelOne**, **Splunk**, **Elasticsearch**, **PaloAltoWildFire**, **VMRay**, **MSDefender***, **MSEntraID** |
| **T3 — responders (action plugins)** | containment + workflow | block/contain (**PaloAltoNGFW**, **CrowdstrikeFalcon**, **Zscaler**, **CheckPoint**, **Cloudflare**, **DNS-RPZ**), DFIR (**Velociraptor**, **HarfangLab**, **BinalyzeAIR**), notify/SOAR (**Mailer/SendGrid**, **Slack**, **Shuffle/n8n/AWSLambda**), ticketing (**Redmine/RT4/QRadar**) |

3. **Map each to the contract:** the catalog's `dataTypeList` → the plugin manifest `data_types`;
   the integration's endpoint → `egress_allow`; its API key → `secrets`; its sensitivity → `max_tlp`
   (local plugins like geoip2 = tlp 3; external APIs default tlp 2). Multiple Cortex *flavors* of one
   integration become *actions/params* of one plugin.

**Coverage note:** the long tail (the ~120 niche integrations) stays available via the Cortex bridge;
we promote an integration to a native plugin when demand or the audit/security benefit justifies it.
Don't silently drop the tail — the bridge is what keeps the ecosystem usable.

---

## Cross-references
- Plugin architecture & contract: [`newhive-enrichment-plugins.md`](./newhive-enrichment-plugins.md)
- Cortex mechanics (job runner, TLP/PAP, ActionOperations): [`cortex.md`](./cortex.md)
- MISP specifics: [`thehive-misp-connector.md`](./thehive-misp-connector.md)
- Permissions to run/manage: [`newhive-rbac-design.md`](./newhive-rbac-design.md) (`run:analyzers`, `manage:plugins`)
- Build home: [`thehive5-implementation-plan.md`](./thehive5-implementation-plan.md) (M7)
- Source: [Cortex-Analyzers](https://github.com/TheHive-Project/Cortex-Analyzers), [Cortex](https://github.com/TheHive-Project/Cortex), [StrangeBee feature page](https://strangebee.com/product-feature/cortex-analyzers-responders/)
