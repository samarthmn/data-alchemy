"""LLM JSON calls and extraction of explicitly-stated requirements from user messages."""

import json
import re

from openai import OpenAI

# Generous so a large multi-table spec is never truncated mid-JSON.
GENERATION_MAX_TOKENS = 8192


def call_llm_json(client: OpenAI, messages: list[dict], model: str) -> dict:
    """Run a chat completion expecting JSON and parse it, tolerating fences and prose."""
    result = (
        client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=GENERATION_MAX_TOKENS,
        )
        .choices[0]
        .message.content
    )
    text = (result or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def required_file_count(complexity: str | None) -> int:
    """Minimum number of related tables expected for a given complexity."""
    return {"Simple": 1, "Medium": 2, "Hard": 3}.get(complexity, 1)


# --- Explicit requirement extraction ------------------------------------------------
# These read only what the user literally wrote; the LLM checker handles topic and any
# looser phrasing. We never infer these three fields from the topic alone.


def explicit_complexity(message: str) -> str | None:
    match = re.search(r"\b(simple|medium|hard)\b", message, re.IGNORECASE)
    return match.group(1).title() if match else None


def explicit_format(message: str) -> str | None:
    match = re.search(r"\b(csv|json|markdown|md)\b", message, re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).lower()
    return "Markdown" if value in {"markdown", "md"} else value.upper()


def explicit_rows(message: str) -> int | None:
    match = re.search(
        r"\b(\d+)\s*(rows?|items?|records?|entries|entry)\b", message, re.IGNORECASE
    )
    return int(match.group(1)) if match else None
