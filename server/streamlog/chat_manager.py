"""
chat_manager.py — Manages follow-up chat sessions with Gemini.

Each ChatSession:
  - Loads existing chat history from <name>.chat.pb on disk (if present)
  - Reconstructs the initial Gemini context from the .genspark.pb turns + summary
  - Tries to use Gemini CachedContent for the initial context (avoids re-tokenising
    the large initial prompt on every follow-up call)
  - Persists every new turn back to <name>.chat.pb so chat survives server restarts
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
import uuid
from pathlib import Path

from google import genai
from google.genai import types as gtypes

import sys

_file = Path(__file__).resolve()
_server_root = _file.parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

from proto import insightgen_pb2

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 3600  # 1 hour


class ChatSession:
    """A single follow-up conversation for one recording."""

    def __init__(
        self,
        genspark_path: Path,
        chat_path: Path,
        file_name: str,
        gemini_config: dict,
    ):
        self._genspark_path = genspark_path
        self._chat_path = chat_path
        self._file_name = file_name
        self._gemini_config = gemini_config

        # Initial Gemini context (genspark turns + summary) — built lazily
        self._initial_contents: list | None = None

        # Follow-up chat turns only
        self._chat_turns: list = []          # list of insightgen_pb2.ChatTurn
        self._chat_contents: list = []       # list of gtypes.Content (mirrors _chat_turns)

        # Gemini CachedContent name for the initial context
        self._gemini_cache_name: str = ""

        self.created_at = time.time()
        self.last_used = time.time()

        self._load_chat_history()

    # ── Disk persistence ───────────────────────────────────────────────────────

    def _load_chat_history(self):
        """Load existing chat turns from <name>.chat.pb if present."""
        if not self._chat_path.exists():
            return
        try:
            ch = insightgen_pb2.ChatHistory()
            ch.ParseFromString(self._chat_path.read_bytes())
            self._chat_turns = list(ch.turns)
            self._gemini_cache_name = ch.gemini_cache_name or ""
            # Rebuild gtypes.Content list from stored turns
            for turn in self._chat_turns:
                self._chat_contents.append(gtypes.Content(
                    role=turn.role,
                    parts=[gtypes.Part(text=turn.text)],
                ))
            logger.info(
                "Loaded %d chat turns for %s (cache_name=%r)",
                len(self._chat_turns), self._file_name,
                self._gemini_cache_name or "<none>",
            )
        except Exception as exc:
            logger.warning("Failed to load chat history for %s: %s", self._file_name, exc)
            self._chat_turns = []
            self._chat_contents = []
            self._gemini_cache_name = ""

    def _save_chat_history(self):
        """Persist current chat turns and cache name to <name>.chat.pb."""
        try:
            ch = insightgen_pb2.ChatHistory()
            ch.file_name = self._file_name
            for turn in self._chat_turns:
                ch.turns.append(turn)
            ch.gemini_cache_name = self._gemini_cache_name
            self._chat_path.write_bytes(ch.SerializeToString())
        except Exception as exc:
            logger.error("Failed to save chat history for %s: %s", self._file_name, exc)

    # ── Initial context reconstruction ────────────────────────────────────────

    def _build_initial_contents(self) -> list:
        """Reconstruct Gemini conversation history from stored GensparkResponse."""
        if self._initial_contents is not None:
            return self._initial_contents

        raw = self._genspark_path.read_bytes()
        full = insightgen_pb2.GensparkResponse()
        full.ParseFromString(raw)

        prompt_file = self._gemini_config.get("prompt_file", "prompt.md")
        prompt_path = _server_root / "genspark" / prompt_file
        prompt_text = prompt_path.read_text("utf-8") if prompt_path.exists() else ""

        contents: list = []

        contents.append(gtypes.Content(
            role="user",
            parts=[gtypes.Part(text=(
                f"[Video analysis of recording {self._file_name}]\n\n{prompt_text}"
            ))],
        ))

        for turn in full.turns:
            model_parts = []
            if turn.text:
                model_parts.append(gtypes.Part(text=turn.text))
            for tc in turn.tool_calls:
                try:
                    args = json.loads(tc.arguments_json)
                except (json.JSONDecodeError, ValueError):
                    args = {}
                model_parts.append(gtypes.Part(
                    function_call=gtypes.FunctionCall(name=tc.tool_name, args=args),
                ))
            if model_parts:
                contents.append(gtypes.Content(role="model", parts=model_parts))
            if turn.tool_calls:
                fr_parts = [
                    gtypes.Part(
                        function_response=gtypes.FunctionResponse(
                            name=tc.tool_name,
                            response={"result": tc.result},
                        )
                    )
                    for tc in turn.tool_calls
                ]
                contents.append(gtypes.Content(role="user", parts=fr_parts))

        if full.HasField("summary") and full.summary.text:
            summary_md = f"## {full.summary.title}\n\n{full.summary.text}"
            if full.summary.parameters:
                summary_md += "\n\n| Parameter | Value | Unit |\n|---|---|---|\n"
                for p in full.summary.parameters:
                    summary_md += f"| {p.name} | {p.value} | {p.unit} |\n"
            contents.append(gtypes.Content(
                role="model",
                parts=[gtypes.Part(text=summary_md)],
            ))

        self._initial_contents = contents
        logger.info("Built %d initial content entries for %s", len(contents), self._file_name)
        return contents

    # ── Gemini CachedContent ───────────────────────────────────────────────────

    def _get_or_create_cache(self, client: genai.Client, model: str) -> str | None:
        """
        Try to use an existing Gemini CachedContent for the initial context.
        Creates one if it doesn't exist or has expired.
        Returns the cache name, or None if caching is unavailable/unsupported.
        """
        if self._gemini_cache_name:
            try:
                client.caches.get(name=self._gemini_cache_name)
                logger.debug("Reusing Gemini cache %s for %s", self._gemini_cache_name, self._file_name)
                return self._gemini_cache_name
            except Exception:
                logger.info("Gemini cache %s expired or invalid for %s — recreating",
                            self._gemini_cache_name, self._file_name)
                self._gemini_cache_name = ""

        initial = self._build_initial_contents()
        try:
            cache = client.caches.create(
                model=f"models/{model}",
                config=gtypes.CreateCachedContentConfig(
                    contents=initial,
                    ttl=datetime.timedelta(hours=24),
                ),
            )
            self._gemini_cache_name = cache.name
            self._save_chat_history()
            logger.info("Created Gemini cache %s for %s", cache.name, self._file_name)
            return cache.name
        except Exception as exc:
            logger.info("Gemini caching unavailable for %s (%s) — using full context", self._file_name, exc)
            return None

    # ── Send message ───────────────────────────────────────────────────────────

    def send_message(self, user_message: str) -> str:
        """Send a user message and return Gemini's text response."""
        self.last_used = time.time()

        api_key = os.environ.get(self._gemini_config.get("api_key_env", "GEMINI_API_KEY"))
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        client = genai.Client(api_key=api_key)
        model = self._gemini_config.get("model", "gemini-2.5-flash")

        # Append new user turn to both the proto list and contents list
        user_turn = insightgen_pb2.ChatTurn(
            role="user",
            text=user_message,
            timestamp_ns=time.time_ns(),
        )
        self._chat_turns.append(user_turn)
        self._chat_contents.append(gtypes.Content(
            role="user",
            parts=[gtypes.Part(text=user_message)],
        ))

        # Try cached context path first
        cache_name = self._get_or_create_cache(client, model)

        if cache_name:
            response = client.models.generate_content(
                model=model,
                contents=self._chat_contents,
                config=gtypes.GenerateContentConfig(cached_content=cache_name),
            )
        else:
            # Fallback: send full initial context + chat turns together
            full_contents = self._build_initial_contents() + self._chat_contents
            response = client.models.generate_content(
                model=model,
                contents=full_contents,
            )

        response_text = response.text or ""

        # Append model turn
        model_turn = insightgen_pb2.ChatTurn(
            role="model",
            text=response_text,
            timestamp_ns=time.time_ns(),
        )
        self._chat_turns.append(model_turn)
        self._chat_contents.append(gtypes.Content(
            role="model",
            parts=[gtypes.Part(text=response_text)],
        ))

        self._save_chat_history()
        return response_text


class ChatManager:
    """Manages chat sessions with TTL-based cleanup."""

    def __init__(self, gemini_config: dict, recordings_dir: Path):
        self._sessions: dict[str, ChatSession] = {}
        self._gemini_config = gemini_config
        self._recordings_dir = recordings_dir

    def _cleanup(self):
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - s.last_used > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("Cleaned up %d expired chat sessions", len(expired))

    def handle_message(
        self,
        genspark_path: Path,
        file_name: str,
        message: str,
        session_id: str | None = None,
    ) -> tuple[str, str]:
        """
        Process a user message. Returns (response_text, session_id).
        Creates a new session if session_id is missing or unknown.
        New sessions load existing chat history from disk automatically.
        """
        self._cleanup()

        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
        else:
            session_id = str(uuid.uuid4())
            chat_path = self._recordings_dir / f"{file_name}.chat.pb"
            session = ChatSession(genspark_path, chat_path, file_name, self._gemini_config)
            self._sessions[session_id] = session

        response_text = session.send_message(message)
        return response_text, session_id
