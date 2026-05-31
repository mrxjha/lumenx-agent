"""One-shot bootstrap: initialise DB and build the LLM Wiki.

Usage:
    python -m scripts.bootstrap
"""
from __future__ import annotations

import json

from db import init_db
from wiki import build_wiki


def main() -> None:
    db_path = init_db()
    print(f"[ok] db initialised at {db_path}")

    summary = build_wiki()
    print("[ok] wiki built:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
