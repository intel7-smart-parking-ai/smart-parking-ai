"""Common conversation handling; never authorizes actions or invents facts."""
import re
import time
from . import config, dialog_context as DC

SOCIAL = {
 'greeting':(r'(?:ㅎ\s*ㅇ|하이|안녕(?:하세요|하십니까)?|반가워(?:요)?|hello|hi|hey)[!~.\s😊🙂]*',
             '안녕하세요! 빈자리, 내 차 위치, 주차요금 중 무엇을 도와드릴까요?'),
 'thanks':(r'(?:고마워(?:요)?|고맙습니다|감사(?:합니다|해요)?|ㄱ\s*ㅅ|땡큐|thanks?)[!~.\s😊🙂]*',
           '도움이 됐다니 다행이에요. 더 궁금한 게 있으면 말씀해 주세요.'),
 'ack':(r'(?:알겠어(?:요)?|ㅇㅋ|오케이|확인했어(?:요)?)[!~.\s]*',
        '네, 필요하시면 언제든 말씀해 주세요.'),
 'complaint':(r'(?:왜\s*이래|답답해(?:요)?|이상해(?:요)?|틀렸어(?:요)?|잘못됐어(?:요)?|불편해(?:요)?|안\s*되잖아)[!?.~\s]*',
              '어떤 부분이 불편하셨나요? 차량 조회, 요금, 안내 내용 중 문제가 된 부분을 알려주시면 확인해 드릴게요.'),
}
EXPLAIN=re.compile(r'^(?:뭐지|뭐야|무슨\s*(?:말|뜻)(?:이야|이에요|인지)?|다시\s*(?:설명|말)(?:해\s*줘|해줘|해주세요)?|쉽게\s*(?:말해|설명해)(?:줘|주세요)?|이해가?\s*안\s*(?:돼|되네|돼요))[!?.~\s]*$')
VAGUE=re.compile(r'^(?:음+|흠+|글쎄|모르겠어(?:요)?|도와\s*줘|도움|저기(?:요)?|어떡해|어쩌지)[!?.~\s]*$')
FOLLOWUP=re.compile(r'^\s*(?:그럼|그러면)?\s*(.+?)\s*(?:은|는|도)?\s*[?？!]*\s*$')
READ_INTENTS={'operations_report','get_empty_spots','find_my_car','get_fee','fee_rule','help','charging_status','congestion_forecast'}


def fresh(s):
    return s.conversation_at and time.monotonic()-s.conversation_at<=config.QUERY_CONTEXT_TTL_SEC


def common(s,text):
    if not text:return None
    text=text.strip()
    for intent,(pattern,reply) in SOCIAL.items():
        if re.fullmatch(pattern,text,re.I):return {'intent':intent,'reply':reply}
    if EXPLAIN.fullmatch(text):
        if s.pending:
            return {'intent':'explain','reply':'진행 중인 확인이 있어요. 내용을 확인한 뒤 최근 확인 버튼을 선택해 주세요. 이 메시지만으로 결제나 호출을 승인하지는 않습니다.'}
        if fresh(s) and s.conversation_reply:
            if not s.conversation_question:
                reply='주차 안내를 도와드리는 챗봇이에요. “빈자리 알려줘”, “내 차 어디 있어?”처럼 말씀해 주세요.'
            else:
                lines=[line for line in s.conversation_reply.splitlines() if line.strip()]
                reply='방금 안내한 내용을 다시 정리하면 다음과 같아요. 실시간 정보는 다시 조회하면 달라질 수 있어요.\n'+'\n'.join(lines[:4])
                if len(lines)>4:reply+='\n어느 항목을 더 설명해 드릴까요?'
            return {'intent':'explain','reply':reply}
        return {'intent':'explain','reply':'어떤 부분이 궁금하셨나요? 빈자리, 차량 위치, 요금 중 하나만 말씀해 주셔도 돼요.'}
    if VAGUE.fullmatch(text):
        return {'intent':'clarify','reply':'차량을 찾으시나요, 빈자리가 궁금하신가요? 원하시는 것 하나만 말씀해 주세요.'}
    if fresh(s) and s.conversation_question and not s.pending and not s.agent_followup:
        match=FOLLOWUP.fullmatch(text)
        if match:
            value=match[1].strip()
            selected=[]
            for lot in config.catalog.ids():
                row=config.catalog.lot(lot)
                aliases=[row['name'],lot]+row.get('aliases',[])+row.get('loose_aliases',[])
                if any(value==alias for alias in aliases):selected.append(lot)
            if not selected and value in ('거기','그곳','그 주차장','해당 주차장') and DC.focus(s):
                selected=[DC.focus(s)]
            if len(selected)==1:
                original=s.conversation_question
                if re.search(r'결제해|취소해|호출|예약해|매출|수익',original):return None
                aliases=[]
                for lot in config.catalog.ids():
                    row=config.catalog.lot(lot)
                    aliases += [row['name']]+row.get('aliases',[])+row.get('loose_aliases',[])
                for alias in sorted(set(aliases),key=len,reverse=True):
                    if len(alias)>1:original=re.sub(r'(?<![가-힣A-Za-z0-9])'+re.escape(alias)+r'(?![가-힣A-Za-z0-9])','',original)
                original=re.sub(r'(?<![A-Za-z0-9])[A-D](?:\s*주차장)?(?![A-Za-z0-9-]|\s*구역)','',original)
                return {'intent':'followup','rewrite':config.catalog.name(selected[0])+' '+original.strip()}
    return None


def remember(s,question,response):
    meta=response.get('meta') or {}
    if meta.get('conversation_intent') and meta['conversation_intent']!='followup':
        return
    if question and meta.get('intent') in READ_INTENTS and not meta.get('permission_denied') and not meta.get('unavailable'):
        if meta.get('intent')!='congestion_forecast':s.forecast_question={};s.forecast_at=0
        s.conversation_question=question
        s.conversation_failures=0
    if response.get('text'):
        s.conversation_reply=response['text']
        s.conversation_at=time.monotonic()


def unclear(s):
    s.conversation_failures+=1
    if s.pending:
        return '진행 중인 확인이 있어요. 승인이나 취소는 최근 확인 버튼으로 선택해 주세요. 다른 질문도 말씀하실 수 있어요.'
    if s.conversation_failures>1:
        return '원하시는 기능을 함께 좁혀볼게요. 아래에서 빈자리, 내 차 찾기, 충전 현황 중 하나를 선택해 주세요.'
    return '어떤 도움이 필요하신가요? “빈자리 알려줘”, “내 차 찾기”, “주차요금”처럼 한 단어로 말씀해 주셔도 돼요.'
