import time
import uuid
import datetime
from typing import Dict, Any, Optional, List
from .catalog import LOTS_CONFIG
from .parser import parse
from .adapters import SmartAdapter
from .session_flow import SessionFlow
from .eval_qa import handle_eval_query
from .templates import (
    empty_spots_template, car_location_template, fee_template,
    fee_rule_template, payment_confirm_template, payment_receipt_template,
    alert_template, alert_resolved_template, admin_help_template, call_admin_template,
    exit_help_template, violations_template, help_template, fallback_template,
    charging_time_template, nearest_zone_template, facility_template,
    flight_congestion_template, ev_charging_template, view_map_template,
    welcome_template
)

class ChatEngine:
    def __init__(self, adapter: Optional[SmartAdapter] = None):
        self.adapter = adapter or SmartAdapter()
        self.flow_manager = SessionFlow()
        self.runtime_id = uuid.uuid4().hex

    def handle(
        self,
        session_id: str,
        text: Optional[str] = None,
        chip_id: Optional[str] = None,
        request_id: Optional[str] = None,
        vehicle_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        start_time = time.time()
        
        # 0. 멱등성 검사 (동일한 request_id 재전송 방지)
        if request_id:
            cached = self.flow_manager.get_idempotent_response(request_id)
            if cached:
                return cached
                
        session = self.flow_manager.get_session(session_id)
        
        # vehicle_context 복원/갱신
        if vehicle_context and vehicle_context.get("car_id"):
            session["car_id"] = str(vehicle_context["car_id"])

        session["message_count"] = session.get("message_count", 0) + 1

        eval_resp = None
        if not chip_id and text:
            eval_resp = handle_eval_query(
                text,
                session=session,
                adapter=self.adapter,
                flow_manager=self.flow_manager
            )

        if eval_resp:
            result = eval_resp
            intent = eval_resp.get("meta", {}).get("intent", "eval_qa")
            source = "eval_qa"
        elif chip_id:
            intent, params = self._interpret_chip(chip_id, session)
            source = "chip"
            # 세션 컨텍스트 업데이트
            if params.get("car_id"):
                session["car_id"] = params["car_id"]
            if params.get("lot"):
                session["lot_id"] = params["lot"]
            result = self._dispatch(intent, params, session)
        else:
            raw_text = text or ""
            # 파서 호출
            parse_res = parse(raw_text, context={"car_id": session.get("car_id"), "lot": session.get("lot_id")})
            intent = parse_res["intent"]
            params = parse_res["params"]
            source = parse_res.get("source", "rule")
            
            # 세션 컨텍스트 업데이트
            if params.get("car_id"):
                session["car_id"] = params["car_id"]
            if params.get("lot"):
                session["lot_id"] = params["lot"]

            # 2. 핸들러 디스패치
            result = self._dispatch(intent, params, session)
        
        latency_ms = round((time.time() - start_time) * 1000, 2)
        
        # 3. 응답 봉투(Envelope) 조립
        response = {
            "text": result["text"],
            "card": result.get("card"),
            "chips": result.get("chips", []),
            "banner": result.get("banner"),
            "safety": result.get("safety"),
            "confirmation_token": result.get("confirmation_token"),
            "flow": session.get("flow", "idle"),
            "runtime_id": self.runtime_id,
            "state_version": session.get("state_version", 1),
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "vehicle_context": {
                "car_id": session.get("car_id"),
                "state": "selected" if session.get("car_id") else "unselected"
            },
            "catalog": {
                "lots": [
                    {
                        "lot_id": l["lot_id"],
                        "name": l["name"],
                        "kind": l["kind"],
                        "capacity": l["capacity"],
                        "spaces": l["spaces"]
                    }
                    for l in LOTS_CONFIG.values()
                ],
                "zones": [
                    {"key": "general", "name": "일반"},
                    {"key": "ev", "name": "전기차"},
                    {"key": "accessible", "name": "장애인"}
                ],
                "source": "backend"
            },
            "meta": {
                "intent": intent,
                "source": source,
                "latency_ms": latency_ms
            }
        }
        
        if request_id:
            self.flow_manager.set_idempotent_response(request_id, response)
            
        return response

    def _interpret_chip(self, chip_id: str, session: Dict[str, Any]) -> tuple:
        if chip_id == "empty_all":
            return "get_empty_spots", {}
        if chip_id.startswith("lot:"):
            return "get_empty_spots", {"lot": chip_id.split(":")[1]}
        if chip_id.startswith("zone:"):
            return "get_empty_spots", {"zone": chip_id.split(":")[1]}
        if chip_id.startswith("view_map:"):
            return "view_map", {"lot": chip_id.split(":")[1]}
        if chip_id == "ev_status":
            return "ev_charging_status", {}
        if chip_id.startswith("ev_lot:"):
            return "ev_charging_status", {"lot": chip_id.split(":")[1]}
        if chip_id == "find_car":
            return "find_my_car", {"car_id": session.get("car_id")}
        if chip_id.startswith("find_car_"):
            return "find_my_car", {"car_id": chip_id.replace("find_car_", "")}
        if chip_id == "fee":
            return "get_fee", {"car_id": session.get("car_id")}
        if chip_id.startswith("fee_car:"):
            return "get_fee", {"car_id": chip_id.split(":")[1]}
        if chip_id.startswith("fee_lot:"):
            return "fee_rule", {"lot": chip_id.split(":")[1]}
        if chip_id == "fee_rule":
            return "fee_rule", {"all_lots": True}
        if chip_id.startswith("pay_car:"):
            return "pay_fee", {"car_id": chip_id.split(":")[1]}
        if chip_id.startswith("confirm_pay:"):
            return "confirm_payment", {"token": chip_id.split(":")[1]}
        if chip_id == "call_admin":
            return "call_admin", {}
        if chip_id == "call_status":
            return "call_status", {}
        if chip_id == "exit_help":
            return "exit_help", {"lot": session.get("lot_id")}
        if chip_id in ["evacuate", "alert"]:
            return "evacuation_guide", {}
        if chip_id in ["fire_clear", "clear_alert", "alert_clear"]:
            return "clear_alert", {}
        if chip_id == "cancel_action":
            return "cancel_action", {}
        return "unknown", {}

    def _dispatch(self, intent: str, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        # 핸들러 함수 매핑
        handlers = {
            "get_empty_spots": self._h_get_empty_spots,
            "find_my_car": self._h_find_my_car,
            "get_fee": self._h_get_fee,
            "fee_rule": self._h_fee_rule,
            "pay_fee": self._h_pay_fee,
            "confirm_payment": self._h_confirm_payment,
            "cancel_action": self._h_cancel_action,
            "get_alerts": self._h_get_alerts,
            "alert_near_my_car": self._h_get_alerts,
            "evacuation_guide": self._h_evacuation_guide,
            "clear_alert": self._h_clear_alert,
            "fire_clear": self._h_clear_alert,
            "exit_help": self._h_exit_help,
            "get_violations": self._h_get_violations,
            "call_admin": self._h_call_admin,
            "call_status": self._h_call_status,
            "admin_help": self._h_admin_help,
            "payment_help": self._h_payment_help,
            "payment_issue": self._h_payment_issue,
            "charging_time_info": self._h_charging_time_info,
            "ev_charging_status": self._h_ev_charging_status,
            "view_map": self._h_view_map,
            "nearest_zone_guide": self._h_nearest_zone_guide,
            "facility_info": self._h_facility_info,
            "flight_congestion_info": self._h_flight_congestion_info,
            "welcome": self._h_welcome,
            "thanks": self._h_thanks,
            "help": self._h_help,
            "unknown": self._h_fallback
        }
        handler = handlers.get(intent, self._h_fallback)
        return handler(params, session)

    def _h_get_empty_spots(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot")
        zone = params.get("zone")
        rank = params.get("rank")
        data = self.adapter.get_empty_spots(lot_id=lot_id, zone=zone)
        return empty_spots_template(data, lot_id=lot_id, rank=rank, zone=zone)

    def _h_find_my_car(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        car_id = params.get("car_id") or session.get("car_id")
        if not car_id:
            return {
                "text": "차량 번호를 알려주세요! (예: '23번 차 어디 있어?', '1724번')",
                "card": None,
                "chips": [
                    {"id": "find_car_23", "label": "23번 차량 위치"},
                    {"id": "find_car_57", "label": "57번 차량 위치"},
                    {"id": "find_car_1724", "label": "1724번 차량 위치"}
                ]
            }
        car = self.adapter.find_car(car_id)
        if car:
            session["car_id"] = car["car_id"]
            session["lot_id"] = car["lot_id"]
        return car_location_template(car, car_id)

    def _h_get_fee(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        car_id = params.get("car_id") or session.get("car_id")
        if not car_id:
            return {
                "text": "요금을 조회할 차량 번호를 알려주세요. (예: '23번 요금 얼마야?')",
                "card": None,
                "chips": [
                    {"id": "fee_car:23", "label": "23번 차량 요금"},
                    {"id": "fee_car:57", "label": "57번 차량 요금"},
                    {"id": "fee_rule", "label": "주차 요금표 보기"}
                ]
            }
        fee_info = self.adapter.get_fee(car_id)
        if fee_info:
            session["car_id"] = fee_info["car_id"]
            session["lot_id"] = fee_info["lot_id"]
        return fee_template(fee_info, car_id)

    def _h_fee_rule(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        if params.get("all_lots"):
            return fee_rule_template(None)
        lot_id = params.get("lot")
        return fee_rule_template(lot_id)

    def _h_pay_fee(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        # 1. 비상 경보 체크
        alerts = self.adapter.get_alerts()
        for a in alerts:
            if a.get("level", 0) >= 2:
                return {
                    "text": "🚨 **[결제 불가 안내]** 비상 대피 경보가 발령 중이므로 결제 및 정산이 일시 중단되었습니다. 신속히 비상구로 대피하세요!",
                    "card": {"type": "alert", "data": a},
                    "chips": [{"id": "evacuate", "label": "비상 대피로 보기"}]
                }
                
        car_id = params.get("car_id") or session.get("car_id")
        if not car_id:
            return {
                "text": "결제하실 차량 번호를 먼저 입력해주세요.",
                "card": None,
                "chips": [{"id": "fee_car:23", "label": "23번 차량 정산"}]
            }
            
        fee_info = self.adapter.get_fee(car_id)
        if not fee_info:
            return {"text": f"차량({car_id})의 입차 기록을 찾을 수 없습니다.", "card": None, "chips": []}
            
        if fee_info.get("paid"):
            return {
                "text": f"**{fee_info['plate_number']}** 차량은 이미 정산 완료되었습니다. 추가 결제가 필요 없습니다.",
                "card": {"type": "fee", "data": fee_info},
                "chips": [{"id": "exit_help", "label": "출차 안내"}]
            }
            
        # 2단계 결제 확인 토큰 발급
        token = self.flow_manager.create_payment_token(
            session_id=session["session_id"],
            car_id=car_id,
            amount=fee_info["total_fee"]
        )
        res = payment_confirm_template(car_id, fee_info, token)
        res["confirmation_token"] = token
        return res

    def _h_confirm_payment(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        token = params.get("token") or session.get("pending_token")
        if not token:
            return {"text": "만료되었거나 유효하지 않은 결제 요청입니다. 다시 요금을 조회해주세요.", "card": None, "chips": [{"id": "fee", "label": "요금 다시 조회"}]}
            
        verified = self.flow_manager.verify_and_consume_token(token, session["session_id"])
        if not verified:
            return {"text": "결제 승인 유효 시간이 초과되었습니다. 다시 결제를 시도해주세요.", "card": None, "chips": [{"id": "fee", "label": "요금 조회"}]}
            
        # 결제 실행
        receipt = self.adapter.pay_fee(
            car_id=verified["car_id"],
            amount=verified["amount"],
            confirmation_token=token
        )
        return payment_receipt_template(receipt)

    def _h_cancel_action(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        self.flow_manager.clear_flow(session["session_id"])
        return {
            "text": "진행 중이던 작업을 취소했습니다. 다른 도움이 필요하시면 언제든 말씀해주세요.",
            "card": None,
            "chips": [
                {"id": "empty_all", "label": "주차장 빈자리 보기"},
                {"id": "find_car", "label": "내 차 위치 찾기"}
            ]
        }

    def _h_get_alerts(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot") or session.get("lot_id") or "A"
        alerts = self.adapter.get_alerts(lot_id)
        return alert_template(alerts)

    def _h_evacuation_guide(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot") or session.get("lot_id") or "A"
        alerts = self.adapter.get_alerts(lot_id)
        if not alerts:
            self.adapter.set_mock_alert(lot_id, "A-05", 2, "A구역 비상 화재 발생")
            alerts = self.adapter.get_alerts(lot_id)
        return alert_template(alerts)

    def _h_clear_alert(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot") or session.get("lot_id") or "A"
        self.adapter.clear_alerts()
        return alert_resolved_template(lot_id=lot_id, space_id="A-05구역")

    def _h_exit_help(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        return exit_help_template(params.get("lot") or session.get("lot_id"))

    def _h_get_violations(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        return violations_template()

    def _h_call_admin(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot") or session.get("lot_id") or "A"
        space_id = params.get("space")
        call_record = self.adapter.call_admin(lot_id=lot_id, space_id=space_id, reason="고객 호출 요청")
        return call_admin_template(call_record)

    def _h_call_status(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        calls = self.adapter.admin_calls
        if not calls:
            return {"text": "현재 진행 중인 관리자 호출 접수 내역이 없습니다.", "card": None, "chips": [{"id": "call_admin", "label": "관리자 호출하기"}]}
        last_call = calls[-1]
        text = (
            f"**관리자 호출 접수 상태** 📋\n\n"
            f"• 접수 번호: {last_call['call_id']}\n"
            f"• 위치: {last_call['lot_name']}\n"
            f"• 접수 시간: {last_call['created_at']}\n"
            f"• 진행 상태: {last_call['status']}"
        )
        return {"text": text, "card": {"type": "admin_call", "data": last_call}, "chips": [{"id": "empty_all", "label": "주차장 현황"}]}

    def _h_admin_help(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        return admin_help_template()

    def _h_payment_help(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        text = (
            f"**스마트 모의 결제 이용 방법** 💳\n\n"
            f"1. 채팅창에 **'내 차 요금 얼마야?'** 또는 **'23번 요금'**을 입력하세요.\n"
            f"2. 청구된 요금 카드가 나타나면 **[결제하기]** 버튼을 누릅니다.\n"
            f"3. 최종 확인 안내에서 **[결제 승인]**을 누르면 정산이 완료되며 모의 영수증이 발급됩니다.\n"
            f"4. 정산 완료 후 15분 이내에 출차하시면 됩니다."
        )
        return {"text": text, "card": None, "chips": [{"id": "fee_car:23", "label": "23번 요금 결제 체험"}]}

    def _h_payment_issue(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        text = (
            f"결제 중 오류가 발생하셨나요? 😢\n"
            f"동일한 요청은 중복 승인되지 않도록 안전 잠금 장치가 적용되어 있습니다.\n"
            f"지속적인 오류 발생 시 현장 관리자를 호출해 주시면 즉시 차단기 수동 개방 및 확인을 도와드립니다."
        )
        return {"text": text, "card": None, "chips": [{"id": "call_admin", "label": "관리자 긴급 호출"}]}

    def _h_charging_time_info(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        return charging_time_template()

    def _h_ev_charging_status(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot")
        chargers = self.adapter.get_ev_charging_status(lot_id=lot_id)
        return ev_charging_template(chargers, lot_id=lot_id)

    def _h_view_map(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot") or session.get("lot_id") or "A"
        session["lot_id"] = lot_id
        return view_map_template(lot_id)

    def _h_nearest_zone_guide(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot") or session.get("lot_id") or "C"
        dest = params.get("destination")
        empty_data = self.adapter.get_empty_spots(lot_id=lot_id)
        return nearest_zone_template(lot_id=lot_id, destination=dest, empty_data=empty_data)

    def _h_facility_info(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        lot_id = params.get("lot") or session.get("lot_id") or "D"
        facility = params.get("facility", "엘리베이터")
        return facility_template(lot_id=lot_id, facility=facility)

    def _h_flight_congestion_info(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        flight_time = params.get("flight_time")
        before_hours = params.get("before_hours")
        empty_data = self.adapter.get_empty_spots(lot_id="C")
        return flight_congestion_template(flight_time=flight_time, before_hours=before_hours, empty_data=empty_data)

    def _h_welcome(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        return welcome_template()

    def _h_thanks(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        text = "천만에요! 즐겁고 편리한 주차 되시길 바랍니다. 필요하신 점이 있으면 언제든 말씀해주세요! 😊"
        return {"text": text, "card": None, "chips": [{"id": "empty_all", "label": "주차장 빈자리 보기"}]}

    def _h_help(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        return help_template()

    def _h_fallback(self, params: Dict[str, Any], session: Dict[str, Any]) -> Dict[str, Any]:
        return fallback_template()

