"""Versioned prompts for the conversation layer."""

from pathlib import Path


def conversation_prompt() -> str:
    return Path(__file__).with_name("conversation.md").read_text(encoding="utf-8")
