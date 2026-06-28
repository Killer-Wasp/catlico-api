# TheHive — Database / Data Model

> Reverse-engineered from the [`TheHive-Project/TheHive`](https://github.com/TheHive-Project/TheHive)
> Scala source (v4/v5 architecture), from the domain model under
> `thehive/app/org/thp/thehive/models/` and the Cortex connector under
> `cortex/connector/src/main/scala/org/thp/thehive/connector/cortex/models/`.

## TL;DR — it's a graph, not relational

TheHive does **not** use a relational database. It runs on a **graph database
(JanusGraph)** through an in-house abstraction layer called **ScalliGraph**.
So there are no tables or foreign keys — there are:

- **Vertices** = entities, declared with `@BuildVertexEntity`
- **Edges** = relationships, declared with `@BuildEdgeEntity[From, To]`

Indexes are declared inline on the entities with `@DefineIndex(...)` and are
backed by Lucene / Elasticsearch. Binary attachments are **not** stored in the
graph — only metadata + hashes are; the bytes live in an external store
(local FS / HDFS / S3) keyed by `attachmentId`.

Every vertex also carries ScalliGraph metadata properties: `_id`, `_label`,
`_createdBy`, `_createdAt`, `_updatedBy`, `_updatedAt`.

---

## Full model

```mermaid
erDiagram
    Organisation ||--o{ Share : OrganisationShare
    Organisation }o--o{ Organisation : OrganisationOrganisation
    Organisation ||--o{ Taxonomy : OrganisationTaxonomy
    Organisation ||--o{ Config : OrganisationConfig
    Organisation ||--o{ Dashboard : OrganisationDashboard
    Organisation ||--o{ Page : OrganisationPage
    User ||--o{ Role : UserRole
    User ||--o| Attachment : UserAttachment
    User ||--o{ Config : UserConfig
    Role }o--|| Profile : RoleProfile
    Role }o--|| Organisation : RoleOrganisation
    Share }o--|| Case : ShareCase
    Share }o--o{ Observable : ShareObservable
    Share }o--o{ Task : ShareTask
    Share }o--|| Profile : ShareProfile
    Case }o--o| ResolutionStatus : CaseResolutionStatus
    Case }o--o| ImpactStatus : CaseImpactStatus
    Case }o--o{ Tag : CaseTag
    Case }o--o| Case : MergedFrom
    Case }o--o{ CustomField : CaseCustomField
    Case }o--o| User : CaseUser
    Case }o--o| CaseTemplate : CaseCaseTemplate
    Case ||--o{ Procedure : CaseProcedure
    Task }o--o| User : TaskUser
    Task ||--o{ Log : TaskLog
    Log ||--o{ Attachment : LogAttachment
    Observable ||--o{ KeyValue : ObservableKeyValue
    Observable }o--o{ Attachment : ObservableAttachment
    Observable }o--o| Data : ObservableData
    Observable }o--o{ Tag : ObservableTag
    Observable ||--o{ ReportTag : ObservableReportTag
    Alert }o--o{ CustomField : AlertCustomField
    Alert ||--o{ Observable : AlertObservable
    Alert }o--|| Organisation : AlertOrganisation
    Alert }o--o| Case : AlertCase
    Alert }o--o| CaseTemplate : AlertCaseTemplate
    Alert }o--o{ Tag : AlertTag
    CaseTemplate }o--|| Organisation : CaseTemplateOrganisation
    CaseTemplate }o--o{ CustomField : CaseTemplateCustomField
    CaseTemplate }o--o{ Tag : CaseTemplateTag
    CaseTemplate ||--o{ Task : CaseTemplateTask
    Taxonomy ||--o{ Tag : TaxonomyTag
    Procedure }o--|| Pattern : ProcedurePattern
    Pattern }o--o| Pattern : PatternPattern
    Audit }o--|| User : AuditUser
    Observable ||--o{ Job : ObservableJob
    Job ||--o{ Observable : ReportObservable
    Organisation {
        string name UK
        string description
    }
    User {
        string login UK
        string name
        string apikey
        boolean locked
        string password
        string totpSecret
    }
    Role {
    }
    Profile {
        string name UK
        set permissions
    }
    Case {
        int number UK
        string title
        int severity
        date startDate
        boolean flag
        int tlp
        int pap
        enum status
        seq tags
        string assignee
    }
    Task {
        string title
        string group
        enum status
        boolean flag
        int order
        date dueDate
        string assignee
    }
    Log {
        string message
        date date
    }
    Observable {
        string message
        int tlp
        boolean ioc
        boolean sighted
        string dataType
        seq tags
        string data
    }
    Data {
        string data UK
        string fullData
    }
    Alert {
        string type
        string source
        string sourceRef
        string title
        int severity
        date date
        int tlp
        int pap
        boolean read
        boolean follow
        seq tags
    }
    CaseTemplate {
        string name
        string displayName
        seq tags
        int severity
        boolean flag
        int tlp
        int pap
    }
    CustomField {
        string name UK
        string displayName
        enum type
        boolean mandatory
        seq options
    }
    Tag {
        string namespace
        string predicate
        string value
        string colour
    }
    Attachment {
        string name
        long size
        string contentType
        seq hashes
        string attachmentId
    }
    Share {
        boolean owner
    }
    Audit {
        string requestId
        string action
        boolean mainAction
        string objectId
        string objectType
    }
    Dashboard {
        string title
        string description
        json definition
    }
    Page {
        string title
        string slug
        int order
        string category
    }
    Procedure {
        string description
        date occurDate
        string tactic
    }
    Pattern {
        string patternId
        string name
        set tactics
        string patternType
        boolean revoked
    }
    Taxonomy {
        string namespace
        string description
        int version
    }
    KeyValue {
        string namespace
        string predicate
        string level
        enum type
    }
    ReportTag {
        string origin
        enum level
        string namespace
        string predicate
    }
    ObservableType {
        string name UK
        boolean isAttachment
    }
    Config {
        string name
        json value
    }
    ResolutionStatus {
        string value UK
    }
    ImpactStatus {
        string value UK
    }
    Job {
        string workerId
        string workerName
        enum status
        date startDate
        json report
        string cortexId
    }
    Action {
        string workerId
        string workerName
        enum status
        json parameters
        json report
    }
    AnalyzerTemplate {
        string workerId
        string content
    }
```

---

## Per-domain diagrams

### 1. Case & collaboration

```mermaid
erDiagram
    Organisation ||--o{ Share : OrganisationShare
    Share }o--|| Case : ShareCase
    Share }o--o{ Task : ShareTask
    Share }o--o{ Observable : ShareObservable
    Share }o--|| Profile : ShareProfile
    Case }o--o| User : CaseUser
    Case }o--o| ResolutionStatus : CaseResolutionStatus
    Case }o--o| ImpactStatus : CaseImpactStatus
    Case }o--o{ Tag : CaseTag
    Case }o--o| Case : MergedFrom
    Case }o--o{ CustomField : CaseCustomField
    Case }o--o| CaseTemplate : CaseCaseTemplate
    Case ||--o{ Procedure : CaseProcedure
    Task }o--o| User : TaskUser
    Task ||--o{ Log : TaskLog
    Log ||--o{ Attachment : LogAttachment
    CaseTemplate }o--|| Organisation : CaseTemplateOrganisation
    CaseTemplate ||--o{ Task : CaseTemplateTask
    CaseTemplate }o--o{ CustomField : CaseTemplateCustomField
    CaseTemplate }o--o{ Tag : CaseTemplateTag
    Procedure }o--|| Pattern : ProcedurePattern
    Case {
        int number UK
        string title
        int severity
        enum status
        int tlp
        int pap
        string assignee
        set organisationIds
    }
    Task {
        string title
        string group
        enum status
        int order
        date dueDate
    }
    Share {
        boolean owner
    }
    Procedure {
        string tactic
        date occurDate
    }
```

A `Case` is the central investigation object. It is made visible to an
`Organisation` only through a `Share`, which also bundles its `Task`s and
`Observable`s and pins a `Profile` (permission level) for that share.

### 2. Alerts & observable enrichment

```mermaid
erDiagram
    Alert ||--o{ Observable : AlertObservable
    Alert }o--|| Organisation : AlertOrganisation
    Alert }o--o| Case : AlertCase
    Alert }o--o| CaseTemplate : AlertCaseTemplate
    Alert }o--o{ Tag : AlertTag
    Alert }o--o{ CustomField : AlertCustomField
    Observable }o--o| Data : ObservableData
    Observable ||--o{ KeyValue : ObservableKeyValue
    Observable }o--o{ Attachment : ObservableAttachment
    Observable }o--o{ Tag : ObservableTag
    Observable ||--o{ ReportTag : ObservableReportTag
    Share }o--o{ Observable : ShareObservable
    Alert {
        string type
        string source
        string sourceRef
        int severity
        boolean read
        boolean follow
    }
    Observable {
        string dataType
        boolean ioc
        boolean sighted
        int tlp
        string data
    }
    Data {
        string data UK
    }
    ReportTag {
        string origin
        enum level
    }
```

An `Alert` is an inbound event (unique on `type`+`source`+`sourceRef`+org). It
can be promoted into a `Case`, carrying its `Observable`s with it. Observable
values are deduplicated into `Data` vertices; analyzer verdicts attach as
`ReportTag`.

### 3. RBAC & multi-tenancy

```mermaid
erDiagram
    User ||--o{ Role : UserRole
    Role }o--|| Profile : RoleProfile
    Role }o--|| Organisation : RoleOrganisation
    Organisation ||--o{ Share : OrganisationShare
    Share }o--|| Profile : ShareProfile
    User ||--o| Attachment : UserAttachment
    User ||--o{ Config : UserConfig
    Organisation ||--o{ Config : OrganisationConfig
    Organisation }o--o{ Organisation : OrganisationOrganisation
    Organisation ||--o{ Dashboard : OrganisationDashboard
    Dashboard }o--o| User : DashboardUser
    Organisation ||--o{ Page : OrganisationPage
    Organisation ||--o{ Taxonomy : OrganisationTaxonomy
    Taxonomy ||--o{ Tag : TaxonomyTag
    Audit }o--|| User : AuditUser
    Organisation {
        string name UK
    }
    User {
        string login UK
        boolean locked
        string apikey
    }
    Profile {
        string name UK
        set permissions
    }
    Audit {
        string action
        string objectType
        boolean mainAction
    }
```

A `User` has one `Role` per `Organisation`; a `Role` binds the user to a
`Profile` (the named permission set) within that org. `OrganisationOrganisation`
links orgs together (e.g. for cross-org sharing).

### 4. MITRE ATT&CK & Cortex

```mermaid
erDiagram
    Case ||--o{ Procedure : CaseProcedure
    Procedure }o--|| Pattern : ProcedurePattern
    Pattern }o--o| Pattern : PatternPattern
    Observable ||--o{ Job : ObservableJob
    Job ||--o{ Observable : ReportObservable
    Action }o..o| Case : ActionContext
    Pattern {
        string patternId
        string name
        set tactics
        string patternType
        boolean revoked
    }
    Procedure {
        string tactic
        date occurDate
    }
    Job {
        string workerName
        enum status
        string cortexId
        json report
    }
    Action {
        string workerName
        enum status
        json parameters
        json report
    }
    AnalyzerTemplate {
        string workerId
        string content
    }
```

ATT&CK techniques are `Pattern` vertices (self-linked into a tactic/technique
hierarchy via `PatternPattern`), tied to a `Case` through a `Procedure`. The
Cortex connector adds `Job` (analyzer runs on observables) and `Action`
(responder runs on any entity, via the polymorphic `ActionContext` edge).

---

## Vertices (entities)

| Vertex | Purpose | Notable / unique fields |
|---|---|---|
| **Organisation** | Tenant boundary (multi-tenancy) | `name` (unique) |
| **User** | Account | `login` (unique), `apikey`, `password`, `totpSecret`, `locked` |
| **Role** | Join object binding a User → Profile within an Org | *(no own fields)* |
| **Profile** | Named permission set | `name` (unique), `permissions` |
| **Case** | Investigation case | `number` (unique), `severity`, `tlp`, `pap`, `status`, `tags`, `assignee` |
| **Task** | Work item within a case | `group`, `status`, `order`, `dueDate`, `assignee` |
| **Log** | Task log entry | `message`, `date` |
| **Observable** | IOC / artifact | `dataType`, `ioc`, `sighted`, `tlp`, `data` |
| **Data** | Deduplicated observable value | `data` (unique) — hashed if oversized |
| **Alert** | Inbound event before promotion to a case | unique (`type`,`source`,`sourceRef`,`organisationId`); `read`, `follow` |
| **CaseTemplate** | Template for cases | `name`; tasks/customFields via edges |
| **CustomField** | Custom-field definition | `name` (unique), `type` ∈ {string,integer,float,boolean,date} |
| **Tag** | Tag / taxonomy tag | unique (`namespace`,`predicate`,`value`), `colour` |
| **Attachment** | File metadata (bytes live externally) | `hashes`, `attachmentId` |
| **Share** | Grants an Org access to a Case (+ tasks/observables) | `owner` |
| **Audit** | Audit-trail event | `action` (create/update/delete/merge), `objectId`, `objectType` |
| **Dashboard** | Dashboard definition | `definition` (JSON) |
| **Page** | Org knowledge-base page | `slug`, `category`, `order` |
| **Procedure** | ATT&CK procedure occurrence on a case | `tactic`, `occurDate` |
| **Pattern** | MITRE ATT&CK technique/tactic | `patternId`, `tactics`, `capecId`; self-referential parent |
| **Taxonomy** | MISP-style taxonomy | `namespace`, `version` |
| **KeyValue** | Extra typed key/values on observables | `type` ∈ ValueType |
| **ReportTag** | Tag produced by a Cortex analyzer report | `level` ∈ {info,safe,suspicious,malicious} |
| **ObservableType** | Allowed observable datatypes | `name` (unique), `isAttachment` |
| **Config** | Org-level / user-level config | `name`, `value` (JSON) |
| **ResolutionStatus / ImpactStatus** | Case status lookups | `value` (unique) |
| **Job** *(Cortex)* | Analyzer run on an observable | `status`, `cortexId`, `report` |
| **Action** *(Cortex)* | Responder run on any entity | `status`, `parameters`, `report` |
| **AnalyzerTemplate** *(Cortex)* | Cached analyzer report template | `workerId`, `content` |

## Edges (relationships)

Most edges are plain directed links. A few carry properties:

- **Custom-field-value edges** (`CaseCustomField`, `AlertCustomField`,
  `CaseTemplateCustomField`) hold `order` + one of
  `stringValue` / `booleanValue` / `integerValue` / `floatValue` / `dateValue`.
- `ShareTask` carries `actionRequired`; `OrganisationDashboard` carries `writable`.
- Self-referential: `MergedFrom` (Case→Case), `PatternPattern` (Pattern→parent),
  `OrganisationOrganisation` (org→org links).

**Polymorphic edges** (hand-written `EdgeModel`s whose target is *any* vertex,
so they are omitted from the typed diagrams above):

| Edge | From | To |
|---|---|---|
| **Audited** | Audit | the audited object (any vertex) |
| **AuditContext** | Audit | the visibility context (e.g. owning Case) |
| **ActionContext** | Action | the entity a responder acted on (any vertex) |

---

## Permissions reference

A `Profile` is a named set of these permissions (`models/Permissions.scala`).
Each permission has a **scope**: `organisation` permissions can be granted to any
org's profiles; `admin`-scoped permissions are **only effective in the admin
organisation** and are stripped from ordinary orgs (`restrictedPermissions`).

| Permission | Scope | Governs |
|---|---|---|
| `manageCase` | organisation | create/update/delete cases |
| `manageObservable` | organisation | observables/IOCs |
| `manageAlert` | organisation | alerts + promote-to-case |
| `manageTask` | organisation | tasks + logs |
| `manageShare` | organisation | share/unshare a case with orgs |
| `manageProcedure` | organisation | ATT&CK procedures on cases |
| `managePage` | organisation | org knowledge-base pages |
| `manageTag` | organisation | tags |
| `manageCaseTemplate` | organisation | case/task templates |
| `manageAnalyse` | organisation | run Cortex **analyzers** |
| `manageAction` | organisation | run Cortex **responders** |
| `accessTheHiveFS` | organisation | the TheHiveFS/WebDAV attachment mount |
| `manageUser` | organisation, admin | users (org-scoped, broader in admin org) |
| `manageConfig` | organisation, admin | org-level + platform config |
| `manageOrganisation` | admin | create/manage organisations |
| `manageProfile` | admin | permission profiles |
| `manageCustomField` | admin | custom-field definitions |
| `manageObservableTemplate` | admin | observable types |
| `manageTaxonomy` | admin | taxonomies |
| `managePattern` | admin | MITRE ATT&CK pattern catalog |
| `manageAnalyzerTemplate` | admin | Cortex report templates |
| `managePlatform` | admin | platform-wide administration |

**Built-in profiles** (`Profile.initialValues`): `admin` (all admin-scope perms),
`org-admin` (all organisation-scope perms), `analyst` (manage case/observable/
alert/task/action/share/analyse/page/procedure + accessTheHiveFS), and
`read-only` (no permissions).

## Enumerations reference

| Enum | Values | Used by |
|---|---|---|
| `CaseStatus` | `Open`, `Resolved`, `Duplicated` | `Case.status` |
| `TaskStatus` | `Waiting`, `InProgress`, `Completed`, `Cancel` | `Task.status` |
| `CustomFieldType` | `string`, `integer`, `float`, `boolean`, `date` | `CustomField.type` |
| `ValueType` | `string`, `integer`, `float`, `boolean`, `date` | `KeyValue.type` |
| `ReportTagLevel` | `info`, `safe`, `suspicious`, `malicious` | `ReportTag.level` (Cortex verdicts) |
| `JobStatus` (Cortex) | `InProgress`, `Success`, `Failure`, `Waiting`, `Deleted` | `Job.status`, `Action.status` |

**TLP** (`tlp`) and **PAP** (`pap`) are integers `0..3` = WHITE / GREEN / AMBER /
RED, default `2` (AMBER). **Severity** is `1..4` (low/medium/high/critical).

**`ObservableType.initialValues`** (the seeded datatypes; `isAttachment=true` only
for `file`): `url`, `domain`, `fqdn`, `hostname`, `ip`, `mail`, `mail-subject`,
`hash`, `filename`, `file`, `registry`, `regexp`, `uri_path`, `user-agent`,
`autonomous-system`, `other`.

---

## Things worth knowing when reading/querying the model

- **Multi-tenancy is enforced by graph traversal.** Visibility of a `Case` =
  "is there a `Share` path from your `Organisation` to it?" Several vertices
  also denormalize `organisationIds` / `relatedId` as **indexed properties**
  purely to speed up those traversals.
- **Denormalized fields shadow edges.** `Case.assignee` / `impactStatus` /
  `resolutionStatus`, `Observable.data`, `Alert.caseId`, etc. are
  "filled by the service" copies of edge targets, kept for indexing. The edges
  (`CaseUser`, `ObservableData`, `AlertCase`, …) are the source of truth.
- **Attachments/blobs are not in the graph** — `Attachment.attachmentId`
  references an external object store; the graph keeps only metadata + hashes.
- **MITRE ATT&CK** is modeled as `Pattern` (self-linked hierarchy) linked to
  cases through `Procedure`.
- **Cortex** (analyzers/responders) lives in a separate connector module:
  `Job`, `Action`, `AnalyzerTemplate`.
