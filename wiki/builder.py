"""LLM Wiki builder.

Pulls product + policy data from the LumenX admin API and renders the wiki:
    wiki/products/{id}.md       one structured page per product
    wiki/company_policy.md      cross-product policies (refund window, programs, bundle)
    wiki/INDEX.md               master cross-reference (by category, integration, audience)

Every product page contains a **Related Products** section that links to:
  - same-category siblings
  - products explicitly mentioned in description/features
  - products sharing >=2 integrations
  - the company policy page

Cross-references are intra-wiki relative markdown links, so the LLM (and any
human reading the wiki) can navigate the knowledge graph without leaving it.

Reference (wiki-as-context pattern):
    https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from config import settings
from lumenx import LumenXClient


# ---------------------------------------------------------------------------
# Cross-reference graph
# ---------------------------------------------------------------------------

def _text_blob(product: dict[str, Any]) -> str:
    parts = [
        product.get("description", ""),
        product.get("tagline", ""),
        " ".join(product.get("features", []) or []),
    ]
    return " ".join(parts).lower()


def build_cross_ref_graph(products: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """Compute every product's relations to every other product.

    Returns: { product_id: { "category_siblings": [...], "mentions": [...],
                             "shared_integrations": [...] } }
    """
    by_id = {p["id"]: p for p in products}
    by_category: dict[str, list[str]] = defaultdict(list)
    for p in products:
        by_category[p.get("category", "Uncategorized")].append(p["id"])

    graph: dict[str, dict[str, list[str]]] = {}
    for p in products:
        pid = p["id"]
        relations: dict[str, list[str]] = {
            "category_siblings": [s for s in by_category[p.get("category", "Uncategorized")] if s != pid],
            "mentions": [],
            "shared_integrations": [],
        }

        # Explicit mentions: another product's id or name appearing in this product's text
        blob = _text_blob(p)
        for other_id, other in by_id.items():
            if other_id == pid:
                continue
            if other_id in blob or other["name"].lower() in blob:
                relations["mentions"].append(other_id)

        # Shared integrations: at least 2 common integrations counts as "related"
        my_ints = set(map(str.lower, p.get("integrations", []) or []))
        if my_ints:
            shared = []
            for other_id, other in by_id.items():
                if other_id == pid:
                    continue
                other_ints = set(map(str.lower, other.get("integrations", []) or []))
                if len(my_ints & other_ints) >= 2:
                    shared.append(other_id)
            relations["shared_integrations"] = shared

        graph[pid] = relations

    return graph


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _md_section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def _render_pricing(pricing: Any) -> str:
    if not pricing:
        return "_No pricing tiers listed._"
    if isinstance(pricing, dict):
        lines = []
        for tier_name, tier in pricing.items():
            if isinstance(tier, dict):
                bits = ", ".join(f"{k}: {v}" for k, v in tier.items())
                lines.append(f"- **{tier_name}** — {bits}")
            else:
                lines.append(f"- **{tier_name}** — {tier}")
        return "\n".join(lines)
    return str(pricing)


def _bullet_list(items: list[Any]) -> str:
    if not items:
        return "_None listed._"
    return "\n".join(f"- {i}" for i in items)


def _link_to_product(pid: str, by_id: dict[str, dict[str, Any]]) -> str:
    name = by_id[pid]["name"] if pid in by_id else pid
    return f"[{name}](./{pid}.md)"


def _related_products_section(
    pid: str,
    graph: dict[str, dict[str, list[str]]],
    by_id: dict[str, dict[str, Any]],
) -> str:
    rel = graph.get(pid, {})
    blocks: list[str] = []

    cat_sibs = rel.get("category_siblings", [])
    if cat_sibs:
        cat = by_id[pid].get("category", "Uncategorized")
        blocks.append(f"**Same category ({cat}):** " + ", ".join(_link_to_product(s, by_id) for s in cat_sibs))

    mentions = rel.get("mentions", [])
    if mentions:
        blocks.append("**Explicitly mentions:** " + ", ".join(_link_to_product(m, by_id) for m in mentions))

    shared = [s for s in rel.get("shared_integrations", []) if s not in cat_sibs and s not in mentions]
    if shared:
        blocks.append("**Shares 2+ integrations with:** " + ", ".join(_link_to_product(s, by_id) for s in shared))

    blocks.append("**Company-wide policies:** [Lumenx policies](../company_policy.md)")
    blocks.append("**Wiki index:** [All products](../INDEX.md)")

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Product page
# ---------------------------------------------------------------------------

def render_product_md(
    product: dict[str, Any],
    graph: dict[str, dict[str, list[str]]],
    by_id: dict[str, dict[str, Any]],
) -> str:
    pid = product["id"]
    parts: list[str] = [
        f"# {product['name']}",
        "",
        f"**Product ID:** `{pid}`  ",
        f"**Category:** {product.get('category', 'Uncategorized')}  ",
        f"**Tagline:** {product.get('tagline', '')}",
        "",
    ]

    if product.get("description"):
        parts.append(_md_section("Description", product["description"]))

    if product.get("target_audience"):
        parts.append(_md_section("Target Audience", product["target_audience"]))

    parts.append(_md_section("Pricing Tiers", _render_pricing(product.get("pricing"))))

    if product.get("annual_discount_pct") is not None:
        parts.append(_md_section(
            "Annual Discount",
            f"{product['annual_discount_pct']}% off when billed annually. "
            "See [Lumenx policies](../company_policy.md) for company-wide programs."
        ))

    parts.append(_md_section("Features", _bullet_list(product.get("features", []) or [])))

    if product.get("integrations"):
        parts.append(_md_section("Integrations", _bullet_list(product["integrations"])))

    if product.get("refund"):
        parts.append(_md_section(
            "Refund Policy",
            f"{product['refund']}\n\nFor the company-wide refund window and edge cases, "
            "see [Lumenx policies → refund](../company_policy.md#refund-and-billing)."
        ))

    if product.get("cancellation"):
        parts.append(_md_section("Cancellation", product["cancellation"]))

    if product.get("support_sla_hours") is not None:
        parts.append(_md_section(
            "Support SLA",
            f"Response within **{product['support_sla_hours']} hours** during support hours. "
            "See [Lumenx policies → support hours](../company_policy.md#support)."
        ))

    parts.append(_md_section("Related Products", _related_products_section(pid, graph, by_id)))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Company policy page
# ---------------------------------------------------------------------------

def render_company_policy_md(company: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    parts = [
        f"# {company.get('name', 'Lumenx')} — Company Policies",
        "",
        f"_{company.get('tagline', '')}_",
        "",
        company.get("description", ""),
        "",
        "## At a Glance",
        f"- **Founded:** {company.get('founded', 'n/a')}",
        f"- **Headquarters:** {company.get('headquarters', 'n/a')}",
        f"- **Billing currency:** {company.get('billing_currency', 'USD')}",
        "",
        "## Support",
        f"- **Email:** {company.get('support_email', 'n/a')}",
        f"- **Hours:** {company.get('support_hours', 'n/a')}",
        "",
        "## Refund and Billing",
        f"- **Refund window:** {company.get('refund_window_days', 'n/a')} days from first purchase.",
        f"- **Free trial:** {company.get('free_trial_days', 'n/a')} days, no card required.",
        f"- **Annual discount:** {company.get('annual_discount_pct', 'n/a')}% off when billed annually.",
        "",
    ]

    sp = company.get("startup_program") or {}
    if sp:
        parts += [
            f"## {sp.get('name', 'Startup program')}",
            f"- **Eligibility:** {sp.get('eligibility', 'n/a')}",
            f"- **Discount:** {sp.get('discount_pct', 'n/a')}% off for {sp.get('duration_months', 'n/a')} months.",
            "",
        ]

    ep = company.get("education_program") or {}
    if ep:
        parts += [
            f"## {ep.get('name', 'Education program')}",
            f"- **Eligibility:** {ep.get('eligibility', 'n/a')}",
            f"- **Discount:** {ep.get('discount_pct', 'n/a')}% off.",
            "",
        ]

    bundle = company.get("bundle") or {}
    if bundle:
        eligible_links = ", ".join(f"[{by_id[pid]['name']}](products/{pid}.md)" for pid in sorted(by_id))
        parts += [
            f"## {bundle.get('name', 'Bundle')}",
            f"{bundle.get('description', '')}",
            f"- **Discount:** {bundle.get('discount_pct', 'n/a')}% off.",
            f"- **Eligible products:** {eligible_links}",
            "",
        ]

    parts += [
        "## See also",
        "- [Wiki index](INDEX.md)",
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Master index
# ---------------------------------------------------------------------------

def render_index_md(
    products: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    graph: dict[str, dict[str, list[str]]],
) -> str:
    parts = ["# Lumenx Wiki Index", "",
             "Master cross-reference for all 20 products plus company policies. "
             "Every link is a relative path inside `wiki/`, so the agent can navigate "
             "without leaving the wiki when assembling context.", "",
             "## See also",
             "- [Company-wide policies](company_policy.md)",
             ""]

    # By category
    by_category: dict[str, list[str]] = defaultdict(list)
    for p in products:
        by_category[p.get("category", "Uncategorized")].append(p["id"])
    parts.append("## By Category\n")
    for cat in sorted(by_category):
        parts.append(f"### {cat}")
        for pid in sorted(by_category[cat]):
            tagline = by_id[pid].get("tagline", "")
            parts.append(f"- [{by_id[pid]['name']}](products/{pid}.md) — {tagline}")
        parts.append("")

    # By integration
    by_integration: dict[str, list[str]] = defaultdict(list)
    for p in products:
        for integ in p.get("integrations", []) or []:
            by_integration[integ].append(p["id"])
    parts.append("## By Integration\n")
    parts.append("_Use this to answer questions like \"Which Lumenx product integrates with Slack?\"._\n")
    for integ in sorted(by_integration, key=str.lower):
        owners = by_integration[integ]
        if len(owners) >= 1:
            parts.append(f"- **{integ}** → " + ", ".join(f"[{by_id[o]['name']}](products/{o}.md)" for o in owners))
    parts.append("")

    # Alphabetical
    parts.append("## Alphabetical\n")
    for pid in sorted(by_id):
        parts.append(f"- [{by_id[pid]['name']}](products/{pid}.md) — `{pid}`")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_wiki(client: LumenXClient | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    """Fetch products from LumenX and write the wiki to disk.

    Layout:
        {wiki_root}/INDEX.md
        {wiki_root}/company_policy.md
        {wiki_root}/products/{id}.md   (== settings.wiki_path)
    """
    products_dir = out_dir or settings.wiki_path
    wiki_root = products_dir.parent
    products_dir.mkdir(parents=True, exist_ok=True)
    wiki_root.mkdir(parents=True, exist_ok=True)

    owns_client = client is None
    client = client or LumenXClient()
    try:
        payload = client.get_products()
    finally:
        if owns_client:
            client.close()

    products = payload.get("products", []) if isinstance(payload, dict) else payload
    company = payload.get("company", {}) if isinstance(payload, dict) else {}

    by_id = {p["id"]: p for p in products}
    graph = build_cross_ref_graph(products)

    # Product pages
    for product in products:
        md = render_product_md(product, graph, by_id)
        (products_dir / f"{product['id']}.md").write_text(md, encoding="utf-8")

    # Company policy + index
    policy_path = wiki_root / "company_policy.md"
    policy_path.write_text(render_company_policy_md(company, by_id), encoding="utf-8")

    index_path = wiki_root / "INDEX.md"
    index_path.write_text(render_index_md(products, by_id, graph), encoding="utf-8")

    # Persist the cross-ref graph as JSON for the loader / Confidence Net features
    graph_path = wiki_root / "cross_refs.json"
    graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

    return {
        "products_written": len(products),
        "wiki_dir": str(products_dir),
        "policy_path": str(policy_path),
        "index_path": str(index_path),
        "cross_refs_path": str(graph_path),
    }


if __name__ == "__main__":
    summary = build_wiki()
    print(json.dumps(summary, indent=2))
