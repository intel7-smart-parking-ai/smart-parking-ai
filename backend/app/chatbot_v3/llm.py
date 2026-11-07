"""OpenRouter structured-output adapter reconstructed from the recovery record."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import TypeVar

import httpx
from dotenv import dotenv_values
from pydantic import BaseModel
from .budget import remaining_timeout


class ModelError(RuntimeError):
    """A safe message without provider bodies or credentials."""


@dataclass(frozen=True)
class ModelSettings:
    api_key: str = field(repr=False)
    model: str
    timeout: float = 8.0
    max_output_tokens: int = 700

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "ModelSettings":
        values = dict(dotenv_values(env_file)) if env_file is not None else {}
        values.update(os.environ)
        key = (values.get("OPENROUTER_API_KEY") or "").strip()
        selected = (values.get("OPENROUTER_MODEL") or "").strip()
        legacy = (values.get("LLM_MODEL") or "").strip()
        if selected and legacy and selected != legacy:
            raise ModelError("OPENROUTER_MODEL과 LLM_MODEL 설정이 서로 다릅니다.")
        if not key or not (selected or legacy):
            raise ModelError("OPENROUTER_API_KEY와 OPENROUTER_MODEL 설정이 필요합니다.")
        return cls(api_key=key, model=selected or legacy)


T = TypeVar("T", bound=BaseModel)


class OpenRouterModel:
    def __init__(self, client: httpx.Client, settings: ModelSettings):
        self.client = client
        self.settings = settings
        self.last_usage: dict = {}

    def structured(self, system: str, payload: dict, output_model: type[T]) -> T:
        self.last_usage = {}
        try:
            response = self.client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                json={
                    "model": self.settings.model,
                    "temperature": 0,
                    "max_tokens": self.settings.max_output_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    "tools": [{"type": "function", "function": {
                        "name": "submit_result", "description": "Return the structured result.",
                        "parameters": output_model.model_json_schema(),
                    }}],
                    "tool_choice": {"type": "function", "function": {"name": "submit_result"}},
                },
                timeout=remaining_timeout(self.settings.timeout),
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise ModelError("모델 연결에 실패했습니다. 자동 재시도하지 않았습니다.") from exc
        if response.status_code != 200:
            raise ModelError(f"모델 요청 실패 (HTTP {response.status_code}).")
        try:
            body = response.json()
            calls = body["choices"][0]["message"]["tool_calls"]
            if len(calls) != 1 or calls[0]["function"]["name"] != "submit_result":
                raise ValueError("invalid tool output")
            result = output_model.model_validate_json(calls[0]["function"]["arguments"])
            usage = body.get("usage") or {}
            self.last_usage = {k: v for k, v in usage.items()
                               if k in {"prompt_tokens", "completion_tokens", "total_tokens", "cost"}
                               and isinstance(v, (int, float)) and not isinstance(v, bool)}
            return result
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ModelError("모델 응답 형식을 확인할 수 없습니다.") from exc
