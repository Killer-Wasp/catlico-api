"""G4: Case reporting service — render templates with case data.

Placeholder syntax
------------------
The engine is a deliberately small string substituter (no Jinja dependency).

Scalars:  ``{{ key }}`` or ``{{key}}`` — replaced by the case field of that name
    (case_id, title, description, severity, status, tlp, pap, created_at,
    updated_at, assignee (as UUID), date, timeline_summary).

Sections (loops over the case's children):
    ``{{#observables}} ... {{/observables}}`` — the inner block is repeated once
        per observable, exposing ``{{type}} {{value}} {{tlp}} {{ioc}} {{sighted}}``.
    ``{{#tasks}} ... {{/tasks}}`` — per task: ``{{title}} {{status}} {{assignee}}``.
    ``{{#timeline}} ... {{/timeline}}`` — a single block exposing the same counts
        as ``{{ timeline_summary }}`` (created_at, closed_at, observable_count,
        task_count, comment_count).

An empty collection renders its section zero times. A section open tag with no
matching close tag is left untouched (the regex requires both), so a malformed
template can never eat the rest of the document. Tables should be authored as
raw HTML in the template — the md→html converter below is intentionally minimal.
"""

import re
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.case_ import Case
from app.models.comment import Comment, CommentEntityType
from app.models.observable import Observable
from app.models.report_template import ReportTemplate
from app.models.task import Task

#: Matches ``{{#name}} inner {{/name}}`` with a backreference on the name, so an
#: unclosed (or mismatched) open tag simply doesn't match and is left intact.
_SECTION_RE = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.DOTALL)


def _sub_scalars(text: str, ctx: dict[str, str]) -> str:
    for key, val in ctx.items():
        text = text.replace("{{ " + key + " }}", str(val))
        text = text.replace("{{" + key + "}}", str(val))
    return text


def _expand_sections(content: str, sections: dict[str, list[dict[str, str]]]) -> str:
    """Expand ``{{#name}}...{{/name}}`` blocks. Unknown section names are left as
    written (a later scalar pass may handle them); known names repeat the inner
    block once per item, substituting that item's fields."""

    def _repl(match: re.Match[str]) -> str:
        name, inner = match.group(1), match.group(2)
        if name not in sections:
            return match.group(0)
        return "".join(_sub_scalars(inner, item) for item in sections[name])

    return _SECTION_RE.sub(_repl, content)


async def render_report(
    session: AsyncSession,
    template: ReportTemplate,
    case_id: int,
    fmt: str = "html",
) -> str:
    """Render a report template for a case. Supports markdown and HTML output.

    See the module docstring for the supported placeholder / section syntax.
    """
    case = await session.get(Case, case_id)
    if case is None:
        raise ValueError("Case not found")

    observables = (
        (
            await session.execute(
                select(Observable)
                .where(
                    Observable.case_id == case_id,
                    Observable.deleted_at.is_(None),
                )
                .order_by(Observable.created_at)
            )
        )
        .scalars()
        .all()
    )
    tasks = (
        (
            await session.execute(
                select(Task)
                .where(Task.case_id == case_id, Task.deleted_at.is_(None))
                .order_by(Task.order, Task.id)
            )
        )
        .scalars()
        .all()
    )
    comment_count = (
        await session.execute(
            select(func.count())
            .select_from(Comment)
            .where(
                Comment.entity_type == CommentEntityType.case,
                Comment.entity_id == str(case_id),
                Comment.deleted_at.is_(None),
            )
        )
    ).scalar_one()

    closed_at = case.end_date.isoformat() if case.end_date else ""
    timeline_summary = (
        f"Created {case.created_at.isoformat() if case.created_at else ''}"
        + (f", closed {closed_at}" if closed_at else "")
        + f"; {len(observables)} observable{'s' if len(observables) != 1 else ''}, "
        + f"{len(tasks)} task{'s' if len(tasks) != 1 else ''}, "
        + f"{comment_count} comment{'s' if comment_count != 1 else ''}"
    )

    # Scalar context (existing placeholders, unchanged behaviour) + timeline summary.
    scalars = {
        "case_id": str(case.id),
        "title": case.title or "",
        "description": case.description or "",
        "severity": case.severity or "",
        "status": case.status.value if case.status else "",
        "tlp": str(case.tlp),
        "pap": str(case.pap),
        "created_at": case.created_at.isoformat() if case.created_at else "",
        "updated_at": case.updated_at.isoformat() if case.updated_at else "",
        "assignee": str(case.assignee_id) if case.assignee_id else "",
        "date": datetime.now(UTC).isoformat(),
        "timeline_summary": timeline_summary,
    }

    sections = {
        "observables": [
            {
                "type": o.observable_type,
                "value": o.data,
                "tlp": str(o.tlp),
                "ioc": str(o.ioc).lower(),
                "sighted": str(o.sighted).lower(),
            }
            for o in observables
        ],
        "tasks": [
            {
                "title": t.title,
                "status": t.status.value if t.status else "",
                "assignee": str(t.assignee_id) if t.assignee_id else "",
            }
            for t in tasks
        ],
        "timeline": [
            {
                "created_at": scalars["created_at"],
                "closed_at": closed_at,
                "observable_count": str(len(observables)),
                "task_count": str(len(tasks)),
                "comment_count": str(comment_count),
            }
        ],
    }

    # Expand sections first (per-item fields win inside their block), then apply
    # the case-level scalar substitution over whatever remains.
    content = _expand_sections(template.content_md, sections)
    content = _sub_scalars(content, scalars)

    if fmt == "markdown":
        return content

    return _wrap_print_html(_md_to_html(content))


#: Minimal print CSS so browser print-to-PDF (the chosen PDF path — no server-side
#: PDF library) produces reasonable margins and legible tables.
_PRINT_CSS = """<style>
@page { margin: 18mm; }
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; line-height: 1.5;
  color: #111; max-width: 800px; margin: 0 auto; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #999; padding: 4px 8px; text-align: left; }
h1, h2, h3 { page-break-after: avoid; }
</style>"""


def _wrap_print_html(body_html: str) -> str:
    return f"{_PRINT_CSS}\n{body_html}"


def _md_to_html(md: str) -> str:
    """Minimal markdown to HTML converter. ponytail: full renderer if needed."""
    lines = md.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append("<br>")
        elif stripped.startswith("# "):
            tag = "h1"
            while stripped.startswith("#"):
                stripped = stripped[1:]
                if tag == "h1" and stripped.startswith(" "):
                    tag = "h1"
                    stripped = stripped[1:]
                elif tag == "h1":
                    tag = "h2"
            out.append(f"<{tag}>{stripped.strip()}</{tag}>")
        elif stripped.startswith("- "):
            out.append(f"<li>{stripped[2:]}</li>")
        elif stripped.startswith("**") and stripped.endswith("**"):
            out.append(f"<strong>{stripped[2:-2]}</strong>")
        else:
            out.append(f"<p>{stripped}</p>")
    return "\n".join(out)
