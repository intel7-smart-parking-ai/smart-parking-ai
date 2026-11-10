"""역할별 지시문(프롬프트). AI를 부르는 역할 하나에 지시문 파일 하나.

| 역할 | 파일 | 쓰는 곳 | 도구 | 결과 검사 |
|---|---|---|---|---|
| 대화 AI | conversation.md | agents/conversation.py | 읽기 도구(tools/read.py) | agents/evidence.py 근거 검사 → 한 번 고쳐 쓰기 |
| 질문 번역 | translate_question.md | response_locale._question_to_korean | 없음 | 숫자가 바뀌면 원문 사용 |
| 답 번역 | translate_answer.md | response_locale._translate | 없음 | 자리표시(__NAME0__ 등) 복원, 개수 확인 |
| 질문 분류(예전 방식) | intent_classifier.md | llm.py (LLM_ENGINE=direct일 때만) | 없음 | 허용 intent 목록 검사 |

각 지시문의 버전(내용 해시 8자리)은 모니터링 기록(metadata.prompts)에 남는다.
지시문을 고치면 버전이 바뀌므로, 답이 달라진 때를 지시문 변경과 맞춰 볼 수 있다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_DIR = Path(__file__).parent

ROLES = {
    "conversation": "conversation.md",
    "translate_question": "translate_question.md",
    "translate_answer": "translate_answer.md",
    "intent_classifier": "intent_classifier.md",
}


def load(role: str) -> str:
    """역할의 지시문 원문(파일 끝 줄바꿈 제외). 매번 읽으므로 파일을 고치면 서버를 다시 켜지 않아도 반영된다."""
    return (_DIR / ROLES[role]).read_text(encoding="utf-8").rstrip("\n")


def render(role: str, **values) -> str:
    """{name} 자리를 채운 지시문. 지시문 안의 JSON 중괄호는 파일에 {{ }}로 적는다."""
    return load(role).format(**values)


def version(role: str) -> str:
    return hashlib.sha256(load(role).encode("utf-8")).hexdigest()[:8]


def versions() -> dict:
    return {role: version(role) for role in ROLES}


def conversation_prompt() -> str:
    return load("conversation")
