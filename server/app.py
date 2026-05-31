"""Wiki Explorer web server.

Serves an interactive knowledge graph + chat UI for the Lumenx wiki.

Endpoints:
    GET  /                    single-page UI
    GET  /api/graph           {nodes, edges} for the visualisation
    GET  /api/wiki/INDEX      master index markdown
    GET  /api/wiki/company    company policy markdown
    GET  /api/wiki/{pid}      product page markdown
    POST /api/query           { question } -> { answer, sources }
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings
from wiki.loader import (
    load_wiki,
    load_company_policy,
    load_cross_refs,
    select_relevant_pages,
    expand_via_cross_refs,
)

ROOT = Path(__file__).parent
STATIC = ROOT / "static"

app = FastAPI(title="Lumenx Wiki Explorer")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------- helpers ----------

# Distinct color per product category, deterministic
CATEGORY_COLORS = {
    "Communication":     "#60a5fa",  # blue
    "Finance":           "#34d399",  # emerald
    "Productivity":      "#f472b6",  # pink
    "Data Collection":   "#fbbf24",  # amber
    "Scheduling":        "#a78bfa",  # violet
    "Knowledge":         "#22d3ee",  # cyan
    "Customer Support":  "#fb923c",  # orange
    "Documents":         "#facc15",  # yellow
    "Research":          "#4ade80",  # green
    "Meetings":          "#f87171",  # red
    "Design":            "#e879f9",  # fuchsia
    "People":            "#2dd4bf",  # teal
    "Compliance":        "#94a3b8",  # slate
}
DEFAULT_COLOR = "#9ca3af"


def _load_products_meta() -> dict[str, dict[str, Any]]:
    """Parse the first lines of every wiki page to get title + category + tagline."""
    pages = load_wiki()
    out: dict[str, dict[str, Any]] = {}
    for pid, md in pages.items():
        title = pid
        category = "Uncategorized"
        tagline = ""
        for line in md.splitlines()[:8]:
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("**Category:**"):
                category = line.replace("**Category:**", "").strip().rstrip("  ")
            elif line.startswith("**Tagline:**"):
                tagline = line.replace("**Tagline:**", "").strip()
        out[pid] = {"title": title, "category": category, "tagline": tagline}
    return out


# ---------- routes ----------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    """Liveness probe for Railway / load balancer. Cheap — touches only the
    wiki directory and the SQLite file path."""
    try:
        wiki_dir = settings.wiki_path
        db_exists = settings.sqlite_path.exists()
        wiki_ok = wiki_dir.exists() and any(wiki_dir.glob("*.md"))
        return {
            "status": "ok" if (wiki_ok and db_exists) else "degraded",
            "wiki_pages": wiki_ok,
            "db_present": db_exists,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/graph")
def graph() -> JSONResponse:
    cross = load_cross_refs()
    meta = _load_products_meta()
    if not cross or not meta:
        raise HTTPException(503, "Wiki not built yet. Run `python -m wiki.builder` first.")

    nodes = []
    # Central "company" node
    nodes.append({
        "id": "__company__",
        "label": "Lumenx",
        "title": "Company-wide policies",
        "group": "company",
        "color": {"background": "#facc15", "border": "#a16207"},
        "shape": "star",
        "size": 36,
        "font": {"size": 18, "color": "#fef3c7", "face": "Inter, sans-serif"},
    })

    for pid, info in meta.items():
        color = CATEGORY_COLORS.get(info["category"], DEFAULT_COLOR)
        nodes.append({
            "id": pid,
            "label": info["title"],
            "title": f"{info['category']} · {info['tagline']}",
            "group": info["category"],
            "color": {"background": color, "border": "#1f2937"},
            "shape": "dot",
            "size": 22,
            "font": {"size": 14, "color": "#e6edf3", "face": "Inter, sans-serif"},
        })

    edges = []
    seen_pairs: set[tuple[str, str, str]] = set()

    def _add_edge(a: str, b: str, kind: str, color: str, dashes: bool, width: float):
        key = (min(a, b), max(a, b), kind)
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        edges.append({
            "from": a,
            "to": b,
            "color": {"color": color, "opacity": 0.55},
            "dashes": dashes,
            "width": width,
            "title": kind,
            "smooth": {"type": "continuous"},
        })

    for pid, rel in cross.items():
        for other in rel.get("category_siblings", []):
            _add_edge(pid, other, "Same category", "#475569", True, 1.0)
        for other in rel.get("mentions", []):
            _add_edge(pid, other, "Explicit mention", "#7c3aed", False, 2.4)
        for other in rel.get("shared_integrations", []):
            _add_edge(pid, other, "Shared integrations", "#06b6d4", False, 1.3)
        # link every product to the central company node, faint
        _add_edge(pid, "__company__", "Company policy", "#a16207", True, 0.6)

    return JSONResponse({"nodes": nodes, "edges": edges})


@app.get("/api/wiki/INDEX")
def wiki_index() -> dict:
    path = settings.wiki_path.parent / "INDEX.md"
    if not path.exists():
        raise HTTPException(404, "INDEX.md not built yet")
    return {"id": "INDEX", "title": "Wiki Index", "markdown": path.read_text(encoding="utf-8")}


@app.get("/api/wiki/company")
def wiki_company() -> dict:
    md = load_company_policy()
    if md is None:
        raise HTTPException(404, "company_policy.md not built yet")
    return {"id": "company", "title": "Lumenx — Company Policies", "markdown": md}


@app.get("/api/wiki/{pid}")
def wiki_page(pid: str) -> dict:
    pages = load_wiki()
    if pid not in pages:
        raise HTTPException(404, f"Unknown wiki page: {pid}")
    title = pid
    for line in pages[pid].splitlines()[:3]:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return {"id": pid, "title": title, "markdown": pages[pid]}


# ---------- query ----------

class QueryBody(BaseModel):
    question: str
    top_k: int = 3
    follow_cross_refs: bool = True


def _snippet(md: str, n: int = 240) -> str:
    # strip leading title + metadata lines
    body_lines = [ln for ln in md.splitlines() if not ln.startswith("**") and not ln.startswith("#")]
    body = " ".join(ln.strip() for ln in body_lines if ln.strip())
    return body[:n] + ("…" if len(body) > n else "")


@app.post("/api/query")
def query(body: QueryBody) -> dict:
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Empty question")

    pages = load_wiki()
    seeds = select_relevant_pages(q, pages=pages, top_k=body.top_k)
    seed_ids = [pid for pid, _, _ in seeds]
    extras = (
        expand_via_cross_refs(seed_ids, max_extra=2) if body.follow_cross_refs else []
    )

    sources: list[dict[str, Any]] = []
    for pid, md, score in seeds:
        sources.append({
            "id": pid,
            "title": _title_of(md, pid),
            "score": score,
            "via": "match",
            "snippet": _snippet(md),
        })
    for pid in extras:
        if pid in pages:
            sources.append({
                "id": pid,
                "title": _title_of(pages[pid], pid),
                "score": 0,
                "via": "cross-ref",
                "snippet": _snippet(pages[pid]),
            })

    # Always include policy as a quiet source for sensitive questions
    policy_md = load_company_policy()
    policy_block = ""
    if policy_md:
        policy_block = "<!-- source: company_policy.md -->\n" + policy_md

    context_blocks = [policy_block] if policy_block else []
    for pid, md, score in seeds:
        context_blocks.append(f"<!-- source: products/{pid}.md  score={score} -->\n{md}")
    for pid in extras:
        if pid in pages:
            context_blocks.append(f"<!-- source: products/{pid}.md  via=cross_ref -->\n{pages[pid]}")
    context = "\n\n---\n\n".join(context_blocks)

    answer = _call_claude(q, context)

    return {
        "question": q,
        "answer": answer["text"],
        "model": answer["model"],
        "sources": sources,
        "context_chars": len(context),
    }


def _title_of(md: str, fallback: str) -> str:
    for line in md.splitlines()[:3]:
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


SYSTEM_PROMPT = """You are the Lumenx wiki assistant. You answer questions strictly from the wiki context provided below. Rules:

1. Use ONLY information present in the <wiki> block. If the answer isn't there, say "I don't have that information in the wiki." — do not guess.
2. Pricing, refund windows, and discount percentages are sensitive. Quote them verbatim from the wiki. Never invent or round numbers.
3. When you state a fact, end the sentence with a citation like [product_id] or [company_policy] matching the `source:` comment of the block you used.
4. Be concise: 2–5 short sentences unless the question genuinely needs more.
5. If multiple products are relevant, name them and cite each one.
6. Tone: professional, friendly, empathetic. No emojis. No marketing fluff.
"""


def _call_claude(question: str, wiki_context: str) -> dict[str, str]:
    """Call Claude with the wiki context. Falls back to a snippet-only answer if
    no ANTHROPIC_API_KEY is configured.
    """
    if not settings.anthropic_api_key:
        return {
            "text": (
                "_(No `ANTHROPIC_API_KEY` configured — showing matched source pages instead. "
                "Set the key in `.env` and restart the server to get LLM-generated answers.)_\n\n"
                "Top matches are listed on the right; click any source chip to open the page."
            ),
            "model": "fallback",
        }
    try:
        from anthropic import Anthropic
    except ImportError:
        return {
            "text": "_(The `anthropic` package isn't installed. Run `pip install anthropic`.)_",
            "model": "fallback",
        }

    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        user_prompt = f"<wiki>\n{wiki_context}\n</wiki>\n\nQuestion: {question}"
        resp = client.messages.create(
            model=settings.draft_model,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        return {"text": text or "_(empty response)_", "model": resp.model}
    except Exception as e:
        msg = str(e)
        if "invalid x-api-key" in msg or "authentication_error" in msg or "401" in msg:
            hint = ("_(The configured `ANTHROPIC_API_KEY` was rejected by the Anthropic API "
                    "with a 401 authentication error. A valid key starts with `sk-ant-`. "
                    "Update `.env` and restart the server.)_\n\n"
                    "Top matching source pages are shown on the right — the retrieval layer "
                    "works without the LLM, so you can still verify what would be cited.")
            return {"text": hint, "model": "fallback (auth_error)"}
        return {"text": f"_(LLM call failed: {type(e).__name__}: {msg[:300]})_", "model": "fallback (error)"}
