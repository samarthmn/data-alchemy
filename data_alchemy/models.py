"""Ollama model discovery and the chat client used for generation.

DataAlchemy talks to a local Ollama server through its OpenAI-compatible API. Listing the
installed models and choosing a default happens once at startup, separate from the app
logic, and produces a single :class:`OllamaModels` bundle.
"""

import os
from dataclasses import dataclass

import ollama
from openai import OpenAI

# Picked as the default when the local server has it installed.
PREFERRED_DEFAULT_MODEL = "gpt-oss:20b"


@dataclass
class OllamaModels:
    """The chat client plus the locally available model names and the default choice."""

    client: OpenAI
    names: list[str]
    default: str


def load_models() -> OllamaModels:
    """Connect to the local Ollama server and list installed models. Call once at startup."""
    host = os.getenv("OLLAMA_HOST")
    ollama_client = ollama.Client(host=host)
    chat_client = OpenAI(base_url=f"{host}/v1", api_key="ollama")

    names = [model.model for model in ollama_client.list().models]
    default = (
        PREFERRED_DEFAULT_MODEL
        if PREFERRED_DEFAULT_MODEL in names
        else (names[0] if names else PREFERRED_DEFAULT_MODEL)
    )
    return OllamaModels(client=chat_client, names=names, default=default)
