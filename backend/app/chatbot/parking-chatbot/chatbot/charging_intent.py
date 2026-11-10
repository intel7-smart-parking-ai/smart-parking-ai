"""One shared matcher for questions that should open the charger dashboard."""
import re


def wants_status(text):
    text=text.replace('충천','충전')
    if not re.search(r'충전|충전기',text):
        return False
    if re.search(r'매출|결제|대피|불\s*났|화재',text):
        return False
    if re.search(r'현황|상태|여부|충전\s*중|충전\s*시간|충전율|배터리|완료|언제.{0,8}끝|연결.{0,4}(?:됐|되었)',text):
        return True
    # '남은/남았/잔여'만 있으면: '남은 시간'은 충전 현황, '충전 칸 남은 자리'는 빈자리 질문(대화 AI·빈자리 조회로)
    return bool(re.search(r'남은|남았|잔여',text)) and not re.search(r'자리|칸|면|몇\s*(?:개|대)',text)
