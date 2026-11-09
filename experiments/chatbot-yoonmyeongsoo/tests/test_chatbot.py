import unittest
from backend.app.chatbot.parser import parse
from backend.app.chatbot.engine import ChatEngine
from backend.app.chatbot.catalog import LOTS_CONFIG

class ChatbotRegressionTests(unittest.TestCase):
    def setUp(self):
        self.engine = ChatEngine()

    # 1. Q7 버그 검증: "관리자 부르면 돈 들어?" -> call_admin이 아니라 admin_help여야 함
    def test_q7_bug_admin_cost_question(self):
        test_phrases = [
            "관리자 부르면 돈 들어?",
            "관리자 부르면 비용 있어?",
            "관리자 호출하면 돈 들어요?",
            "관리자 불러도 무료인가요?"
        ]
        for phrase in test_phrases:
            res = parse(phrase)
            self.assertEqual(
                res["intent"],
                "admin_help",
                f"문장 '{phrase}'는 call_admin이 아닌 admin_help여야 합니다. (실제: {res['intent']})"
            )

    # 2. 관리자 호출 의도 분기 검증
    def test_call_admin_intent(self):
        test_phrases = [
            "관리자 호출해줘",
            "관리자 좀 불러주세요",
            "관리자 와주세요"
        ]
        for phrase in test_phrases:
            res = parse(phrase)
            self.assertEqual(res["intent"], "call_admin")

    # 3. D-59 전용칸 규격 검증 (김해공항 장애인 16, 신세계 8, 부산역 2, 개발원 1)
    def test_d59_accessible_spaces_count(self):
        self.assertEqual(len(LOTS_CONFIG["C"]["accessible_spaces"]), 16, "김해공항 장애인 전용칸은 16칸이어야 합니다.")
        self.assertEqual(len(LOTS_CONFIG["D"]["accessible_spaces"]), 8, "신세계백화점 장애인 전용칸은 8칸이어야 합니다.")
        self.assertEqual(len(LOTS_CONFIG["B"]["accessible_spaces"]), 2, "부산역 장애인 전용칸은 2칸이어야 합니다.")
        self.assertEqual(len(LOTS_CONFIG["A"]["accessible_spaces"]), 1, "개발원 장애인 전용칸은 1칸이어야 합니다.")

    # 4. 내 차 찾기 (차량 번호 추출)
    def test_find_my_car_extraction(self):
        res = parse("23번 차 어디 있어?")
        self.assertEqual(res["intent"], "find_my_car")
        self.assertEqual(res["params"].get("car_id"), "23")

        res2 = parse("이십삼 번 차 어디 있어?")
        self.assertEqual(res2["intent"], "find_my_car")
        self.assertEqual(res2["params"].get("car_id"), "23")

        res3 = parse("1724번 위치")
        self.assertEqual(res3["intent"], "find_my_car")
        self.assertEqual(res3["params"].get("car_id"), "1724")

    # 5. 요금 조회 및 2단계 모의 결제 플로우
    def test_two_step_payment_flow(self):
        session_id = "test_user_session"
        
        # 1) 요금 조회
        fee_resp = self.engine.handle(session_id=session_id, text="23번 요금 얼마야?")
        self.assertIsNotNone(fee_resp["card"])
        self.assertEqual(fee_resp["card"]["type"], "fee")

        # 2) 결제 요청 (토큰 발급)
        pay_req_resp = self.engine.handle(session_id=session_id, text="결제해줘")
        token = pay_req_resp.get("confirmation_token")
        self.assertIsNotNone(token, "1단계 결제 요청 시 confirmation_token이 발급되어야 합니다.")
        self.assertEqual(pay_req_resp["flow"], "awaiting_payment_confirmation")

        # 3) 결제 승인 확인
        confirm_resp = self.engine.handle(session_id=session_id, chip_id=f"confirm_pay:{token}")
        self.assertEqual(confirm_resp["card"]["type"], "payment")
        self.assertTrue(confirm_resp["card"]["data"]["success"])
        self.assertIn("PAY-", confirm_resp["card"]["data"]["payment_id"])

    # 6. 비상 대피 경보 시 결제 차단 안전 규칙
    def test_evacuation_blocks_payment(self):
        # 모의 화재 경보 활성화
        self.engine.adapter.set_mock_alert("A", "A-05", level=2, message="화재 발생")
        
        # 결제 시도
        res = self.engine.handle(session_id="evac_test_sess", text="23번 결제해줘")
        self.assertIn("결제 불가", res["text"])
        self.assertIsNone(res.get("confirmation_token"), "화재 경보 중에는 결제 확인 토큰이 발급되면 안 됩니다.")
        
        # 경보 해제
        self.engine.adapter.clear_alerts()

    # 7. [이미지 1~4 실전 케이스 검증]
    def test_image_cases_resolution(self):
        # 이미지 1: 전기차 충전 시간
        res1 = self.engine.handle("test_sess", text="전기차 충전 구역에 충전 중인 차량 남은 충전 시간 알 수 있음?")
        self.assertEqual(res1["meta"]["intent"], "charging_time_info")
        self.assertIn("실시간 남은 충전 시간", res1["text"])

        # 이미지 2: 김해공항 국제선 청사 가장 가까운 주차구역
        res2 = self.engine.handle("test_sess", text="김해 공항 주차장에서 국제선 청사로 가는길이 가장 가까운 주차구역이 어디야?")
        self.assertEqual(res2["meta"]["intent"], "nearest_zone_guide")
        self.assertIn("P2 국제선 구역", res2["text"])

        # 이미지 3: 신세계백화점 엘리베이터 개수
        res3 = self.engine.handle("test_sess", text="신세계백화점 엘리베이터 몇개야")
        self.assertEqual(res3["meta"]["intent"], "facility_info")
        self.assertIn("총 2개소", res3["text"])

        # 이미지 4: 비행기 출발 3시간 전 도착 혼잡도
        res4 = self.engine.handle("test_sess", text="비행기가 2시20분 출발인데 3시간전에 도착해도 혼잡하지 않을까?")
        self.assertEqual(res4["meta"]["intent"], "flight_congestion_info")
        self.assertIn("여유 있게 진행", res4["text"])

    # 8. [전 주차장 빈자리 확인 & 전기차 충전 현황 & 도면 확인 검증]
    def test_all_lots_empty_spots_and_ev_charging(self):
        # 1) 전 주차장 빈자리 확인 -> 4개 주차장 현황
        res_all = self.engine.handle("test_sess", text="전 주차장 빈자리 확인")
        self.assertEqual(res_all["meta"]["intent"], "get_empty_spots")
        self.assertIn("전체 4개 주차장", res_all["text"])
        self.assertEqual(res_all["card"]["type"], "empty_spots")
        self.assertEqual(len(res_all["card"]["data"]["lots"]), 4)

        # 2) 전기차 충전 현황 -> 충전율(%)과 남은 충전 시간(분)
        res_ev = self.engine.handle("test_sess", text="전기차 충전 현황")
        self.assertEqual(res_ev["meta"]["intent"], "ev_charging_status")
        self.assertIn("충전 중", res_ev["text"])
        self.assertIn("약", res_ev["text"])
        self.assertIn("분 남음", res_ev["text"])
        self.assertEqual(res_ev["card"]["type"], "ev_charging")
        # 충전기 데이터 확인
        chargers = res_ev["card"]["data"]["chargers"]
        self.assertTrue(any(c["battery_percent"] > 0 for c in chargers))
        self.assertTrue(any(c["remaining_minutes"] > 0 for c in chargers))

        # 3) 도면 확인 칩 액션 -> view_map 의도
        res_map = self.engine.handle("test_sess", chip_id="view_map:A")
        self.assertEqual(res_map["meta"]["intent"], "view_map")
        self.assertEqual(res_map["card"]["type"], "view_map_trigger")
        self.assertEqual(res_map["card"]["data"]["lot_id"], "A")

    # 9. 최초 진입 시 환영 인사말 검증 (fallback 문구 방지)
    def test_welcome_greeting(self):
        res = self.engine.handle("new_user_sess", text="시작")
        self.assertEqual(res["meta"]["intent"], "welcome")
        self.assertIn("안녕하세요! 스마트 주차 도우미", res["text"])
        self.assertNotIn("질문을 이해하지 못했어요", res["text"])

    # 10. 각 주차장 요금 조회 시 4개 주차장 전체 요금 응답 검증
    def test_all_lots_fee_rule(self):
        # 1) 단독 질문 "각 주차장 요금 좀 알려줘"
        res = self.engine.handle("fee_sess_1", text="각 주차장 요금 좀 알려줘")
        self.assertEqual(res["meta"]["intent"], "fee_rule")
        self.assertIn("개발원 주차장", res["text"])
        self.assertIn("부산역 주차장", res["text"])
        self.assertIn("김해공항 주차장", res["text"])
        self.assertIn("신세계백화점 주차장", res["text"])
        self.assertEqual(res["card"]["type"], "fee_rule")
        self.assertEqual(len(res["card"]["data"]["lots"]), 4)

        # 2) 이전 세션에 특정 차량(23번, 개발원 A)이 설정되어 있더라도 "각 주차장 요금"은 4곳 모두 반환해야 함
        sess_id = "fee_sess_with_car"
        self.engine.handle(sess_id, text="23번 차 어디 있어?")  # lot_id='A', car_id='23' 설정
        res_after = self.engine.handle(sess_id, text="각 주차장 요금 좀 알려줘")
        self.assertEqual(res_after["meta"]["intent"], "fee_rule")
        self.assertIn("개발원 주차장", res_after["text"])
        self.assertIn("부산역 주차장", res_after["text"])
        self.assertIn("김해공항 주차장", res_after["text"])
        self.assertIn("신세계백화점 주차장", res_after["text"])
        self.assertEqual(len(res_after["card"]["data"]["lots"]), 4)

        # 3) 다양한 표현 검증: "주차장별 요금", "4개 주차장 요금"
        res_variant = self.engine.handle("fee_sess_var", text="주차장별 요금 알려줘")
        self.assertEqual(res_variant["meta"]["intent"], "fee_rule")
        self.assertEqual(len(res_variant["card"]["data"]["lots"]), 4)

        # 4) 특정 주차장만 명시하여 물어볼 때는 해당 주차장 요금만 반환
        res_single = self.engine.handle("fee_sess_single", text="부산역 주차장 요금 알려줘")
        self.assertEqual(res_single["meta"]["intent"], "fee_rule")
        self.assertIn("부산역 주차장 주차 요금 안내", res_single["text"])
        self.assertNotIn("김해공항 주차장", res_single["text"])

if __name__ == "__main__":
    unittest.main()
