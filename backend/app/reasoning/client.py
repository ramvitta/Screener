"""Shared OpenAI-compatible /chat/completions seam.

Both the "reason for trade" generator and the daily-byte analyzer talk to the
same provider-neutral endpoint, so the httpx plumbing (base_url, auth header,
timeout, response parsing, optional JSON mode) lives here in one place.
Provider-neutral: any OpenAI-compatible /chat/completions endpoint works
(Ollama cloud, OpenAI, Groq, OpenRouter, local Ollama, ...).
"""

import logging
from typing import Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def chat_completion(
    messages: List[Dict[str, str]],
    model: str,
    *,
    max_tokens: int = 500,
    json_mode: bool = False,
    timeout: Optional[float] = None,
) -> Optional[str]:
    """One OpenAI-compatible /chat/completions call; returns the assistant text
    (or the raw JSON string in json_mode) or None on empty/refusal. Raises
    httpx.HTTPError on transport/status failures (callers decide how to degrade)."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if json_mode:
        # Most OpenAI-compatible providers honour this; the fallback (asking
        # for JSON in the prompt) is handled by callers that need it.
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(
        base_url=settings.llm_api_base_url,
        timeout=timeout or settings.llm_reason_timeout_seconds,
    ) as client:
        response = await client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str):
        return None
    return content.strip() or None
