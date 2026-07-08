"""G4: Case reporting service — render templates with case data."""

import re
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.case_ import Case
from app.models.report_template import ReportTemplate


async def render_report(
    session: AsyncSession,
    template: ReportTemplate,
    case_id: int,
    fmt: str = "html",
) -> str:
    """Render a report template for a case. Supports markdown and HTML output."""
    case = await session.get(Case, case_id)
    if case is None:
        raise ValueError("Case not found")

    # Build context dict for template placeholders
    ctx = {
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
        "date": datetime.now().isoformat(),
    }

    # Simple {{ placeholder }} replacement
    content = template.content_md
    for key, val in ctx.items():
        content = content.replace("{{ " + key + " }}", str(val))
        content = content.replace("{{" + key + "}}", str(val))

    if fmt == "markdown":
        return content

    # Basic markdown → HTML conversion
    html = _md_to_html(content)
    return html


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
