"""Telegram Bot API connector — the real-world replacement for the LumenX client.

Auth is just a bot token (TELEGRAM_BOT_TOKEN) from @BotFather — no OAuth, no
consent screen, no admin. The pipeline talks to this wrapper, not Telegram
directly, so another platform could be swapped in later without touching it.

Concept mapping (LumenX -> Telegram):
  thread / conversation  ->  a Telegram chat (chat_id)
  incoming message       ->  an update with a `message`
  poll the inbox         ->  getUpdates long-polling (Phase 5 poller)
  send reply             ->  sendMessage to chat_id

Phase 0 sanity check (after you paste the token into .env):
    python -m tg.client
Prints the bot's @username if the token works.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


class TelegramClient:
    def __init__(self, token: str | None = None, timeout: float = 65.0):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token or self.token == "replace-me":
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN not set. Get one from @BotFather in the Telegram "
                "app (/newbot) and put it in .env."
            )
        self.base = f"https://api.telegram.org/bot{self.token}"
        # read timeout must exceed the long-poll timeout used in get_updates()
        self._http = httpx.Client(timeout=timeout)

    def _call(self, method: str, **params) -> dict | list:
        resp = self._http.post(f"{self.base}/{method}", json=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error on {method}: {data}")
        return data["result"]

    # --- read ---------------------------------------------------------------
    def get_me(self) -> dict:
        """getMe — used as the Phase 0 token sanity check."""
        return self._call("getMe")

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        """Long-poll for new updates. Pass (last_update_id + 1) as offset to ack
        everything before it so Telegram stops re-sending old updates."""
        params: dict = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", **params)  # type: ignore[return-value]

    # --- write --------------------------------------------------------------
    def send_message(
        self, chat_id: int | str, text: str, reply_to_message_id: int | None = None
    ) -> dict:
        params: dict = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        return self._call("sendMessage", **params)  # type: ignore[return-value]

    def send_typing(self, chat_id: int | str) -> dict:
        """Show the 'typing…' indicator while the LLM drafts a reply."""
        return self._call("sendChatAction", chat_id=chat_id, action="typing")  # type: ignore[return-value]


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    me = TelegramClient().get_me()
    print(f"Bot OK: @{me.get('username')}  (id {me.get('id')}, name {me.get('first_name')!r})")
