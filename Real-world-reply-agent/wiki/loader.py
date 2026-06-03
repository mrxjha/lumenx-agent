"""Load wiki markdown pages, select pages relevant to a query, and expand
that selection along the cross-reference graph written by builder.py.

Phase 1 relevance is keyword overlap (cheap, deterministic). Embeddings can
slot in later behind the same interface.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from config import settings


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "i", "you", "we", "my", "your", "our", "this", "that", "it", "be", "with",
    "do", "does", "have", "has", "can", "will", "would", "should", "could",
    "what", "how", "when", "where", "why", "who", "which",
}


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _products_dir(wiki_dir: Optional[Path] = None) -> Path:
    return wiki_dir or settings.wiki_path


def _wiki_root(wiki_dir: Optional[Path] = None) -> Path:
    return _products_dir(wiki_dir).parent


# ---------------------------------------------------------------------------
# Disk loaders
# ---------------------------------------------------------------------------

def load_wiki(wiki_dir: Optional[Path] = None) -> dict[str, str]:
    """{product_id: markdown} for every product page."""
    pdir = _products_dir(wiki_dir)
    if not pdir.exists():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(pdir.glob("*.md"))}


def load_company_policy(wiki_dir: Optional[Path] = None) -> Optional[str]:
    path = _wiki_root(wiki_dir) / "company_policy.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def load_index(wiki_dir: Optional[Path] = None) -> Optional[str]:
    path = _wiki_root(wiki_dir) / "INDEX.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def load_cross_refs(wiki_dir: Optional[Path] = None) -> dict[str, dict[str, list[str]]]:
    """Cross-reference graph produced by builder.build_wiki()."""
    path = _wiki_root(wiki_dir) / "cross_refs.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Selection + cross-ref expansion
# ---------------------------------------------------------------------------

def select_relevant_pages(
    query: str,
    pages: Optional[dict[str, str]] = None,
    top_k: int = 3,
    intent: Optional[str] = None,
) -> list[tuple[str, str, int]]:
    """Score every page by keyword overlap with `query` (+ optional `intent`).

    Returns up to `top_k` results as `(product_id, markdown, score)`.
    Direct mentions of the product id in the query get a big boost.
    """
    pages = pages if pages is not None else load_wiki()
    if not pages:
        return []
    q_tokens = _tokenize(query)
    if intent:
        q_tokens.update(_tokenize(intent))

    scored: list[tuple[str, str, int]] = []
    q_lower = query.lower()
    for pid, md in pages.items():
        page_tokens = _tokenize(md)
        overlap = len(q_tokens & page_tokens)
        if pid.lower() in q_lower:
            overlap += 10  # explicit product id mention
        if overlap > 0:
            scored.append((pid, md, overlap))
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored[:top_k]


def expand_via_cross_refs(
    seed_ids: Iterable[str],
    cross_refs: Optional[dict[str, dict[str, list[str]]]] = None,
    include: tuple[str, ...] = ("mentions", "category_siblings"),
    max_extra: int = 3,
) -> list[str]:
    """Given seed page ids, return additional related page ids drawn from the
    cross-reference graph. By default follows explicit mentions and same-category
    siblings (the two strongest signals). Caps total extras at `max_extra`.
    """
    cross_refs = cross_refs if cross_refs is not None else load_cross_refs()
    if not cross_refs:
        return []
    seeds = set(seed_ids)
    extras: list[str] = []
    seen: set[str] = set(seeds)
    for seed in seeds:
        rel = cross_refs.get(seed, {})
        for key in include:
            for other in rel.get(key, []):
                if other in seen:
                    continue
                extras.append(other)
                seen.add(other)
                if len(extras) >= max_extra:
                    return extras
    return extras


def assemble_wiki_context(
    query: str,
    top_k: int = 3,
    intent: Optional[str] = None,
    follow_cross_refs: bool = True,
    max_extra_via_refs: int = 2,
) -> str:
    """Build the wiki context string for an LLM prompt.

    Order of assembly:
      1. company_policy.md      (always — sensitive policies)
      2. Top-k pages by relevance to the query
      3. Up to `max_extra_via_refs` related pages picked from the cross-ref graph
    """
    pages = load_wiki()
    policy = load_company_policy()
    relevant = select_relevant_pages(query, pages=pages, top_k=top_k, intent=intent)

    blocks: list[str] = []
    if policy:
        blocks.append("<!-- source: company_policy.md -->\n" + policy)

    seed_ids = [pid for pid, _, _ in relevant]
    for pid, md, score in relevant:
        blocks.append(f"<!-- source: products/{pid}.md  relevance_score={score} -->\n{md}")

    if follow_cross_refs and seed_ids:
        extras = expand_via_cross_refs(seed_ids, max_extra=max_extra_via_refs)
        for pid in extras:
            if pid in pages:
                blocks.append(f"<!-- source: products/{pid}.md  via=cross_ref -->\n{pages[pid]}")

    return "\n\n---\n\n".join(blocks)


def retrieval_hits(query: str, intent: Optional[str] = None) -> int:
    """Confidence-Net feature: how many wiki pages matched the query."""
    return len(select_relevant_pages(query, intent=intent))
