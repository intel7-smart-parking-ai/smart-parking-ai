"""A12/B16 synthetic demo. No camera, downloads or operational dataset writes."""
import argparse
import copy
from datetime import datetime,timezone
from pathlib import Path
import time
import tkinter as tk
import cv2
import numpy as np
from camera_hub import FramePacket
from dashboard import Dashboard
from layouts import layout,snapshot
from pipeline import read_json
from vision19 import VisionEngine,make_reference

def build_demo_scene(lot,cfg):
    if lot not in ('A','B'):raise ValueError('합성 예제는 A 또는 B를 선택하세요.')
    columns=6 if lot=='A' else 8;empty=np.full((540,columns*200,3),65,np.uint8);spaces=[]
    for i,slot in enumerate(layout(lot)['spaces']):
        x=i%columns*200+8;y=i//columns*260+8
        poly=[[x+20,y],[x+180,y],[x+160,y+240],[x,y+240]] if lot=='B' else [[x,y],[x+180,y],[x+180,y+240],[x,y+240]]
        roi_points=poly if i<columns else [poly[3],poly[2],poly[1],poly[0]]
        spaces.append({**slot,'polygon':copy.deepcopy(roi_points),'boundary_kind':'open_u',
            'painted_edges':copy.deepcopy([[roi_points[3],roi_points[0]],[roi_points[0],roi_points[1]],[roi_points[1],roi_points[2]]]),
            'entrance_edge':copy.deepcopy([roi_points[2],roi_points[3]]),'line_support':[1.,1.,1.]})
        if slot['space_type']!='general':cv2.fillPoly(empty,[np.asarray(poly)],(70,125,75) if slot['space_type']=='electric' else (170,110,65))
        painted=[poly[3],poly[0],poly[1],poly[2]] if i<columns else [poly[0],poly[3],poly[2],poly[1]]
        cv2.polylines(empty,[np.asarray(painted)],False,(235,235,235),3)
        cv2.putText(empty,slot['space_id'],(x+40,y+218),cv2.FONT_HERSHEY_SIMPLEX,.65,(235,235,235),2)
    roi=snapshot(lot,'IMPORT','synthetic-demo',empty.shape[1::-1],'demo-fixed',spaces)
    base=FramePacket('IMPORT','synthetic-demo',time.time_ns(),datetime.now(timezone.utc).isoformat(),time.monotonic(),empty,stream_epoch=1,source_kind='synthetic')
    image=empty.copy()
    for index,number,color in [(0,'23',(235,235,235)),(2,'42',(235,235,235)),(columns-2,'57',(0,180,0)),(columns+1,'85',(220,60,10)),(columns*2-1,'14',(0,180,0))]:
        x=index%columns*200+8;y=index//columns*260+8
        cv2.rectangle(image,(x+38,y+28),(x+138,y+190),(25,25,25),-1)
        cv2.rectangle(image,(x+58,y+80),(x+118,y+125),color,-1)
        cv2.putText(image,number,(x+61,y+113),cv2.FONT_HERSHEY_SIMPLEX,.9,(0,0,0),2)
    packet=FramePacket('IMPORT','synthetic-demo',time.time_ns(),datetime.now(timezone.utc).isoformat(),time.monotonic(),image,stream_epoch=1,source_kind='synthetic')
    demo_cfg=copy.deepcopy(cfg);demo_cfg.update(enable_ocr=False,capture_source={'kind':'synthetic','synthetic':True})
    reference=make_reference(base,roi);prediction=VisionEngine().run(packet,roi,demo_cfg,reference)
    return packet,roi,reference,prediction


class DemoDashboard(Dashboard):
    def __init__(self,window,cfg,root,project=None,lot='A'):
        cfg=copy.deepcopy(cfg);cfg.update(enable_ocr=False,capture_source={'kind':'synthetic','synthetic':True})
        super().__init__(window,cfg,root,project)
        self.lot.set(lot);self.active_lot=lot;self.show_demo(lot)

    def show_demo(self,lot):
        packet,roi,reference,prediction=build_demo_scene(lot,self.cfg)
        self.mode.set('A안 빈 기준');self.ocr.set(False);self.auto.set(False);self.send_enabled.set(False)
        self.input_mode='image';self.raw=packet;self.roi=roi;self.reference=reference;self.confirmed_epoch=packet.stream_epoch
        self.reset_cards();self.publish_scene((self.generation,packet,roi,None),prediction,False)
        self.window.title(f'V51 합성 A/B 주차장 체험 · 현재 {lot} {len(roi["spaces"])}칸 · 실물 정확도 시험 아님')
        self.auto_status.set('합성 예제 · A 12칸 / B 16칸을 위에서 선택하세요. 실제 카메라 연결·자동 수집은 일반 실행을 사용합니다.')
        self.notice.set('이전 A/B 칸 정의를 재사용한 합성 화면입니다. 번호 OCR은 꺼져 있어 번호 후보는 미확인입니다. 저장 위치는 .demo_output이며 실제 데이터가 아닙니다.')

    def after_lot_change(self,was_saved):self.show_demo(self.lot.get())
    def connect(self):raise ValueError('합성 예제에서는 카메라를 연결하지 않습니다. 실제 촬영은 run_windows.bat를 실행하세요.')
    def start_automatic(self):raise ValueError('합성 예제는 A/B 화면 확인용입니다. 실제 자동 수집은 run_windows.bat를 실행하세요.')
    def choose_root(self):raise ValueError('합성 자료는 .demo_output에 저장합니다. 기존 촬영 자료 열기는 일반 실행에서 사용하세요.')
    def import_image(self):raise ValueError('실제 사진 열기는 일반 실행에서 사용하세요.')
    def toggle_send(self):
        self.send_enabled.set(False);self.sender.cancel();self.api_status.set('합성 예제는 서버 전송하지 않습니다.')


def main(lot='A'):
    project=Path(__file__).resolve().parent;cfg=read_json(project/'config.json');cfg['project']=str(project)
    window=tk.Tk();DemoDashboard(window,cfg,project/'.demo_output',project,lot);window.mainloop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='V51 A12/B16 synthetic demo');parser.add_argument('--lot',choices=['A','B'],default='A')
    main(parser.parse_args().lot)
