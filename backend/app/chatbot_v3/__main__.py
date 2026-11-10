"""Opt-in paid-model console; does not replace the existing HTTP chat route."""
import argparse
import os
import uuid

import httpx

from .conversation import ConversationService
from .knowledge import KnowledgeStore
from .llm import ModelError, ModelSettings, OpenRouterModel
from .tools import ParkingTools


def main():
    parser = argparse.ArgumentParser(description="PARK:ON chatbot v3 console")
    parser.add_argument("--env-file", help="명시적으로 읽을 .env 파일 경로")
    parser.add_argument("--backend-url", default=os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    try:
        settings = ModelSettings.load(args.env_file)
    except ModelError as exc:
        parser.exit(2, f"{exc}\n")
    with httpx.Client() as model_client, httpx.Client(base_url=args.backend_url) as api_client:
        service = ConversationService(OpenRouterModel(model_client, settings), ParkingTools(api_client), KnowledgeStore())
        cid = str(uuid.uuid4())
        print("질문당 유료 모델 호출 최대 2회. /new 새 대화, /quit 종료. 요금 조회는 서버에 견적을 저장합니다.")
        while True:
            try:
                question = input("질문> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question == "/quit":
                break
            if question == "/new":
                cid = str(uuid.uuid4())
                continue
            if not question:
                continue
            try:
                print(service.handle(cid, str(uuid.uuid4()), question)["text"])
            except ValueError as exc:
                print(str(exc))


if __name__ == "__main__":
    main()
