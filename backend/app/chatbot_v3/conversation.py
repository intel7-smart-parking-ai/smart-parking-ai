"""Bounded in-memory conversation prototype. Reconstructed, not a recovered binary."""
from copy import deepcopy
import json
from pathlib import Path
import re
from threading import RLock
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .llm import ModelError
from .recommendation import recommend_spaces


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: Literal["availability", "vehicle", "fee", "facilities", "guidance", "recommend", "clarify", "unsupported"]
    lot_id: Literal["A", "B", "C", "D"] | None
    lookup_type: Literal["sticker", "plate_suffix"] | None
    vehicle_value: str | None
    destination_id: str | None
    space_type: Literal["general", "ev", "accessible"] | None
    preference: Literal["nearest", "easy"]
    question: str | None


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fact_ids: list[str] = Field(max_length=20)


class ConversationService:
    def __init__(self, model, tools, knowledge, *, ttl=1800, capacity=100,
                 max_requests=1000, clock=time.monotonic):
        self.model, self.tools, self.knowledge = model, tools, knowledge
        self.ttl, self.capacity, self.max_requests = ttl, capacity, max_requests
        self.clock = clock
        self._lock = RLock()
        self._states, self._requests = {}, {}
        prompt_dir = Path(__file__).with_name("prompts")
        self.coordinator_prompt = (prompt_dir / "coordinator.md").read_text(encoding="utf-8")
        self.reporter_prompt = (prompt_dir / "reporter.md").read_text(encoding="utf-8")
        self.catalog = knowledge.get_place_catalog().data
        self.names = {row["lot_id"]: row["name"] for row in self.catalog["items"]}
        self.destinations = {
            lot: knowledge.get_facilities(lot).data.get("items", []) for lot in self.names
        }

    def handle(self, conversation_id: str, request_id: str, text: str) -> dict:
        if not all(isinstance(v, str) and 1 <= len(v) <= 128 for v in (conversation_id, request_id)):
            raise ValueError("대화 ID와 요청 ID는 1~128자 문자열이어야 합니다.")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("질문은 1~2000자여야 합니다.")
        with self._lock:
            key = (conversation_id, request_id)
            if key in self._requests:
                prior_text, prior_reply = self._requests[key]
                if prior_text != text:
                    raise ValueError("같은 요청 ID로 질문을 바꿀 수 없습니다.")
                return deepcopy(prior_reply)
            if len(self._requests) >= self.max_requests:
                raise ValueError("요청 보관 한도에 도달했습니다. 관리자에게 문의하세요.")
            now = self.clock()
            self._states = {k: v for k, v in self._states.items() if now - v["at"] < self.ttl}
            if conversation_id not in self._states and len(self._states) >= self.capacity:
                raise ValueError("대화 보관 한도에 도달했습니다.")
            state = self._states.setdefault(conversation_id, {"at": now, "context": {}, "history": []})
            reply = {"status": "error", "text": "", "cards": [], "source_refs": [],
                     "model_calls": 0, "usage": [], "is_mock": None, "retrieved_at": None}

            def infer(prompt, payload, schema):
                reply["model_calls"] += 1
                result = self.model.structured(prompt, payload, schema)
                reply["usage"].append(deepcopy(getattr(self.model, "last_usage", {})))
                return result

            try:
                decision = infer(self.coordinator_prompt, {
                    "question": text, "confirmed_context": deepcopy(state["context"]),
                    "recent_dialogue": state["history"][-6:], "catalog": self.catalog,
                    "destinations": self.destinations,
                }, Decision)
                if decision.intent in {"clarify", "unsupported"}:
                    reply.update(status=decision.intent, text=decision.question or "주차장이나 조회할 내용을 조금 더 알려주세요.")
                else:
                    self._validate(decision, text, state["context"])
                    result = self._dispatch(decision, conversation_id, request_id)
                    reply["source_refs"] = result.source_refs
                    reply["is_mock"] = result.is_mock
                    reply["retrieved_at"] = result.retrieved_at.isoformat()
                    if result.status != "ok":
                        reply.update(status="partial", text=(result.error.message if result.error
                            else "조회 결과를 확인하지 못했습니다. 조건을 다시 확인해주세요."))
                    else:
                        facts = self._facts(decision, result.data or {})
                        data = result.data or {}
                        mock_rows = data.get("space_sources", data.get("items", []))
                        if decision.intent in {"availability", "vehicle", "fee", "recommend"} and (
                            result.is_mock is True or data.get("source") in {"simulation", "manual"}
                            or any(r.get("is_mock") is True or r.get("source") in {"simulation", "manual"} for r in mock_rows)
                        ):
                            facts.append({"id": "provenance", "text": "모의·시뮬레이션 데이터가 포함된 조회 결과입니다.", "required": True})
                        selected = facts
                        try:
                            selection = infer(self.reporter_prompt, {"question": text, "facts": facts}, Selection)
                            by_id = {f["id"]: f for f in facts}
                            ids = selection.fact_ids
                            if len(ids) != len(set(ids)) or set(ids) != set(by_id):
                                raise ValueError("missing or fabricated facts")
                            selected = [by_id[i] for i in ids]
                        except (ModelError, ValueError):
                            reply["warning"] = "표현 정리 실패로 검증된 사실을 기본 순서로 표시합니다."
                        reply.update(status="ok", text="\n".join(f["text"] for f in selected),
                                     cards=[{"type": decision.intent, "data": result.data}])
                        self._remember(state["context"], decision, result.data or {})
            except ModelError:
                reply.update(status="unavailable", text="모델 서비스 응답을 확인하지 못했습니다. 잠시 후 다시 시도해주세요.")
            except ValueError:
                reply.update(status="clarify", text="질문을 확실하게 해석하지 못했습니다. 주차장과 조회할 내용을 구체적으로 알려주세요.")
            state["at"] = self.clock()
            state["history"] = (state["history"] + [
                {"role": "user", "content": text}, {"role": "assistant", "content": reply["text"]}
            ])[-6:]
            self._requests[key] = (text, deepcopy(reply))
            return reply

    def _validate(self, d: Decision, text: str, context: dict):
        if (d.intent in {"facilities", "guidance", "recommend"} or d.space_type is not None) and not d.lot_id:
            raise ValueError("missing lot")
        if d.intent not in {"vehicle", "fee"} and (d.lookup_type is not None or d.vehicle_value is not None):
            raise ValueError("irrelevant vehicle fields")
        if d.intent in {"vehicle", "fee"}:
            pattern = r"[1-9][0-9]" if d.lookup_type == "sticker" else r"[0-9]{4}"
            if d.lookup_type is None or d.vehicle_value is None or not re.fullmatch(pattern, d.vehicle_value):
                raise ValueError("missing vehicle")
            mentioned = re.search(r"(?<![0-9])" + re.escape(d.vehicle_value) + r"(?![0-9])", text)
            reused = (context.get("lookup_type") == d.lookup_type and context.get("vehicle_value") == d.vehicle_value)
            explicit_numbers = re.findall(r"(?<![0-9])[0-9]{2,4}(?![0-9])", text)
            if not mentioned and explicit_numbers:
                raise ValueError("new identifier must not fall back to previous vehicle")
            if not mentioned and not reused:
                raise ValueError("invented vehicle")
        if d.intent == "recommend":
            ids = {f.get("facility_id", f.get("id")) for f in self.destinations[d.lot_id]}
            if d.destination_id not in ids or d.space_type not in (None, "general"):
                raise ValueError("unsupported destination or type")

    def _dispatch(self, d, cid, rid):
        if d.intent == "availability":
            return self.tools.list_spaces(d.lot_id) if d.lot_id else self.tools.list_lots()
        if d.intent in {"vehicle", "fee"}:
            location = self.tools.find_vehicle(d.lookup_type, d.vehicle_value)
            if d.intent == "vehicle" or location.status != "ok":
                return location
            if location.data.get("status") != "occupied":
                raise ValueError("uncertain vehicle must be clarified before requesting fee")
            session = (location.data or {}).get("session_id")
            if not session:
                raise ValueError("missing session")
            fee = self.tools.get_fee(session, request_id=json.dumps([cid, rid], ensure_ascii=False))
            fee.source_refs = location.source_refs + fee.source_refs
            if location.is_mock is True or location.data.get("source") in {"simulation", "manual"}:
                fee.is_mock = True
            return fee
        if d.intent == "facilities":
            return self.knowledge.get_facilities(d.lot_id)
        if d.intent == "guidance":
            return self.knowledge.get_lot_guidance(d.lot_id)
        return recommend_spaces(self.tools, self.knowledge, d.lot_id, d.destination_id,
                                preference=d.preference)

    @staticmethod
    def _remember(context, d, data):
        lot_id = data.get("lot_id") if d.intent in {"vehicle", "fee"} else d.lot_id
        if lot_id:
            if context.get("lot_id") != lot_id:
                context.pop("destination_id", None)
            context["lot_id"] = lot_id
        if d.intent in {"vehicle", "fee"} and d.lookup_type and d.vehicle_value:
            context.update(lookup_type=d.lookup_type, vehicle_value=d.vehicle_value)
        if d.intent == "recommend" and d.destination_id:
            context["destination_id"] = d.destination_id

    def _facts(self, d, data):
        lines = []
        name = self.names.get(d.lot_id, "주차장")
        if d.intent == "availability":
            if d.lot_id:
                if data.get("next_cursor") is not None:
                    lines.append(f"{name}: 조회가 일부만 반환되어 전체 빈자리 수를 확정할 수 없습니다.")
                else:
                    count = sum(s["status"] == "empty" and (d.space_type is None or s["space_type"] == d.space_type)
                                for s in data["items"])
                    kind = {"ev": "전기차 전용", "accessible": "장애인 전용", "general": "일반"}.get(d.space_type, "전체")
                    lines.append(f"{name}의 {kind} 빈자리는 조회 시점 기준 {count}칸입니다.")
                if d.space_type == "ev":
                    lines.append("전기차 칸의 빈자리이며, 충전기 고장·사용 가능 여부는 이 조회로 확인할 수 없습니다.")
            else:
                lines.extend(f"{s['name']}: 빈자리 {s['empty_spaces']} / 전체 {s['total_spaces']}칸" for s in data["items"])
            lines.append(f"조회 기준 시각: {data.get('as_of', '확인 불가')}. 도착 시 상태가 달라질 수 있습니다.")
        elif d.intent == "vehicle":
            place = f"{self.names.get(data['lot_id'], data['lot_id'])} {data['space_id']}"
            if data['status'] == 'uncertain':
                lines.append(f"조회 차량의 마지막 위치 정보는 {place}이지만 현재 위치는 불확실합니다. 현장 확인이 필요합니다.")
            else:
                lines.append(f"조회 차량은 {place}에서 확인됩니다.")
            lines.append(f"상태: {data['status']}, 관측 시각: {data['observed_at']}.")
        elif d.intent == "fee":
            lines.append(f"서버 조회 요금: {data['amount_krw']:,}원. 결제 상태: {data['payment_status']}.")
            lines.append(f"요금 기준 시각: {data['target_at']}, 견적 만료: {data['quote_expires_at']}.")
            if data.get("payment_blocked"):
                lines.append("현재 서버에서 결제를 제한하고 있습니다.")
        elif d.intent == "facilities":
            lines.append(f"{name}의 프로젝트 가상 설정입니다. 실제 장소의 시설 정보로 확인된 내용이 아닙니다.")
            for f in data.get("items", []):
                lines.append(f"{f['name']}: {f.get('description', '')} (인접 칸: {', '.join(f.get('nearby_space_ids', [])) or '미정'})")
        elif d.intent == "guidance":
            lines.append(f"{name}의 프로젝트 가상 설정입니다. 실제 운영 정보가 아닙니다.")
            lines.append(f"구조: {data.get('environment', '미정')}, 층: {data.get('floor', '미정')}.")
            for key, label in [("operating_hours", "운영시간"), ("height_limit_m", "높이제한"),
                               ("parking_discount", "할인"), ("sprinkler", "스프링클러")]:
                value = data.get(key)
                lines.append(f"{label}: {value if value is not None else '확인된 정보 없음'}")
            lines.append("대피시설과 안전한 이동 경로는 실시간 검증되지 않았습니다. 현장 안내를 확인하세요.")
        else:
            lines.append(f"{name}: 프로젝트 가상 시설 설정과 서버 빈자리 조회를 조합한 추천입니다.")
            for s in data.get("items", []):
                lines.append(f"{s['space_id']}: {s.get('reason', '')} {' / '.join(s.get('features', []))}")
            if not data.get("items"):
                lines.append("현재 조건에서 추천할 수 있는 빈자리를 확인하지 못했습니다.")
            lines.append(data.get("notice") or "예약이 아니며 도착 시 상태가 달라질 수 있습니다.")
        return [{"id": f"f{i}", "text": line, "required": True} for i, line in enumerate(lines)]
