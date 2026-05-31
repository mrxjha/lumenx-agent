"""Prompt library — load system prompts and the context template from disk."""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent


def load(name: str) -> str:
    """Load a prompt file by stem (e.g. 'draft_system' -> draft_system.md)."""
    path = _DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


INTENT_SYSTEM = load("intent_system")
DRAFT_SYSTEM = load("draft_system")
CONTEXT_TEMPLATE = load("context_template")
