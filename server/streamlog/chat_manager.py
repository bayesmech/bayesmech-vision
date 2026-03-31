"""
chat_manager.py — Manages follow-up chat sessions with Gemini.

Each ChatSession reconstructs the original Gemini conversation from a
.genspark.pb file (turns + summary), then allows appending user messages
and getting Gemini responses while maintaining full conversation context.
"""

from __future__ import annotations

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

# Load .env from project root if present (for API keys not in the server's environment)
from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

from proto import insightgen_pb2

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 3600  # 1 hour


class ChatSession:
    """A single follow-up conversation for one recording."""

    def __init__(self, genspark_path: Path, file_name: str, gemini_config: dict):
        self._genspark_path = genspark_path
        self._file_name = file_name
        self._gemini_config = gemini_config
        self._contents: list | None = None
        self.created_at = time.time()
        self.last_used = time.time()

    def _build_initial_contents(self):
        """Reconstruct Gemini conversation history from stored GensparkResponse."""
        raw = self._genspark_path.read_bytes()
        full = insightgen_pb2.GensparkResponse()
        full.ParseFromString(raw)

        prompt_file = self._gemini_config.get("prompt_file", "prompt.md")
        prompt_path = _server_root / "genspark" / prompt_file
        prompt_text = prompt_path.read_text("utf-8") if prompt_path.exists() else ""

        contents: list = []

        # Original prompt as first user message
        contents.append(gtypes.Content(
            role="user",
            parts=[gtypes.Part(text=(
                f"[Video analysis of recording {self._file_name}]\n\n{prompt_text}"
            ))],
        ))

        # Reconstruct each turn
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
                    function_call=gtypes.FunctionCall(
                        name=tc.tool_name, args=args,
                    ),
                ))

            if model_parts:
                contents.append(gtypes.Content(role="model", parts=model_parts))

            # Tool responses go back as user content
            if turn.tool_calls:
                fr_parts = []
                for tc in turn.tool_calls:
                    fr_parts.append(gtypes.Part(
                        function_response=gtypes.FunctionResponse(
                            name=tc.tool_name,
                            response={"result": tc.result},
                        ),
                    ))
                contents.append(gtypes.Content(role="user", parts=fr_parts))

        # Append summary as final model message so Gemini knows what the user saw
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

        self._contents = contents
        logger.info(
            "ChatSession built %d content entries for %s",
            len(contents), self._file_name,
        )

    def send_message(self, user_message: str) -> str:
        """Send a user message and return Gemini's text response."""
        if self._contents is None:
            self._build_initial_contents()

        self.last_used = time.time()

        self._contents.append(gtypes.Content(
            role="user",
            parts=[gtypes.Part(text=user_message)],
        ))

        api_key = os.environ.get(self._gemini_config.get("api_key_env", "GEMINI_API_KEY"))
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        client = genai.Client(api_key=api_key)
        model = self._gemini_config.get("model", "gemini-2.5-flash")

        response = client.models.generate_content(
            model=model,
            contents=self._contents,
        )

        response_text = response.text or ""

        self._contents.append(gtypes.Content(
            role="model",
            parts=[gtypes.Part(text=response_text)],
        ))

        return response_text


class ChatManager:
    """Manages chat sessions with TTL-based cleanup."""

    def __init__(self, gemini_config: dict):
        self._sessions: dict[str, ChatSession] = {}
        self._gemini_config = gemini_config

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
        """
        self._cleanup()

        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
        else:
            session_id = str(uuid.uuid4())
            session = ChatSession(genspark_path, file_name, self._gemini_config)
            self._sessions[session_id] = session

        response_text = session.send_message(message)
        return response_text, session_id
