"""서비스 범위 밖 요청을 모델 없이 정해진 문장으로 답한다.

루트 CLAUDE.md 5번(근거 없는 답 금지)과 공통 테스트 질문지 🚫 항목(예약·보장 약속, 할인, 매출,
다른 이용자 차량 정보, 지시 무시 요청, 등록되지 않은 주차장)은 모델 상태와 관계없이 같은 답이 나가야 한다.
여기서 맞는 질문은 대화 에이전트와 키워드 규칙보다 먼저 처리한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from . import config
from .parser import extract_lots

_INJECTION = re.compile(
    r"(?:이전|앞|위|기존|모든)\S*\s*(?:지시|명령|프롬프트|규칙|설정)\S*\s*(?:다\s*)?(?:무시|잊)|"
    r"시스템\s*프롬프트|프롬프트\s*(?:보여|알려|출력)|개발자\s*모드|탈옥|"
    r"ignore\s+(?:all\s+|the\s+|your\s+)?(?:previous|prior|above)|jailbreak|system\s+prompt", re.I)
_OTHER_VEHICLES = re.compile(
    r"(?:모든|전체|전부|다른\s*(?:사람|이용자|손님)\S*|남의|남)\s*(?:차|차량)\S*\s*(?:번호|목록|리스트|위치|정보)|"
    r"(?:차|차량)\s*번호\s*(?:전부|다|모두|목록|리스트)|"
    r"(?:[A-D]-\d{2}|\d{1,2}\s*번\s*칸)\S*\s*(?:지금\s*)?(?:무슨|어떤|어느|누구|몇\s*번)\s*(?:차|차량)|"
    r"누구\s*(?:의\s*)?차|차\s*주인|차주\s*(?:누구|연락처|정보)")
_SALES = re.compile(r"매출|수익|순이익|영업\s*이익|정산\s*내역|(?:하루|오늘|어제|이번\s*달)\s*(?:수입|번\s*돈)|얼마\s*벌")
_DISCOUNT = re.compile(r"깎아|깍아|할인|에누리|감면|면제|(?:무료|공짜)로\s*(?:해|좀|해\s*줘|해줘)|싸게\s*해|요금\s*(?:좀\s*)?빼")
_RESERVE = re.compile(r"예약|(?:자리|칸)\S*\s*(?:좀\s*)?(?:잡아|맡아|빼\s*놔|비워\s*놔|찜)|미리\s*(?:잡|맡)|보장|확보해")
_ADMIN_WHERE = re.compile(r"(?:관리자|관리실|직원|안내\s*데스크)\S*\s*(?:어디|위치)|(?:관리자|직원)\S*\s*(?:계세요|있어요|있나요)\s*\??$")
_LOT_WORD = re.compile(r"([가-힣A-Za-z0-9]{2,15})\s*주차장")
_GENERIC_LOT = re.compile(r"^(?:이|그|저|모든|전체|다른|어느|무슨|근처|제일|가장|가까운|한산한|우리|여기|거기|저기|"
                          r"실물|가상|지하|지상|공영|민영|무료|유료|사설|시연|주변|옆|어떤|모든|추천|다음|어디|무슨|몇|"
                          r"\S*(?:은|는|한|운|된|인|할|을|의|던|란|든|른|쁜|큰|낮은|높은))$")


@dataclass
class Boundary:
    key: str
    text: str
    chips: list = field(default_factory=lambda: ["empty_all", "find_car", "help"])
    with_spots: bool = False  # 지금 기준 빈자리를 함께 보여 준다


def _lot_names():
    return "·".join(config.catalog.name(k) for k in config.catalog.ids())


def unknown_lot(text: str):
    """'롯데백화점 주차장'처럼 등록되지 않은 주차장 이름. 등록된 주차장을 함께 말했으면 None."""
    if extract_lots(text):
        return None
    for match in _LOT_WORD.finditer(text):
        word = match.group(1)
        if not _GENERIC_LOT.match(word):
            return word + " 주차장"
    return None


def match(text: str) -> Boundary | None:
    if not isinstance(text, str) or not text.strip():
        return None
    if _INJECTION.search(text):
        return Boundary("prompt_injection",
                        "그 요청은 도와드릴 수 없어요. 저는 정해진 주차 안내(빈자리, 내 차 위치, 요금·모의 결제)만 해요.\n"
                        "다른 이용자의 차량 번호나 위치는 알려드리지 않아요.")
    if _OTHER_VEHICLES.search(text):
        return Boundary("other_vehicle",
                        "다른 이용자의 차량 번호나 위치는 개인정보라서 알려드릴 수 없어요.\n"
                        "칸이 비었는지는 빈자리 조회로, 내 차는 차량 번호로 확인할 수 있어요.")
    if _SALES.search(text):
        return Boundary("sales",
                        "매출과 정산 내역은 관리자 화면에서만 확인할 수 있어요. 이용자 챗봇에서는 안내하지 않아요.",
                        ["fee", "help"])
    if _DISCOUNT.search(text):
        return Boundary("discount",
                        "요금 할인이나 감면은 제가 해 드릴 수 없어요. 요금은 서버가 정한 금액 그대로 안내해요.\n"
                        "요금 문의가 더 있으면 관리자를 불러 확인할 수 있어요.",
                        ["fee", "fee_rule", "call_admin"])
    if _RESERVE.search(text):
        return Boundary("reservation",
                        "자리 예약이나 자리 보장 기능은 없어요. 빈자리는 먼저 들어온 차가 쓰고, "
                        "저는 지금 기준 빈자리만 안내할 수 있어요.",
                        with_spots=True)
    if _ADMIN_WHERE.search(text):
        return Boundary("admin_where",
                        "관리자 위치 정보는 챗봇에 등록되지 않았어요. 도움이 필요하면 아래 '관리자 호출' 버튼을 눌러 주세요.\n"
                        "버튼을 누르기 전에는 호출이 접수되지 않아요.",
                        ["call_admin", "call_status", "help"])
    lot = unknown_lot(text)
    if lot:
        return Boundary("unknown_lot",
                        f"{lot}은 이 서비스에 등록되지 않은 주차장이라 정보가 없어요.\n"
                        f"안내할 수 있는 곳은 {_lot_names()}이에요.",
                        ["lot_" + k for k in config.catalog.ids()][:4])
    return None
