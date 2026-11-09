import time
import secrets
from typing import Dict, Any, Optional

class SessionFlow:
    def __init__(self):
        # session_id -> flow state
        self._sessions: Dict[str, Dict[str, Any]] = {}
        # token -> details
        self._tokens: Dict[str, Dict[str, Any]] = {}
        # request_id -> cached response (중복 방지)
        self._idempotency: Dict[str, Dict[str, Any]] = {}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "session_id": session_id,
                "flow": "idle",
                "car_id": None,
                "lot_id": None,
                "state_version": 1,
                "pending_token": None,
                "updated_at": time.time()
            }
        return self._sessions[session_id]

    def create_payment_token(self, session_id: str, car_id: str, amount: int) -> str:
        token = secrets.token_hex(8)
        now = time.time()
        self._tokens[token] = {
            "token": token,
            "session_id": session_id,
            "action": "payment",
            "car_id": car_id,
            "amount": amount,
            "expires_at": now + 180,  # 3분 유효
            "used": False
        }
        sess = self.get_session(session_id)
        sess["flow"] = "awaiting_payment_confirmation"
        sess["pending_token"] = token
        sess["state_version"] += 1
        return token

    def verify_and_consume_token(self, token: str, session_id: str) -> Optional[Dict[str, Any]]:
        record = self._tokens.get(token)
        if not record:
            return None
        if record["used"]:
            return None
        if time.time() > record["expires_at"]:
            return None
        if record["session_id"] != session_id:
            return None
            
        record["used"] = True
        sess = self.get_session(session_id)
        sess["flow"] = "idle"
        sess["pending_token"] = None
        sess["state_version"] += 1
        return record

    def clear_flow(self, session_id: str):
        sess = self.get_session(session_id)
        sess["flow"] = "idle"
        sess["pending_token"] = None
        sess["state_version"] += 1

    def get_idempotent_response(self, request_id: str) -> Optional[Dict[str, Any]]:
        if not request_id:
            return None
        return self._idempotency.get(request_id)

    def set_idempotent_response(self, request_id: str, resp: Dict[str, Any]):
        if request_id:
            self._idempotency[request_id] = resp

