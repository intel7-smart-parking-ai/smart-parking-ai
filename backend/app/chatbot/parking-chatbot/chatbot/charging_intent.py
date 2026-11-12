"""One shared matcher for questions that should open the charger dashboard."""
import re

# 영어·중국어·일본어 충전 현황 질문 (D-67 A안: #78 다국어 인식만 main 형식으로 이식).
_FOREIGN_STATUS = re.compile(
    r"(?:ev\s+)?(?:charging|charger)s?\s+(?:status|progress|state|availability)|"
    r"(?:battery|charge)\s+(?:level|percent(?:age)?)|how\s+long\s+(?:until|till|to)\s+(?:fully\s+)?charg|"
    r"充电\s*(?:状态|进度|情况|桩状态)|充電\s*(?:状況|状態|進捗)",
    re.IGNORECASE,
)
_FOREIGN_EXCLUDE = re.compile(
    r"\b(?:fee|fees|pay|payment|price|cost|revenue|fire|smoke|alert|evacuat\w*)\b|费用|付款|支付|火灾|料金|支払|火事|火災",
    re.IGNORECASE,
)


def wants_status(text):
    text=text.replace('충천','충전')
    if not re.search(r'충전|충전기',text):
        return bool(_FOREIGN_STATUS.search(text)) and not _FOREIGN_EXCLUDE.search(text)
    if re.search(r'매출|결제|대피|불\s*났|화재',text):
        return False
    if re.search(r'현황|상태|여부|충전\s*중|충전\s*시간|충전율|배터리|완료|언제.{0,8}끝|연결.{0,4}(?:됐|되었)',text):
        return True
    # '충전기 지금 몇 대나 쓰고 있어?' — 사용 중인 충전기 수는 충전 현황(칸별 충전 상태)에 있다.
    # 11/11 내부 검사: 이 질문에 빈 충전 자리 수를 답했다(묻는 것과 다른 값).
    if re.search(r'쓰고\s*있|쓰는\s*중|사용\s*(?:중|하고)|이용\s*(?:중|하고)|가동',text):
        return True
    # '남은/남았/잔여'만 있으면: '남은 시간'은 충전 현황, '충전 칸 남은 자리'는 빈자리 질문(대화 AI·빈자리 조회로)
    return bool(re.search(r'남은|남았|잔여',text)) and not re.search(r'자리|칸|면|몇\s*(?:개|대)',text)
