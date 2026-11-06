"""Evidence from real elapsed time in one continuous collection run."""
from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

from parking_ai.storage import atomic_json


class CollectionRun:
    def __init__(self, root, session, profile):
        self.path=Path(root)/'diagnostics'/'continuous_runs'/(uuid.uuid4().hex+'.json')
        self.start=time.monotonic()
        self.stopped_at=None
        self.last_save=self.start
        self.longest_gap=0.
        self.active_gap_at_stop=None
        self.last_observation=None
        self.next_checkpoint=self.start+30
        self.data={'세션ID':session,'시작_UTC':datetime.now(timezone.utc).isoformat(),
                   '카메라설정':profile,'시계':'PC monotonic','완료측정쌍':0,'분석시작쌍':0,
                   '분석보류쌍':0,'스티커대기쌍':0,'정지로해제한분석쌍':0,
                   '저장실패수':0,'짝선택대기관측수':0,'최대수신차_ms':None,
                   '채널별최근수신':{},'연결회차변경수':{'A':0,'B':0}}
        self.checkpoint()

    def waiting(self, frames, reason):
        key=tuple((p.serial,p.stream_epoch,p.sequence) if p else None for p in (frames.get('A'),frames.get('B')))
        if key!=self.last_observation:
            self.data['짝선택대기관측수']+=1
            self.data['최근대기사유']=str(reason)
            self.last_observation=key

    def observe_channels(self, frames):
        if self.stopped_at is not None:return
        channels=self.data['채널별최근수신']
        for k,packet in frames.items():
            if packet is None:continue
            previous=channels.get(k)
            if previous and previous['연결회차']!=packet.stream_epoch:
                self.data['연결회차변경수'][k]+=1
            channels[k]={'시리얼':packet.serial,'연결회차':packet.stream_epoch,'프레임번호':packet.sequence,
                         'PC수신_UTC':packet.timestamp_utc}

    def saved(self, info):
        now=time.monotonic()
        self.longest_gap=max(self.longest_gap,now-self.last_save)
        self.last_save=now
        self.data['완료측정쌍']+=1
        self.data['최근측정ID']=info['id']
        self.data['최대수신차_ms']=max(self.data['최대수신차_ms'] or 0.,info['skew_ms'])

    def stop_reason(self, reason):
        if self.stopped_at is None:
            self.stopped_at=time.monotonic()
            self.active_gap_at_stop=max(self.longest_gap,self.stopped_at-self.last_save)
            self.data['종료사유']=str(reason)

    def checkpoint(self, finished=False):
        now=time.monotonic()
        active_end=self.stopped_at if self.stopped_at is not None else now
        overall_gap=max(self.longest_gap,now-self.last_save)
        active_gap=overall_gap if self.active_gap_at_stop is None else self.active_gap_at_stop
        atomic_json(self.path,{**self.data,'상태':'종료' if finished else '진행 중',
                    '기록_UTC':datetime.now(timezone.utc).isoformat(),'실제경과_초':round(now-self.start,3),
                    '측정활성_초':round(active_end-self.start,3),'정지후마무리_초':round(now-active_end,3),
                    '최장저장공백_초':round(overall_gap,3),'측정활성_최장저장공백_초':round(active_gap,3)})
        self.next_checkpoint=now+30
