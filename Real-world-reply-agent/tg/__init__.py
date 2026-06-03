"""Telegram connector package for the real-world auto-reply agent.

Named `tg` (not `telegram`) on purpose: the popular `python-telegram-bot` library
installs a top-level package called `telegram`, so a local `telegram/` package
could shadow it and cause confusing import clashes. We talk to the raw Bot API
over httpx instead — no heavy dependency, and it mirrors the old
../Auto-reply-agent/lumenx/client.py style.
"""
