"""Single-camera integrated collection/review UI. Workers never touch Tk."""
import copy
from datetime import datetime,timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import uuid
import cv2
from contracts import displayed_prediction
import numpy as np
from PIL import Image,ImageOps,ImageTk
from capture_views import VIEW_NAMES,view_code,view_counts,capture_pose
from camera_hub import CameraHub,FramePacket,load_camera_specs,find_realsense_devices,CameraSpec
from layouts import layout,LOT_NAMES,TYPE_NAMES,load_calibration,snapshot,save_calibration,zone_rule,COLOR_TYPES
from widgets import RoiEditor
from records import load_original,validate_capture
from pipeline import read_json
from parking_ai.storage import atomic_json
from review import rows_for_edit,save_review_with_receipt,review_revision,export_reviewed,validate_review
from vision19 import VisionEngine,context_key,make_reference,crop_space
from dataset19 import DatasetIndex,capture_id,observation_key,persist_scene,write_png,export_training,validate_prediction

STATE={'unknown':'보류','occupied':'점유','vacant':'빈칸'}
COLOR={'unknown':'미확인','white':'흰색','green':'초록','blue':'파랑'}


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=False);self.jobs=queue.Queue(4);self.results=queue.Queue();self.engine=VisionEngine();self.index=DatasetIndex()
    def run(self):
        while True:
            job=self.jobs.get()
            if job is None:return
            kind,token,args=job
            try:
                if kind=='analyze':
                    packet,roi,cfg,reference,mode=args
                    if roi.get('registration_method')=='aruco_board_v1':
                        from marker_setup import load_template,locate
                        template=load_template(cfg['auto_anchor_root'],roi['lot_id'])
                        if template is None:raise ValueError('마커 기준 파일이 없습니다. 수집을 다시 시작하세요.')
                        tracked=locate(template,packet,roi['pose']);roi=tracked['roi']
                        token=(*token[:2],roi,*token[3:])
                        if reference is not None and reference.get('key')!=context_key(roi):reference=None
                    elif roi.get('registration_method') in ('auto_line_and_text_v1','auto_open_u_and_text_v1'):
                        from automatic_setup import try_anchor
                        aligned=try_anchor(cfg['auto_anchor_root'],packet,roi['lot_id'],roi['pose'])
                        if not aligned or context_key(aligned['roi'])!=context_key(roi):
                            raise ValueError('자동 ROI 구도 불일치 또는 특징 부족: 재등록 필요')
                    validate_capture(packet,roi)
                    result=self.engine.run(packet,roi,{**cfg,'enable_ocr':False},reference,mode)
                    if cfg.get('enable_ocr'):
                        for row in result['spaces']:
                            if row['occupancy']!='vacant':
                                row['ocr_status']='pending';row['issues']=[i for i in row['issues'] if i!='OCR 꺼짐']+['번호 분석 대기']
                elif kind=='identify':
                    validate_capture(args[0],args[1]);result=self.engine.enrich(*args)
                elif kind=='enroll':
                    from marker_setup import enroll
                    result=enroll(*args)
                elif kind=='save':result=persist_scene(*args)
                elif kind=='recover':
                    from capture_recovery import recover_captures
                    result=recover_captures(*args)
                elif kind=='repair':
                    from dataset19 import rebuild_scene_assets
                    result=rebuild_scene_assets(*args)
                elif kind=='scan':result=self.index.scan(*args)
                elif kind=='review':result=save_review_with_receipt(*args)
                elif kind=='export':result=export_training(*args,selected_only=True)
                elif kind=='preview_export':result=export_reviewed(*args)
                elif kind=='audit':
                    from dataset_audit import export_audit
                    result=export_audit(*args)
                elif kind in ('collection_export','selected_export'):
                    from collection_output import export_collection
                    result=export_collection(*args,selected_only=kind=='selected_export')
                elif kind=='calibrate':
                    from automatic_setup import prepare_auto_scene
                    root,packet,lot,cfg=args
                    def reader_factory():
                        if self.engine.reader is None:
                            from analysis import make_reader
                            self.engine.reader,_=make_reader(Path(cfg['project']),cfg)
                        return self.engine.reader
                    result=prepare_auto_scene(root,packet,lot,capture_pose(cfg),reader_factory,cfg.get('auto_roi_min_score',.7))
                elif kind=='load':
                    from saved_scenes import load_saved_scene
                    result=load_saved_scene(*args)
                elif kind=='load_latest':
                    from saved_scenes import load_latest_lot
                    result=load_latest_lot(*args)
                else:raise ValueError('Unknown task')
                if kind in ('analyze','identify'):
                    if kind=='identify':packet,roi=args[:2]
                    validate_prediction(result,{'roi':roi,'frame_id':packet.sequence,'captured_at_utc':packet.timestamp_utc,
                        'lot_id':roi['lot_id'],'stream_epoch':packet.stream_epoch,'image_size':list(packet.image.shape[1::-1])})
                self.results.put((kind,token,result,None))
            except Exception as exc:
                logging.exception('Background %s',kind);self.results.put((kind,token,None,str(exc)))


class Dashboard:
    def __init__(self,window,cfg,root,project=None):
        self.window=window;self.cfg=copy.deepcopy(cfg);self.project=Path(project or Path(__file__).parent)
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True);self.cfg['project']=str(self.project)
        self.worker=Worker();self.worker.start();self.io_worker=Worker();self.io_worker.start();self.identity_worker=Worker();self.identity_worker.start()
        self.identifying=False;self.latest_occupancy_key=None;self.hub=None;self.camera_id='A';self.raw=None;self.roi=None;self.reference=None
        self.auditing=False;self.audit_scope=None;self.audit_result=None;self.exporting=False;self.repairing=False
        self.recovering=False;self.recovery_result=None
        self.auto_run=False;self.auto_phase='off';self.calibrating=False;self.auto_next_setup=0.;self.auto_candidates=[]
        from transport19 import EventSender
        self.sender=EventSender();self.sender.start();self.send_enabled=tk.BooleanVar(value=False);self.api_status=tk.StringVar(value='API 꺼짐 · 202는 접수만 의미')
        self.scene=None;self.record=None;self.pred=None;self.rows=[];self.previous=None;self.dirty=False
        self.session=uuid.uuid4().hex;self.batch='batch-'+uuid.uuid4().hex[:12];self.generation=0
        self.scene_group=None;self.batch_started_at=None
        self.confirmed_epoch=None;self.analyzing=False;self.saving=False;self.scanning=False;self.reviewing=False;self.loading=False
        self.live=False;self.closing=False;self.next_analysis=0.;self.next_save=0.;self.latest_seq=None
        self.selected=None;self.cards={};self.photos={};self.scan_result=None;self.index_scope='전체';self.last_dimensions=None;self.input_mode='camera';self.review_widget_states=[]
        window.title('V51 주차장 통합 수집 · 전체 칸 / 번호 / 스티커색 / 검수 / 저장')
        window.geometry(f'{min(1500,window.winfo_screenwidth()-40)}x{min(940,window.winfo_screenheight()-70)}')
        window.minsize(1060,720)
        if os.name=='nt' and window.state()!='withdrawn':window.state('zoomed')
        style=ttk.Style();style.configure('TButton',padding=3);style.configure('TLabel',font=('맑은 고딕',9))
        self.active_lot='A';self.lot=tk.StringVar(value='A');self.mode=tk.StringVar(value='B안 분류');self.ocr=tk.BooleanVar(value=False);self.auto=tk.BooleanVar(value=False)
        self.notice=tk.StringVar(value='주차장을 선택하고 [자동 수집 시작]을 누르세요. 모든 칸 이름과 경계가 보이면 자동 등록·판독·저장합니다.')
        self.summary=tk.StringVar();self.storage=tk.StringVar(value='저장 경로: '+str(self.root));self.detail=tk.StringVar(value='칸을 선택하세요.')
        self.path_text=tk.StringVar(value='저장한 장면이 없습니다.');self.camera_status=tk.StringVar(value='카메라 미연결')
        self.reviewer=tk.StringVar(value=cfg.get('reviewer','우지원'));self.occ=tk.StringVar(value='unknown');self.number=tk.StringVar();self.color=tk.StringVar(value='unknown')
        self.active_view=view_code(cfg)
        if self.active_view=='unspecified':self.active_view='ceiling'
        self.cfg['capture_view']=self.active_view;self.view=tk.StringVar(value=VIEW_NAMES[self.active_view])
        self.view_status=tk.StringVar(value='시점별 자료 집계 대기');self.view_audit=tk.StringVar(value='시점별 정답 대조: 평가 자료 없음')
        self.scope=tk.StringVar(value='전체');self.review_state=tk.StringVar(value='미검수')
        self.audit_text=tk.StringVar(value='품질·정답 대조: 현재 범위의 저장 예측과 승인 정답을 비교할 수 있습니다.')
        self.auto_status=tk.StringVar(value='자동 수집: 칸 이름·주차선 탐색 → 번호·색 후보 → 표시사진·한글 JSON 저장')
        self.marker_status=tk.StringVar(value='ArUco: A ID 0–5 / B ID 10–15 · 처음 4개 이상 등록, 수집 중 분산된 3개 이상 필요')
        self.export_status=tk.StringVar(value='내보내기 완료 기록 없음')
        self.build();self.reset_cards();self.draw_scene();self.start_recovery_if_needed();self.refresh_stats();window.protocol('WM_DELETE_WINDOW',self.close);window.after(100,self.poll)

    def button(self,parent,label,fn):
        b=ttk.Button(parent,text=label,command=lambda:self.guard(fn));b.pack(side='left',padx=2);return b

    def build(self):
        automatic=ttk.Frame(self.window);automatic.pack(fill='x',padx=6,pady=4)
        ttk.Label(automatic,text='주차장').pack(side='left')
        for value,title in [('A','A · 직사각형 12칸'),('B','B · 사선 16칸')]:
            ttk.Radiobutton(automatic,text=title,variable=self.lot,value=value,command=lambda:self.guard(self.change_lot)).pack(side='left',padx=2)
        self.button(automatic,'▶ 자동 수집 시작',self.start_automatic);self.button(automatic,'자동 수집 정지',self.stop_live)
        self.button(automatic,'칸별 사진·JSON 내보내기',lambda:self.export_current('collection_export'))
        self.button(automatic,'저장 폴더 선택',self.choose_root)
        # Separate row keeps both lot choices and saved-data access visible on small screens.
        browse=ttk.Frame(self.window);browse.pack(fill='x',padx=6,pady=2)
        self.button(browse,'선택 주차장 저장 장면 보기',self.open_latest_lot)
        self.button(browse,'사진 자동 등록·수집',self.collect_photo)
        ttk.Label(browse,text='기존 자료: 저장 폴더 선택 → A/B 선택 → 저장 장면 보기').pack(side='left',padx=6)
        viewbar=ttk.Frame(self.window);viewbar.pack(fill='x',padx=6,pady=2)
        ttk.Label(viewbar,text='우선 수집: 천장 · 현재 촬영 시점').pack(side='left')
        choice=ttk.Combobox(viewbar,textvariable=self.view,values=[v for k,v in VIEW_NAMES.items() if k!='unspecified'],state='readonly',width=9)
        choice.pack(side='left');choice.bind('<<ComboboxSelected>>',lambda e:self.guard(self.change_view))
        ttk.Label(viewbar,text='카메라 위치를 옮긴 뒤 선택 · 위/아래는 주차장 평면 기준 · 같은 차량 배치는 그룹 유지').pack(side='left',padx=5)
        ttk.Label(self.window,textvariable=self.view_status,wraplength=1450).pack(fill='x',padx=8)
        ttk.Label(self.window,textvariable=self.auto_status,wraplength=1450).pack(fill='x',padx=8)
        markerbar=ttk.Frame(self.window);markerbar.pack(fill='x',padx=6,pady=2)
        self.button(markerbar,'현재 ROI를 마커 기준으로 등록',self.enroll_markers)
        self.button(markerbar,'마커 인쇄·배치 안내',lambda:os.startfile(str(self.project/'markers'/'PRINT_MARKERS.html')))
        marker_label=ttk.Label(markerbar,textvariable=self.marker_status,wraplength=600);marker_label.pack(side='left',padx=4,fill='x',expand=True)
        markerbar.bind('<Configure>',lambda e:marker_label.configure(wraplength=max(180,e.width-430)))
        advanced=ttk.Frame(self.window)
        top=ttk.Frame(advanced);top.pack(fill='x',padx=6,pady=3)
        self.button(top,'카메라 1대 연결',self.connect);self.button(top,'사진 열기',self.import_image)
        self.button(top,'ROI 네 모서리',self.edit_roi);self.button(top,'위치 확인',self.confirm)
        self.button(top,'카메라 설정',self.camera_settings)
        ttk.Label(top,textvariable=self.camera_status).pack(side='right')
        bar=ttk.Frame(advanced);bar.pack(fill='x',padx=6)
        modes=ttk.Combobox(bar,textvariable=self.mode,values=['B안 분류','A안 빈 기준','스티커 근거','수동 검수'],state='readonly',width=12);modes.pack(side='left');modes.bind('<<ComboboxSelected>>',lambda e:self.invalidate_inference())
        self.button(bar,'빈 기준 등록',self.register_reference);self.button(bar,'B안 모델 설정',self.model_settings)
        ttk.Checkbutton(bar,text='번호 OCR (색 후보는 별도)',variable=self.ocr,command=self.invalidate_inference).pack(side='left')
        self.button(bar,'1회 분석',self.analyze_once);self.button(bar,'실시간 시작',self.start_live);self.button(bar,'정지/장면 고정',self.stop_live)
        self.button(bar,'현재 장면 저장',self.save_scene)
        ttk.Checkbutton(bar,text='분석 후 자동 저장',variable=self.auto).pack(side='left')
        summary_label=ttk.Label(self.window,textvariable=self.summary,wraplength=1450);summary_label.pack(fill='x',padx=8,pady=4)
        self.button(browse,'고급/수동 설정',lambda:advanced.pack_forget() if advanced.winfo_manager() else advanced.pack(fill='x',before=summary_label))
        netbar=ttk.Frame(advanced);netbar.pack(fill='x',padx=6)
        ttk.Checkbutton(netbar,text='실시간 카메라 결과 서버 전송',variable=self.send_enabled,command=self.toggle_send).pack(side='left')
        ttk.Label(netbar,textvariable=self.api_status).pack(side='left',padx=8)
        body=ttk.Panedwindow(self.window,orient='horizontal');body.pack(fill='both',expand=True,padx=6)
        left=ttk.Frame(body);side=ttk.Frame(body,width=355);body.add(left,weight=4);body.add(side,weight=1)
        scroller=tk.Canvas(side,width=340,highlightthickness=0)
        scrollbar=ttk.Scrollbar(side,orient='vertical',command=scroller.yview)
        scrollbar.pack(side='right',fill='y');scroller.pack(side='left',fill='both',expand=True)
        scroller.configure(yscrollcommand=scrollbar.set)
        right=ttk.Frame(scroller);self.review_panel=right
        panel_id=scroller.create_window((0,0),window=right,anchor='nw')
        right.bind('<Configure>',lambda e:scroller.configure(scrollregion=scroller.bbox('all')))
        scroller.bind('<Configure>',lambda e:scroller.itemconfigure(panel_id,width=e.width))
        self.canvas=tk.Canvas(left,bg='#18222e',height=310,highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Button-1>',self.canvas_select);self.canvas.bind('<Configure>',lambda e:self.draw_scene())
        self.card_area=ttk.Frame(left);self.card_area.pack(fill='x',pady=3)
        ttk.Label(right,text='선택 칸 원본 · 자동 후보 / 사람 정답').pack(anchor='w')
        self.detail_image=ttk.Label(right);self.detail_image.pack(fill='x')
        ttk.Label(right,textvariable=self.detail,wraplength=325).pack(fill='x',pady=3)
        form=ttk.Frame(right);form.pack(fill='x')
        for i,(label,var,values) in enumerate([('검수자',self.reviewer,None),('점유 정답',self.occ,['unknown','occupied','vacant']),('번호 정답',self.number,None),('스티커색 정답',self.color,['unknown','white','green','blue'])]):
            ttk.Label(form,text=label).grid(row=i,column=0,sticky='w')
            widget=ttk.Combobox(form,textvariable=var,values=values,state='readonly',width=16) if values else ttk.Entry(form,textvariable=var,width=18)
            widget.grid(row=i,column=1,sticky='ew');widget.bind('<<ComboboxSelected>>',lambda e:self.mark_dirty());widget.bind('<KeyRelease>',lambda e:self.mark_dirty())
        form.columnconfigure(1,weight=1)
        ttk.Label(right,textvariable=self.review_state).pack(anchor='w')
        for buttons in [[('후보를 정답 초안으로',self.copy_prediction)],[('스티커 내부 지정',self.edit_sticker),('번호 박스 지정',self.edit_number_box)],[('초안 저장',lambda:self.persist_review(False)),('선택 칸 승인',lambda:self.persist_review(True))]]:
            row=ttk.Frame(right);row.pack(fill='x',pady=2)
            for title,fn in buttons:self.button(row,title,fn)
        ttk.Label(right,text='자동 후보는 정답이 아닙니다.\n전용칸 경고는 로컬 의심 표시입니다.\n경계 변화는 손·그림자도 포함할 수 있습니다.',wraplength=320).pack(fill='x',pady=4)
        lower=ttk.LabelFrame(self.window,text='실제 저장 현황 · 파일 경로 / 재열기');lower.pack(fill='x',padx=6,pady=3)
        row=ttk.Frame(lower);row.pack(fill='x')
        ttk.Label(row,text='집계').pack(side='left')
        scope=ttk.Combobox(row,textvariable=self.scope,values=['전체','현재 주차장','현재 세션'],width=11,state='readonly');scope.pack(side='left');scope.bind('<<ComboboxSelected>>',lambda e:self.refresh_stats())
        self.button(row,'파일 대조/새로고침',lambda:self.refresh_stats(True));self.button(row,'새 촬영 배치',self.new_batch)
        self.button(row,'승인 자료·표시사진 내보내기',lambda:self.export_current('preview_export'))
        self.button(row,'통과·승인 학습 내보내기',lambda:self.export_current('export'))
        self.button(row,'저장 폴더 열기',lambda:os.startfile(str(self.root)))
        ttk.Label(lower,textvariable=self.storage,wraplength=1450).pack(fill='x')
        ttk.Label(lower,textvariable=self.export_status,wraplength=1450).pack(fill='x')
        auditbar=ttk.Frame(lower);auditbar.pack(fill='x')
        self.button(auditbar,'품질 통과 자료 내보내기',lambda:self.export_current('selected_export'))
        self.button(auditbar,'현재 범위 품질·정답 대조',lambda:self.export_current('audit'))
        ttk.Label(auditbar,textvariable=self.audit_text,wraplength=1150).pack(side='left',fill='x')
        ttk.Label(lower,textvariable=self.view_audit,wraplength=1450).pack(fill='x')
        self.files=ttk.Treeview(lower,columns=('lot','time','space','sticker','approve','state'),show='tree headings',height=3)
        self.files.heading('#0',text='원본 파일 / 장면 ID');self.files.column('#0',width=310)
        for key,title,width in [('lot','주차장',65),('time','촬영 시각(UTC)',230),('space','칸 파일',80),('sticker','스티커',80),('approve','승인 칸',80),('state','저장 상태',150)]:
            self.files.heading(key,text=title);self.files.column(key,width=width)
        self.files.pack(fill='x');self.files.bind('<<TreeviewSelect>>',self.file_selected);self.files.bind('<Double-1>',lambda e:self.guard(self.open_selected))
        row=ttk.Frame(lower);row.pack(fill='x')
        self.button(row,'선택 장면 검수',self.open_selected);self.button(row,'선택 장면 저장 복구',self.repair_selected);self.button(row,'선택 원본 열기',self.open_raw)
        self.button(row,'경로 복사',self.copy_path);self.button(row,'오류 상세',self.show_errors)
        ttk.Label(row,textvariable=self.path_text,wraplength=850).pack(side='left')
        ttk.Label(self.window,textvariable=self.notice,wraplength=1450,foreground='#174c79').pack(side='bottom',before=body,fill='x',padx=8,pady=3)
        lower.pack_configure(side='bottom',before=body)
        self.card_area.pack_configure(side='bottom',before=self.canvas)
        wrapped=[]
        def collect(widget):
            for child in widget.winfo_children():
                if isinstance(child,ttk.Label) and str(child.cget('textvariable')) in {str(v) for v in (self.summary,self.storage,self.notice,self.auto_status,self.export_status,self.view_status,self.view_audit)}:wrapped.append(child)
                collect(child)
        collect(self.window)
        def resize(event):
            if event.widget==self.window:
                for widget in wrapped:widget.configure(wraplength=max(400,event.width-30))
        self.window.bind('<Configure>',resize,add='+')

    def start_recovery_if_needed(self):
        self.recovery_result=None
        if next((self.root/'capture_journal').glob('*.json'),None) is not None:
            self.recovering=True;self.submit('recover',(self.root,copy.deepcopy(self.cfg)),str(self.root))
            self.notice.set('중단된 촬영의 원본·문맥·예측을 확인하여 복구 중…')

    def guard(self,fn):
        if self.closing:return
        if self.recovering:
            self.view.set(VIEW_NAMES[self.active_view]);self.lot.set(self.active_lot)
            self.notice.set('중단 촬영 복구가 끝난 뒤 실행하세요.');return
        try:return fn()
        except Exception as exc:
            logging.exception('UI');self.notice.set(str(exc));messagebox.showerror('확인 필요',str(exc),parent=self.window)

    def submit(self,kind,args,token=None):
        if self.closing:return
        worker=self.worker if kind=='analyze' else self.identity_worker if kind in ('identify','calibrate') else self.io_worker
        worker.jobs.put_nowait((kind,token if token is not None else self.generation,args))

    def mark_dirty(self):
        if self.selected and self.rows:self.dirty=True;self.review_state.set('수정됨 · 아직 저장하지 않음')

    def flush_fields(self):
        if not self.selected:return
        row=next((r for r in self.rows if r['space_id']==self.selected),None)
        if row is None:return
        values=(self.occ.get(),self.number.get().strip(),self.color.get())
        if values!=(row['occupancy'],row['plate_text'],row['color']):
            row.update(occupancy=values[0],plate_text=values[1],color=values[2],review_status='pending');self.dirty=True

    def can_leave(self):
        if self.reviewing:raise ValueError('검수 저장이 끝난 뒤 전환하세요.')
        self.flush_fields()
        return not self.dirty or messagebox.askyesno('미저장 정답','저장하지 않은 검수 수정을 버리고 전환할까요?',parent=self.window)

    def invalidate_inference(self):
        self.disable_automatic('분석 설정 변경 · 자동 수집 정지')
        self.generation+=1;self.live=False;self.pred=None;self.latest_seq=None
        self.sender.cancel()
        self.notice.set('분석 설정 변경 · 같은 장면을 다시 분석하세요.');self.update_cards()

    def clear_context(self):
        self.disable_automatic('입력/주차장 전환 · 자동 수집 정지');self.auto_candidates=[]
        self.marker_status.set(f"{self.lot.get()} 마커 기준은 자동 수집 시 검사합니다. A ID 0–5 / B ID 10–15")
        self.generation+=1;self.live=False;self.raw=None;self.scene=None;self.record=None;self.pred=None;self.rows=[];self.previous=None;self.dirty=False
        self.sender.cancel();self.latest_occupancy_key=None
        self.scene_group=None
        self.occ.set('unknown');self.number.set('');self.color.set('unknown');self.detail.set('칸을 선택하세요.')
        self.detail_image.configure(image='');self.review_state.set('미검수')
        self.reference=None;self.roi=None;self.confirmed_epoch=None;self.selected=None;self.latest_seq=None;self.reset_cards();self.draw_scene()

    def change_view(self):
        target=next((k for k,v in VIEW_NAMES.items() if v==self.view.get() and k!='unspecified'),None)
        if target==self.active_view:return False
        try:
            if target is None:raise ValueError('촬영 시점을 선택하세요.')
            if self.saving or self.reviewing or self.loading or self.repairing or self.recovering:raise ValueError('저장·검수·복구 완료 후 촬영 시점을 바꾸세요.')
            if not self.can_leave():self.view.set(VIEW_NAMES[self.active_view]);return False
        except Exception:
            self.view.set(VIEW_NAMES[self.active_view]);raise
        resume=self.auto_run and self.input_mode=='camera'
        self.active_view=target;self.cfg['capture_view']=target;self.clear_context();self.raw=None;self.input_mode='camera'
        if resume:
            self.auto_run=True;self.auto_phase='waiting';self.auto.set(True);self.ocr.set(True)
            self.auto_next_setup=time.monotonic()+self.cfg.get('warmup_seconds',3.)
        self.notice.set(VIEW_NAMES[target]+' 촬영 선택 · 주차칸 좌표 재등록 · 같은 차량 촬영 그룹 유지')
        self.view_audit.set('시점별 정답 대조는 현재 범위 품질·정답 대조로 갱신하세요.');return True

    def change_lot(self):
        previous=(self.roi or {}).get('lot_id',self.active_lot);target=self.lot.get()
        if target==previous:return False
        try:
            if self.loading or self.saving or self.repairing:raise ValueError('장면 열기·저장·복구가 끝난 후 주차장을 바꾸세요.')
            if not self.can_leave():self.lot.set(previous);return False
        except Exception:
            self.lot.set(previous);raise
        was_saved=self.input_mode=='saved';self.active_lot=target
        self.clear_context();self.session=uuid.uuid4().hex;self.batch='batch-'+uuid.uuid4().hex[:12]
        self.notice.set(f'{target}주차장 선택 · 기존 사진은 저장 장면 보기, 실제 촬영은 카메라를 옮긴 뒤 자동 수집 시작');self.refresh_stats()
        self.after_lot_change(was_saved)
        return True

    def after_lot_change(self,was_saved):
        if was_saved:self.open_latest_lot()

    def connect(self):
        if not self.can_leave():return
        if self.hub:
            self.clear_context();self.input_mode='camera';self.notice.set('현재 카메라 영상으로 전환. 위치를 확인하세요.');return
        specs=load_camera_specs(self.project/'cameras.json')
        devices=find_realsense_devices() if specs[0].source=='realsense' else []
        if devices:
            serials=[d['serial'] for d in devices]
            desired=specs[0]
            if desired.source=='realsense' and desired.serial not in serials:
                if len(serials)==1:
                    desired=CameraSpec('A','realsense',serials[0],width=desired.width,height=desired.height,fps=desired.fps)
                else:raise ValueError('카메라가 여러 대입니다. cameras.json A 시리얼을 선택하세요.')
            specs=[desired]
        hub=CameraHub(specs[:1])
        self.clear_context();self.hub=hub;self.camera_id=specs[0].camera_id;self.input_mode='camera';self.hub.start();self.notice.set('한 대 연결 중…')

    def get_packet(self):
        if self.scene and not self.live:return self.scene[0]
        packet=self.hub.get_latest_frame(self.camera_id) if self.hub and self.input_mode=='camera' else self.raw
        if packet is None:raise ValueError('새 카메라 영상 또는 가져온 사진이 필요합니다.')
        return packet

    def import_image(self):
        self.require_capture_view()
        if not self.can_leave():return False
        self.stop_live()
        name=filedialog.askopenfilename(parent=self.window,filetypes=[('사진','*.png *.jpg *.jpeg *.bmp *.tif *.webp')])
        if not name:return False
        payload=Path(name).read_bytes()
        with Image.open(io.BytesIO(payload)) as im:image=cv2.cvtColor(np.array(ImageOps.exif_transpose(im).convert('RGB')),cv2.COLOR_RGB2BGR)
        oldroi=self.roi;oldref=self.reference;self.clear_context();self.input_mode='image'
        self.raw=FramePacket('IMPORT','image-input',time.time_ns(),datetime.now(timezone.utc).isoformat(),time.monotonic(),image,source_kind='import')
        if oldroi and oldroi['lot_id']==self.lot.get() and oldroi['image_size']==list(image.shape[1::-1]) and oldroi['camera_id']=='IMPORT':
            self.roi=oldroi;self.reference=oldref
        self.cfg['capture_source']={'kind':'import','original_filename':Path(name).name,'sha256':hashlib.sha256(payload).hexdigest(),'captured_time_kind':'import_time_not_camera_capture'}
        self.draw_scene();self.notice.set('사진 가져옴. 이전 사진과 같은 구도인지 위치 확인 후 분석하세요.')
        return True

    def collect_photo(self):
        if self.loading or self.analyzing or self.identifying or self.calibrating or self.saving or self.repairing:raise ValueError('진행 중 장면 작업 완료 후 사진을 선택하세요.')
        if not self.import_image():return
        self.auto_run=True;self.auto_phase='calibrating';self.ocr.set(True);self.auto.set(True)
        self.auto_status.set('사진의 칸 이름·ㄷ자/사각형 주차선 자동 등록 중…')
        self.submit('calibrate',(self.root,self.raw,self.lot.get(),copy.deepcopy(self.cfg)),(self.generation,self.raw,self.lot.get()))
        self.calibrating=True

    def edit_roi(self):
        self.require_capture_view()
        if not self.can_leave():return
        packet=self.get_packet();self.stop_live()
        editor=RoiEditor(self.window,packet.image,self.lot.get(),(self.roi or {}).get('spaces',[]));self.window.wait_window(editor)
        if not editor.result:return
        self.generation+=1;self.reference=None;self.pred=None;self.record=None;self.rows=[];self.dirty=False
        self.roi=snapshot(self.lot.get(),packet.camera_id,packet.serial,packet.image.shape[1::-1],capture_pose(self.cfg),editor.result)
        save_calibration(self.project,self.roi);self.scene=(packet,copy.deepcopy(self.roi));self.confirmed_epoch=None
        self.scene_group=(self.batch,self.session)
        self.reset_cards();self.draw_scene();self.notice.set('ROI 저장. A 직사각형 / B 사선 모양 그대로 지정됩니다. 위치 확인을 누르세요.')

    def confirm(self):
        if self.input_mode=='saved':raise ValueError('저장 장면은 검수 전용입니다. [카메라 1대 연결] 또는 [사진 열기]로 새 입력을 선택하세요.')
        self.require_capture_view()
        if not self.can_leave():return
        self.disable_automatic('수동 위치 확인 · 자동 수집 정지')
        packet=self.hub.get_latest_frame(self.camera_id) if self.hub and self.input_mode=='camera' else self.get_packet()
        if packet is None:raise ValueError('현재 카메라 영상을 기다리세요.')
        if not self.roi:
            self.roi=load_calibration(self.project,self.lot.get(),packet.camera_id,packet.serial,packet.image.shape[1::-1],capture_pose(self.cfg))
        if not self.roi:raise ValueError('ROI를 먼저 등록하세요.')
        validate_capture(packet,self.roi)
        if self.roi['lot_id']!=self.lot.get():raise ValueError('현재 주차장과 ROI 불일치')
        self.generation+=1;self.live=False;self.pred=None;self.record=None;self.rows=[];self.previous=None;self.dirty=False;self.selected=None
        self.sender.cancel()
        if self.reference and self.reference['epoch']!=packet.stream_epoch:self.reference=None
        self.confirmed_epoch=packet.stream_epoch;self.scene=(packet,copy.deepcopy(self.roi));self.reset_cards();self.draw_scene()
        self.scene_group=(self.batch,self.session)
        self.notice.set(f"위치 확인 · 등록 {len(self.roi['spaces'])}/{layout(self.lot.get())['space_count']}칸. 누락 칸은 미등록으로 표시합니다.")

    def register_reference(self):
        packet=self.hub.get_latest_frame(self.camera_id) if self.hub and self.live else self.get_packet()
        self.check_ready(packet)
        self.stop_live()
        if not messagebox.askyesno('빈 기준 확인','이 원본에서 등록한 모든 칸이 실제로 비어 있습니까?\n차량·손·가림이 있으면 취소하세요.',parent=self.window):return
        self.reference=make_reference(packet,self.roi);identifier=uuid.uuid4().hex
        digest=write_png(self.root/'references'/(identifier+'.png'),packet.image)
        self.reference['record_path']='references/'+identifier+'.json';self.reference['image_sha256']=digest
        atomic_json(self.root/'references'/(identifier+'.json'),{'roi':self.roi,'sha256':digest,'captured_at':packet.timestamp_utc,'confirmed_by':self.reviewer.get(),'image':identifier+'.png'})
        self.generation+=1;self.sender.cancel();self.pred=None;self.notice.set('빈 기준 저장 완료. A안 선택 후 분석하세요. 이동/재연결 시 재등록합니다.')

    def model_settings(self):
        path=filedialog.askopenfilename(parent=self.window,title='OpenVINO 점유 모델 설정 JSON',filetypes=[('JSON','*.json')])
        if path:
            settings=read_json(path);model=Path(settings['model']);settings['model']=str(model.resolve() if model.is_absolute() else (Path(path).parent/model).resolve())
            from vision19 import validate_model_settings
            validate_model_settings(settings)
            self.cfg['occupancy_model']=settings;self.mode.set('B안 분류');self.invalidate_inference()

    def camera_settings(self):
        if not self.hub:raise ValueError('카메라 연결이 필요합니다.')
        if not self.can_leave():return
        from camera_tools import CameraTools
        self.disable_automatic('카메라 설정 변경 · 자동 수집 정지')
        self.live=False;self.confirmed_epoch=None;self.reference=None;self.generation+=1;self.sender.cancel()
        self.scene=None;self.scene_group=None;self.pred=None;self.record=None;self.rows=[];self.previous=None;self.selected=None;self.dirty=False;self.raw=None
        self.occ.set('unknown');self.number.set('');self.color.set('unknown');self.detail.set('카메라 설정 변경 · 위치 재확인 필요');self.detail_image.configure(image='')
        self.update_cards();self.draw_scene()
        tool=CameraTools(self.window,self.hub,self.camera_id,self.project);self.window.wait_window(tool)
        self.scene=None;self.notice.set('설정 후 새 영상에서 위치 확인과 빈 기준 등록을 다시 하세요.')

    def choose_root(self):
        if self.saving or self.reviewing or self.scanning or self.loading or self.analyzing or self.identifying or self.auditing or self.calibrating or self.exporting or self.repairing:raise ValueError('진행 중 작업 완료 후 저장 폴더를 바꾸세요.')
        if not self.can_leave():return
        self.stop_live()
        chosen=filedialog.askdirectory(parent=self.window,initialdir=self.root)
        if chosen:
            self.root=Path(chosen).resolve();self.record=None;self.previous=None;self.rows=[];self.dirty=False;self.generation+=1
            self.cfg['dataset_directory']=str(self.root)
            self.live=False;self.sender.cancel()
            config=copy.deepcopy(self.cfg);config.pop('project',None);atomic_json(self.project/'config.json',config)
            self.notice.set('저장 경로 변경: '+str(self.root));self.start_recovery_if_needed();self.refresh_stats()

    def require_capture_view(self):
        if self.active_view=='unspecified':raise ValueError('과거 자료의 촬영 시점은 미지정입니다. 새 촬영 전에 현재 촬영 시점을 선택하세요.')

    def check_ready(self,packet,allow_relocate=False):
        self.require_capture_view()
        if self.batch_started_at is not None and packet.received_monotonic<=self.batch_started_at:
            raise ValueError('새 촬영 배치 시작 후의 카메라 프레임을 기다리거나 새 사진을 선택하세요.')
        if not self.roi or self.confirmed_epoch!=packet.stream_epoch:raise ValueError('현재 프레임의 ROI 위치 확인이 필요합니다.')
        if self.roi['lot_id']!=self.lot.get():raise ValueError('주차장 불일치')
        if allow_relocate and self.roi.get('registration_method')=='aruco_board_v1':
            from layouts import validate_snapshot
            validate_snapshot(self.roi)
            if (packet.camera_id,packet.serial,list(packet.image.shape[1::-1]))!=(self.roi['camera_id'],self.roi['serial'],self.roi['image_size']):
                raise ValueError('마커 기준의 카메라/해상도가 다릅니다.')
        else:validate_capture(packet,self.roi)
        expected=layout(self.lot.get())['space_count']
        if len(self.roi['spaces'])!=expected:raise ValueError(f'전체 칸 수집 조건: {expected}개 ROI를 모두 등록하세요. 현재 {len(self.roi["spaces"])}개입니다.')

    def analyze_once(self):
        if self.calibrating:raise ValueError('자동 칸 등록 완료 후 분석하세요.')
        if self.analyzing or self.loading:return
        if self.identifying and not self.live:raise ValueError('현재 장면 번호 분석 완료를 기다리세요.')
        if self.dirty:raise ValueError('검수 정답을 먼저 저장하거나 다른 장면으로 전환하세요.')
        packet=self.hub.get_latest_frame(self.camera_id) if self.live and self.hub else self.get_packet();self.check_ready(packet,allow_relocate=True)
        roi=copy.deepcopy(self.roi);cfg=copy.deepcopy(self.cfg);cfg['enable_ocr']=self.ocr.get()
        cfg['auto_anchor_root']=str(self.root)
        mode={'B안 분류':'classifier','A안 빈 기준':'reference','스티커 근거':'sticker','수동 검수':'manual'}[self.mode.get()]
        group=(self.batch,self.session) if self.live else (self.scene_group or (self.batch,self.session))
        token=(self.generation,packet,roi,self.record['image_id'] if self.record and not self.live else None,group)
        self.submit('analyze',(packet,roi,cfg,self.reference,mode),token);self.analyzing=True;self.latest_seq=packet.sequence
        self.notice.set('분석 중 · 현재 결과 영상은 결과가 나온 원본과 고정 연결됩니다.')

    def start_live(self):
        if not self.hub:raise ValueError('실시간 분석은 카메라 연결 후 가능합니다. 사진은 1회 분석하세요.')
        if self.input_mode!='camera':raise ValueError('[카메라 1대 연결]로 현재 카메라 영상으로 전환하세요.')
        if not self.can_leave():return
        packet=self.hub.get_latest_frame(self.camera_id);self.check_ready(packet,allow_relocate=True)
        self.generation+=1;self.sender.cancel();self.dirty=False;self.record=None;self.previous=None;self.rows=[];self.live=True;self.next_analysis=0.;self.latest_seq=None

    def stop_live(self):
        self.disable_automatic('자동 수집 정지 · 저장된 원본/표시사진/JSON 유지')
        self.live=False;self.generation+=1;self.sender.cancel();self.notice.set('현재 표시 장면 고정 · 대기 전송 취소. 이미 전송 중인 HTTP는 회수할 수 없습니다.')

    def disable_automatic(self,message):
        self.auto_run=False;self.auto_phase='off';self.auto.set(False);self.auto_status.set(message)

    def enroll_markers(self):
        self.require_capture_view()
        if self.live or self.analyzing or self.identifying or self.calibrating or self.saving or self.loading or self.repairing or self.reviewing or self.recovering:
            raise ValueError('수집을 정지하고 진행 중 작업을 마친 뒤 기준을 등록하세요.')
        if self.input_mode=='saved':raise ValueError('새 카메라 장면/사진에서 실제 주차칸 ROI를 등록하세요.')
        if not self.can_leave():return
        packet,roi=self.scene if self.scene else (self.get_packet(),self.roi)
        if roi is None:raise ValueError('먼저 사진 자동 등록 또는 ROI 네 모서리로 모든 주차칸을 등록하세요.')
        from marker_setup import template_path
        if template_path(self.root,self.lot.get()).exists():
            if not messagebox.askyesno('마커 기준 교체','현재 표시 장면의 모든 ROI가 실제 주차선과 맞고 마커가 보드에 고정되어 있습니까? 기존 수집 사진은 유지하고 이후 촬영 기준을 교체합니다.'):return
        self.disable_automatic('마커 기준 등록 중');self.generation+=1;self.sender.cancel()
        token=(self.generation,packet,self.lot.get())
        self.submit('enroll',(self.root,packet,copy.deepcopy(roi)),token);self.calibrating=True
        self.marker_status.set('마커 위치·전체 칸 연결 및 기준 저장 중…')

    def start_automatic(self):
        self.require_capture_view()
        if self.analyzing or self.identifying or self.calibrating or self.saving or self.reviewing or self.loading or self.repairing:raise ValueError('진행 중 작업 완료 후 자동 수집을 시작하세요.')
        if not self.can_leave():return
        self.clear_context();self.input_mode='camera'
        if not self.hub:self.connect()
        self.auto_run=True;self.auto_phase='waiting';self.auto.set(True);self.ocr.set(True)
        self.auto_next_setup=time.monotonic()+self.cfg.get('warmup_seconds',3.)
        self.auto_status.set('카메라 영상 대기 → 구도 검사/칸 자동등록 진행 예정')

    def automatic_step(self,packet):
        if not self.auto_run or self.auto_phase!='waiting' or self.calibrating or self.identifying or self.analyzing:return
        if time.monotonic()<self.auto_next_setup:return
        token=(self.generation,packet,self.lot.get())
        self.submit('calibrate',(self.root,packet,self.lot.get(),copy.deepcopy(self.cfg)),token)
        self.calibrating=True;self.auto_phase='calibrating';self.auto_status.set('칸 이름·주차선 탐색 / 이전 구도 대조 중…')

    def save_scene(self):
        if self.saving:return
        if self.identifying and not self.live:raise ValueError('번호 분석 종료 후 장면을 저장하세요.')
        if self.live and self.scene is None:raise ValueError('첫 분석 장면을 기다리세요.')
        packet,roi=self.scene if self.scene else (self.get_packet(),self.roi)
        self.check_ready(packet)
        if self.scene is None:self.scene=(packet,copy.deepcopy(roi))
        cfg=copy.deepcopy(self.cfg)
        if packet.camera_id!='IMPORT':cfg['capture_source']={'kind':packet.source_kind,'captured_time_kind':'host_receive_utc' if packet.source_kind=='camera' else 'replay_receive_utc'}
        batch,session=self.scene_group or (self.batch,self.session)
        token=(self.generation,str(self.root),capture_id(packet,roi,batch,session,cfg))
        saved=self.record['image_id'] if self.record else None
        self.submit('save',(self.root,packet,copy.deepcopy(roi),batch,session,cfg,copy.deepcopy(self.pred),saved),token)
        self.saving=True;self.notice.set('원본·칸 크롭·예측 저장 중… 완료 전 수량에 포함하지 않습니다.')

    def reset_cards(self):
        for child in self.card_area.winfo_children():child.destroy()
        self.cards={}
        columns=6 if self.lot.get()=='A' else 8
        for i in range(8):self.card_area.columnconfigure(i,weight=0)
        for i,s in enumerate(layout(self.lot.get())['spaces']):
            sid=s['space_id'];b=tk.Button(self.card_area,text=sid+'\n미등록',font=('맑은 고딕',9),relief='flat',bg='#e7edf2',command=lambda sid=sid:self.guard(lambda:self.select_slot(sid)))
            b.grid(row=i//columns,column=i%columns,sticky='nsew',padx=1,pady=1);self.cards[sid]=b
        for i in range(columns):self.card_area.columnconfigure(i,weight=1)
        self.update_cards()

    def update_cards(self):
        predictions={r['space_id']:r for r in (self.pred or {}).get('spaces',[])}
        reviews={r['space_id']:r for r in self.rows};registered={s['space_id'] for s in (self.roi or {}).get('spaces',[])}
        counts={'occupied':0,'vacant':0,'unknown':0};num=0;estimates=0;colors=0
        for sid,b in self.cards.items():
            p=predictions.get(sid);r=reviews.get(sid);text=sid+' · '+('미등록' if sid not in registered else '분석 대기');bg='#e7edf2'
            if p:
                occ=p['occupancy'];counts[occ]+=1;num+=bool(p['number']);colors+=p['color']!='unknown'
                estimates+=bool(not p['number'] and p.get('number_estimate'))
                text=f"{sid} {STATE[occ]}\n{displayed_prediction(p,'?')} · {COLOR[p['color']]}"
                bg='#ffe5c2' if p['issues'] else '#d9efe2' if occ=='vacant' else '#dbeafa'
                if p['zone_rule']=='not_allowed':text+=' · ⚠';bg='#ffd6d6'
            if r and r['review_status']=='approved':
                text+=f"\n정답 {STATE[r['occupancy']]} {r['plate_text'] or '-'} {COLOR[r['color']]}"
                if r.get('zone_rule')=='not_allowed':text+=' ⚠';bg='#ffd6d6'
            if self.selected==sid:bg='#b8d4ff'
            b.configure(text=text,bg=bg)
        expected=layout(self.lot.get())['space_count']
        stamp=(self.pred or {}).get('captured_at_utc','분석 전')
        self.summary.set(f"{self.lot.get()}주차장({LOT_NAMES[self.lot.get()]}) · {'직사각형' if self.lot.get()=='A' else '사선'} ROI {len(registered)}/{expected} | AI 점유 {counts['occupied']} · 빈칸 {counts['vacant']} · 보류 {counts['unknown']} | 번호 직접 {num} · 추정 {estimates} · 색 후보 {colors} | 장면 UTC {stamp} | {self.mode.get()}")

    def draw_catalog(self):
        """Display the known layout without treating schematic points as camera ROI."""
        lot=self.lot.get();columns=6 if lot=='A' else 8
        width=max(300,self.canvas.winfo_width());height=max(100,self.canvas.winfo_height())
        self.canvas.create_text(12,10,text=f'{lot}주차장 {columns*2}칸 배치 안내 · 실제 촬영 영상/등록 ROI 아님',fill='#f7da8e',anchor='nw')
        cell=(width-24)/columns;row_height=max(16,(height-65)/2)
        for i,slot in enumerate(layout(lot)['spaces']):
            x=12+(i%columns)*cell;y=38+(i//columns)*row_height
            shift=min(14,cell*.15) if lot=='B' else 0
            points=[(x+shift,y),(x+cell-8,y),(x+cell-8-shift,y+row_height-6),(x,y+row_height-6)]
            self.canvas.create_polygon(points,outline='',fill='#263848',tags=('catalog_slot',slot['space_id']))
            p=points if i<columns else [points[3],points[2],points[1],points[0]]
            self.canvas.create_line(p[3],p[0],p[1],p[2],fill='#62dcb0',width=2)
            self.canvas.create_line(p[2],p[3],fill='#ffc56a',width=2,dash=(5,4))
            self.canvas.create_text(x+cell/2-4,y+row_height/2-3,text=slot['space_id'],fill='white',tags='catalog_name')

    def draw_scene(self):
        if not hasattr(self,'canvas'):return
        packet=self.scene[0] if self.scene else self.raw
        self.canvas.delete('all')
        if packet is None:
            self.draw_catalog();return
        w=max(50,self.canvas.winfo_width());h=max(50,self.canvas.winfo_height());ih,iw=packet.image.shape[:2]
        self.scale=min(w/iw,h/ih);self.offset=((w-iw*self.scale)/2,(h-ih*self.scale)/2)
        picture=Image.fromarray(cv2.cvtColor(packet.image,cv2.COLOR_BGR2RGB));picture.thumbnail((w,h))
        self.photos['main']=ImageTk.PhotoImage(picture);self.canvas.create_image(*self.offset,image=self.photos['main'],anchor='nw')
        preds={r['space_id']:r for r in (self.pred or {}).get('spaces',[])}
        overlay_roi=self.roi or {}
        if overlay_roi.get('registration_method')=='aruco_board_v1':
            f=overlay_roi['marker_evidence']['frame']
            if (f['frame_id'],f['timestamp'],f['stream_epoch'])!=(packet.sequence,packet.timestamp_utc,packet.stream_epoch):overlay_roi={}
        for marker in overlay_roi.get('marker_evidence',{}).get('observed',[]):
            pts=[(x*self.scale+self.offset[0],y*self.scale+self.offset[1]) for x,y in marker['corners']]
            self.canvas.create_polygon(pts,outline='#f3a0ff',fill='',width=2)
            self.canvas.create_text(*pts[0],text='ID '+str(marker['id']),fill='#f3a0ff',anchor='sw')
        for slot in overlay_roi.get('spaces',[]):
            points=[(x*self.scale+self.offset[0],y*self.scale+self.offset[1]) for x,y in slot['polygon']];p=preds.get(slot['space_id'])
            color='#ff6868' if p and (p['zone_rule']=='not_allowed' or any('걸침' in v or '경계' in v for v in p['issues'])) else '#62dcb0'
            line_width=3 if slot['space_id']==self.selected else 2
            if slot.get('boundary_kind')=='open_u':
                self.canvas.create_line(points[3],points[0],points[1],points[2],fill=color,width=line_width)
                self.canvas.create_line(points[2],points[3],fill='#ffc56a',width=line_width,dash=(5,4))
            else:self.canvas.create_polygon(points,outline=color,fill='',width=line_width)
            label=slot['space_id']+(f" {STATE[p['occupancy']]}\n{displayed_prediction(p,'?')} {COLOR[p['color']]}" if p else ' 대기')
            self.canvas.create_text(*points[0],text=label,fill='#ffff99',anchor='nw',font=('맑은 고딕',10,'bold'))
        for warning in (self.pred or {}).get('geometry',[]):
            x1,y1,x2,y2=warning['bbox'];self.canvas.create_rectangle(x1*self.scale+self.offset[0],y1*self.scale+self.offset[1],x2*self.scale+self.offset[0],y2*self.scale+self.offset[1],outline='#ffb14a',dash=(4,3),width=2)

    def canvas_select(self,event):
        if not self.roi or not hasattr(self,'scale'):return
        x=(event.x-self.offset[0])/self.scale;y=(event.y-self.offset[1])/self.scale
        for slot in self.roi['spaces']:
            if cv2.pointPolygonTest(np.asarray(slot['polygon'],np.float32),(x,y),False)>=0:
                self.guard(lambda:self.select_slot(slot['space_id']));return

    def select_slot(self,sid):
        if self.reviewing:return
        self.flush_fields();self.selected=sid
        slot=next((s for s in (self.roi or {}).get('spaces',[]) if s['space_id']==sid),None)
        p=next((r for r in (self.pred or {}).get('spaces',[]) if r['space_id']==sid),None)
        r=next((r for r in self.rows if r['space_id']==sid),None)
        self.occ.set(r['occupancy'] if r else 'unknown');self.number.set(r['plate_text'] if r else '');self.color.set(r['color'] if r else 'unknown')
        self.review_state.set('검수 승인' if r and r['review_status']=='approved' else '미검수 / 장면 저장 후 수정 가능')
        text=sid+' · '+(TYPE_NAMES[slot['space_type']] if slot else 'ROI 미등록')
        if p:
            text+=f"\nAI: {STATE[p['occupancy']]} / 번호 {displayed_prediction(p,'?')} / {COLOR[p['color']]}\n"+(p['occupancy_reason'] or '')+'\n'+(' · '.join(p['issues']) or '종류 비교 이상 없음 / 위치는 별도 확인')+'\n'+p['geometry_status']
            quality=p.get('quality',{})
            text+='\n품질 참고: '+(' · '.join(quality.get('warnings',[])) or '지표 경고 없음 / 육안 확인 필요')
            if r and r['review_status']=='approved':
                mismatches=[]
                if p['occupancy']!=r['occupancy']:mismatches.append('점유')
                estimate = (p.get('number_estimate') or {}).get('number') if not p['number'] else None
                if (p['number'] or estimate or '')!=r['plate_text']:mismatches.append('추정 번호' if estimate else '번호')
                if p['color']!=r['color']:mismatches.append('스티커색')
                text+='\n정답 비교: '+(', '.join(mismatches)+' 불일치' if mismatches else '승인한 항목과 일치')
                if estimate and estimate==r['plate_text']:text+=' · 추정 번호 일치'
        self.detail.set(text)
        packet=self.scene[0] if self.scene else self.raw
        if packet is not None and slot:
            crop,_,_=crop_space(packet.image,slot['polygon'],False);pic=Image.fromarray(cv2.cvtColor(crop,cv2.COLOR_BGR2RGB));pic.thumbnail((310,130));self.photos['detail']=ImageTk.PhotoImage(pic);self.detail_image.configure(image=self.photos['detail'])
        else:self.detail_image.configure(image='')
        self.update_cards();self.draw_scene()

    def require_review(self):
        if self.live:raise ValueError('정지/장면 고정 후 저장하고 검수하세요.')
        if self.identifying:raise ValueError('번호 분석 종료 후 검수하세요.')
        if not self.record or not self.selected:raise ValueError('장면 저장 완료 후 검수할 칸을 선택하세요.')
        if self.reviewing:raise ValueError('검수 저장 중입니다.')
        self.flush_fields()
        return next(r for r in self.rows if r['space_id']==self.selected)

    def copy_prediction(self):
        r=self.require_review();p=next((x for x in (self.pred or {}).get('spaces',[]) if x['space_id']==self.selected),None)
        if not p:raise ValueError('자동 예측이 없습니다.')
        estimate=p.get('number_estimate') or {}
        r.update(occupancy=p['occupancy'],plate_text=p['number'] or estimate.get('number',''),color=p['color'],
                 plate_bbox=p['plate_bbox'] or estimate.get('text_bbox'),sticker_polygon=p['sticker_polygon'],review_status='pending')
        self.occ.set(r['occupancy']);self.number.set(r['plate_text']);self.color.set(r['color']);self.dirty=True;self.select_slot(self.selected)

    def edit_sticker(self):
        r=self.require_review();slot=next(s for s in self.roi['spaces'] if s['space_id']==self.selected)
        e=RoiEditor(self.window,self.scene[0].image,self.lot.get(),sticker_only=True,focus_polygon=slot['polygon']);self.window.wait_window(e)
        if e.result:
            from analysis import color_candidate
            r['sticker_polygon']=e.result;r['color']=color_candidate(self.scene[0].image,e.result)['name'];r['review_status']='pending';self.color.set(r['color']);self.dirty=True;self.select_slot(self.selected)

    def edit_number_box(self):
        r=self.require_review();e=RoiEditor(self.window,self.scene[0].image,self.lot.get(),box_only=True);self.window.wait_window(e)
        if e.result:r['plate_bbox']=e.result;r['review_status']='pending';self.dirty=True

    def persist_review(self,approve):
        r=self.require_review()
        if r['occupancy']=='vacant':r.update(plate_text='',color='unknown',plate_bbox=None,sticker_polygon=None);self.number.set('');self.color.set('unknown')
        submitted=copy.deepcopy(self.rows)
        next(row for row in submitted if row['space_id']==self.selected)['review_status']='approved' if approve else 'pending'
        self.submit('review',(self.root,copy.deepcopy(self.record),submitted,self.reviewer.get(),review_revision(self.previous)),(self.generation,self.record['image_id']))
        self.reviewing=True;self.lock_review(True)

    def lock_review(self,locked):
        if not locked:
            for widget,state in self.review_widget_states:
                if widget.winfo_exists():widget.configure(state=state)
            self.review_widget_states=[];return
        def visit(parent):
            for child in parent.winfo_children():
                visit(child)
                if isinstance(child,(ttk.Entry,ttk.Combobox,ttk.Button)):
                    self.review_widget_states.append((child,child.cget('state')));child.configure(state='disabled')
        visit(self.review_panel)

    def refresh_stats(self,force=False):
        if force:self.audit_scope=None
        scope=(str(self.root),self.scope.get(),self.lot.get() if self.scope.get()=='현재 주차장' else None,self.session if self.scope.get()=='현재 세션' else None)
        if self.audit_scope!=scope:
            self.audit_result=None;self.audit_text.set('품질·정답 대조: 현재 범위에서 다시 실행하세요.')
            self.view_audit.set('시점별 정답 대조: 현재 범위에서 다시 실행하세요.')
        if self.scanning or self.closing:return
        lot=self.lot.get() if self.scope.get()=='현재 주차장' else None;session=self.session if self.scope.get()=='현재 세션' else None
        self.submit('scan',(self.root,lot,session,force),(str(self.root),self.scope.get(),lot,session));self.scanning=True

    def file_selected(self,event=None):
        selected=self.files.selection()
        if selected and self.scan_result:
            entry=next((e for e in self.scan_result['entries'] if e['id']==selected[0]),None)
            if entry:self.path_text.set(entry['raw_path'])

    def open_raw(self):
        if self.files.selection():os.startfile(self.path_text.get())
    def copy_path(self):self.window.clipboard_clear();self.window.clipboard_append(self.path_text.get())
    def show_errors(self):
        errors=list((self.scan_result or {}).get('errors',[]))+(self.recovery_result or {}).get('복구보류',[])
        messagebox.showinfo('파일 대조 오류',json.dumps(errors,ensure_ascii=False,indent=2)[:8000] if errors else '최근 대조에서 오류 없음',parent=self.window)

    def open_selected(self):
        if self.loading:raise ValueError('현재 장면을 여는 중입니다.')
        selected=self.files.selection()
        if not selected or not self.can_leave():return
        self.stop_live();self.dirty=False;self.submit('load',(self.root,selected[0]));self.loading=True

    def open_latest_lot(self):
        if self.loading or self.saving or self.repairing:raise ValueError('진행 중 장면 작업 완료 후 열어주세요.')
        if not self.can_leave():return
        self.clear_context();self.input_mode='saved';self.active_lot=self.lot.get()
        self.submit('load_latest',(self.root,self.lot.get()),(self.generation,str(self.root),self.lot.get()));self.loading=True
        self.notice.set(f'{self.lot.get()}주차장 저장 장면을 현재 폴더에서 찾는 중…')

    def repair_selected(self):
        selected=self.files.selection()
        if not selected:raise ValueError('복구할 장면을 파일 목록에서 선택하세요.')
        self.flush_fields()
        if self.dirty:raise ValueError('수정한 정답을 먼저 저장하세요.')
        if self.saving or self.reviewing or self.loading or self.repairing or self.exporting:raise ValueError('진행 중 파일 작업 완료 후 복구하세요.')
        self.stop_live();self.submit('repair',(self.root,selected[0],copy.deepcopy(self.cfg)),(str(self.root),selected[0]));self.repairing=True
        self.notice.set('원본·저장 예측·검수 근거를 대조하여 파생 파일 복구 중…')

    def new_batch(self):
        if self.live or self.analyzing or self.identifying or self.saving or self.calibrating or self.repairing:raise ValueError('정지 후 등록·분석·저장 완료를 기다린 다음 새 촬영 배치를 시작하세요.')
        if not self.can_leave():return
        self.disable_automatic('새 촬영 배치 · 자동 수집 정지')
        self.generation+=1;self.sender.cancel();self.scene=None;self.scene_group=None;self.pred=None;self.record=None;self.rows=[];self.previous=None;self.dirty=False
        self.raw=None;self.latest_occupancy_key=None;self.latest_seq=None
        self.batch_started_at=time.monotonic();self.batch='batch-'+uuid.uuid4().hex[:12]
        self.selected=None;self.detail_image.configure(image='');self.detail.set('새 배치의 칸을 선택하세요.');self.review_state.set('미검수')
        self.notice.set('새 장면 그룹 '+self.batch+' · 새 카메라 프레임 또는 새로 가져온 사진부터 저장합니다. 시점만 바꾸면 같은 배치를 유지하세요.')
        self.update_cards();self.draw_scene()

    def toggle_send(self):
        self.sender.cancel()
        self.api_status.set('다음 유효 관측부터 전송' if self.send_enabled.get() else 'API 꺼짐 · 대기/재시도 취소 (진행 중 HTTP 회수 불가)')

    def export_current(self,kind):
        if self.recovering:raise ValueError('중단 촬영 복구 완료 후 내보내세요.')
        if self.reviewing or self.repairing:raise ValueError('정답 저장·복구 완료 후 내보내세요.')
        self.flush_fields()
        if self.dirty:raise ValueError('수정한 정답을 저장한 다음 내보내기·대조를 실행하세요.')
        if kind=='audit' and self.auditing:return
        lot=self.lot.get() if self.scope.get()=='현재 주차장' else None
        session=self.session if self.scope.get()=='현재 세션' else None
        if kind=='audit':
            self.submit(kind,(self.root,lot,session),(str(self.root),self.scope.get(),lot,session));self.auditing=True
            self.audit_text.set('파일·정답·예측 대조 중…');return
        if self.exporting:raise ValueError('현재 내보내기가 끝날 때까지 기다리세요.')
        self.submit(kind,(self.root,lot,session));self.exporting=True
        self.notice.set(self.scope.get()+(' 품질 통과·승인 학습 자료' if kind=='export' else ' 품질 통과 자료' if kind=='selected_export' else ' 자동 수집 자료' if kind=='collection_export' else ' 승인 자료')+' 내보내기 중…')
        self.export_status.set(self.notice.get()+' · 완료 수량 집계 전')

    def send_prediction(self,packet,roi,result):
        if not (self.live and self.input_mode=='camera' and self.send_enabled.get() and not self.closing):return
        from transport19 import build_events
        try:
            config=copy.deepcopy(self.cfg);config['capture_source']={'kind':packet.source_kind}
            events=build_events(packet,roi,result,config)
            if events:self.sender.offer(events,config.get('event_max_age_seconds',2.))
        except Exception as exc:self.api_status.set('전송 보류: '+str(exc))

    def publish_scene(self,token,result,autosave=True):
        generation,packet,roi,existing=token[:4]
        self.scene=(packet,roi);self.roi=roi;self.pred=result
        self.scene_group=token[4] if len(token)>4 else (self.batch,self.session)
        if not existing:self.record=None;self.rows=[];self.previous=None
        self.update_cards();self.draw_scene()
        if self.selected:self.select_slot(self.selected)
        self.notice.set(f"표시 장면 UTC {packet.timestamp_utc} · 분석 {result['elapsed_ms']:.0f} ms · "+(' / '.join(result['errors']) or '후보는 검수 후 승인하세요.'))
        if autosave and not self.closing and self.auto.get() and not self.saving and time.monotonic()>=self.next_save:
            self.save_scene();self.next_save=time.monotonic()+self.cfg['save_interval']

    def accept(self,kind,token,result,error):
        if kind in ('export','preview_export','collection_export','selected_export'):self.exporting=False
        if kind=='recover':self.recovering=False
        if kind=='repair':self.repairing=False
        if kind in ('calibrate','enroll'):self.calibrating=False
        if kind=='analyze':self.analyzing=False
        if kind=='identify':self.identifying=False
        if kind=='save':self.saving=False
        if kind=='scan':self.scanning=False
        if kind=='review':
            self.reviewing=False;self.lock_review(False)
            if token is not None and token!=(self.generation,self.record['image_id'] if self.record else None):return
        if kind in ('load','load_latest'):self.loading=False
        if kind=='audit':self.auditing=False
        if kind=='load_latest' and token!=(self.generation,str(self.root),self.lot.get()):return
        if kind=='load' and token!=self.generation:return
        if error:
            if kind in ('enroll','calibrate','analyze') and token[0]==self.generation:self.marker_status.set('좌표 확인 보류: '+error)
            if kind in ('export','preview_export','collection_export','selected_export'):self.export_status.set('내보내기 실패 · 완료 수량으로 집계하지 않음: '+error)
            if kind in ('save','review','repair'):self.audit_scope=None;self.refresh_stats(True)
            if kind=='analyze' and token[0]==self.generation and self.auto_run:
                self.live=False;self.generation+=1;self.sender.cancel();self.confirmed_epoch=None
                self.scene=None;self.pred=None;self.record=None;self.roi=None;self.rows=[]
                self.auto_phase='waiting';self.auto_next_setup=time.monotonic()+5.
                self.auto_status.set('분석/구도 검사 보류 · 5초 후 자동 재등록: '+error)
            if kind=='save' and self.auto_run:
                self.stop_live();self.auto_status.set('저장 실패로 자동 수집 정지 · 경로/디스크 오류 확인: '+error)
            if kind=='calibrate' and token[0]==self.generation and self.auto_run:
                if self.input_mode=='image':self.disable_automatic('사진 자동등록 실패: '+error)
                else:self.auto_phase='waiting';self.auto_next_setup=time.monotonic()+5.;self.auto_status.set('자동등록 보류: '+error+' · 재시도 예정')
            if kind=='audit':self.audit_text.set('품질·정답 대조 실패: '+error)
            if kind=='review':
                confirmed={r['space_id'] for r in (self.previous or {}).get('spaces',[]) if r['review_status']=='approved'}
                for row in self.rows:
                    if row['space_id'] not in confirmed:row['review_status']='pending'
                self.dirty=True;self.update_cards();self.review_state.set('저장 실패 · 수정 내용 재검토 후 다시 저장')
            self.notice.set(kind+' 실패: '+error);return
        if kind=='enroll':
            if token[0]!=self.generation or token[2]!=self.lot.get():return
            self.roi=result['roi'];self.scene=(token[1],self.roi);self.confirmed_epoch=token[1].stream_epoch
            self.pred=None;self.record=None;self.previous=None;self.rows=[];self.reference=None;self.scene_group=(self.batch,self.session)
            self.marker_status.set(f"마커 기준 등록 완료 · {result['marker_count']}개 · 자동 수집 시작으로 좌표 갱신/수집")
            self.reset_cards();self.draw_scene();return
        if kind=='recover':
            if token!=str(self.root):return
            self.recovery_result=result
            self.notice.set(f"재시작 복구: 완료 {result['복구완료수']} · 보류 {result['복구보류수']} (보류 사유는 오류 상세)")
            self.refresh_stats(True);return
        if kind=='load_latest':
            warnings=result['warnings'];scene=result['scene']
            if scene is None:
                self.notice.set(f"{self.lot.get()}주차장에 열 수 있는 저장 장면이 없습니다. 이전 datasets 폴더를 선택하거나 새로 촬영하세요."+(' 제외: '+' / '.join(warnings[:3]) if warnings else ''))
                self.draw_scene();return
            self.accept('load',self.generation,scene,None)
            self.notice.set(self.notice.get()+(' · 이전 자료 제외: '+' / '.join(warnings[:3]) if warnings else ''))
        elif kind=='repair':
            record,receipt=result
            if token[0]==str(self.root) and self.record and self.record['image_id']==record['image_id']:self.record=record
            self.notice.set('저장 복구 '+receipt['status']+' · '+record['image_id']);self.audit_scope=None;self.refresh_stats(True)
        elif kind=='calibrate':
            if token[0]!=self.generation or not self.auto_run or token[2]!=self.lot.get():return
            packet=token[1]
            current=self.hub.get_latest_frame(self.camera_id) if self.hub and self.input_mode=='camera' else packet
            if current is None or current.stream_epoch!=packet.stream_epoch:
                self.auto_phase='waiting';self.auto_next_setup=time.monotonic()+3.;return
            if not result['ready']:
                self.auto_candidates=result.get('spaces',[]);self.auto_phase='waiting';self.auto_next_setup=time.monotonic()+5.
                self.auto_status.set(f"칸 자동등록 {result.get('detected_count',0)}/{result.get('expected_count',0)} · 미확인 {', '.join(result.get('missing',[]))} · 5초 후 재탐색")
                if self.input_mode=='image':self.disable_automatic(f"사진 자동등록 보류 {result.get('detected_count',0)}/{result.get('expected_count',0)}칸 · 주차선/이름 확인 후 다른 사진을 선택하세요.")
                self.notice.set('주차선과 칸 이름이 전부 보이도록 구도를 조정하세요. 진단: '+result.get('diagnostic_path',''));return
            self.roi=result['roi'];self.confirmed_epoch=packet.stream_epoch;self.scene=None;self.scene_group=None
            self.marker_status.set('ArUco 좌표 자동 갱신 사용' if self.roi.get('registration_method')=='aruco_board_v1' else '마커 기준 없음 · 기존 주차선/칸 이름 등록 사용')
            self.record=None;self.pred=None;self.rows=[];self.previous=None;self.reference=None;self.latest_seq=None
            self.mode.set('B안 분류' if self.cfg.get('occupancy_model') else '스티커 근거')
            self.live=True;self.next_analysis=0.;self.next_save=0.;self.auto_phase='collecting';self.reset_cards()
            if self.input_mode=='image':
                self.live=False;self.auto_run=False;self.auto_phase='photo';self.scene=(packet,self.roi);self.scene_group=(self.batch,self.session)
                self.auto_status.set(f"사진 등록 완료 · {self.lot.get()} {len(self.roi['spaces'])}칸 · 1회 분석 후 사진·JSON 저장")
                self.analyze_once();return
            self.auto_status.set(f"자동 수집 중 · {self.lot.get()} {len(self.roi['spaces'])}칸 · "+('점유 모델 사용' if self.cfg.get('occupancy_model') else '스티커 근거 점유 후보 / 빈칸 판단 보류'))
        elif kind=='analyze':
            generation,packet,roi,existing=token[:4]
            if generation!=self.generation:return
            if roi.get('registration_method')=='aruco_board_v1':
                s=roi['marker_evidence']['statistics']
                self.marker_status.set(f"ArUco {s['marker_count']}개 · 좌표오차 {s['maximum_error_px']:.2f}px · 프레임 {packet.sequence} · 번호/색은 별도 판독")
            self.latest_occupancy_key=observation_key(packet,roi);self.send_prediction(packet,roi,result)
            if self.ocr.get() and not self.closing:
                if not self.identifying:
                    self.submit('identify',(packet,roi,copy.deepcopy(self.cfg),result),token);self.identifying=True
                    if self.pred is None:self.publish_scene(token,result,False)
                    self.notice.set('번호 분석 중 · 최신 점유 분석/전송은 별도 진행, 화면은 원래 장면에 고정됩니다.')
            else:self.publish_scene(token,result)
        elif kind=='identify':
            if token[0]!=self.generation:return
            packet,roi=token[1:3]
            # Do not send a late identity result over a newer occupancy observation.
            if self.latest_occupancy_key==observation_key(packet,roi):self.send_prediction(packet,roi,result)
            self.publish_scene(token,result)
        elif kind=='save':
            record,receipt=result
            if receipt['status']!='complete' and self.auto_run:
                self.stop_live();self.auto_status.set('일부 파일 저장 실패 · 원본 보존 · 오류 확인 후 재시작')
            group=self.scene_group or (self.batch,self.session)
            if self.scene and token==(self.generation,str(self.root),capture_id(*self.scene,*group,self.cfg)):
                self.record=record
                if not self.rows:self.rows=rows_for_edit(record);self.previous=None
            self.notice.set('저장 '+receipt['status']+' · '+record['raw_path']+(' / '+str(receipt['errors']) if receipt['errors'] else ''))
            self.audit_scope=None;self.refresh_stats()
        elif kind=='scan':
            if token!=(str(self.root),self.scope.get(),self.lot.get() if self.scope.get()=='현재 주차장' else None,self.session if self.scope.get()=='현재 세션' else None):
                self.refresh_stats();return
            self.scan_result=result;c=result['counts']
            import shutil
            free=shutil.disk_usage(self.root).free/1073741824
            self.storage.set(f"{self.root} | {self.scope.get()} 유효자료 | 촬영장면 {c.get('capture_scenes',c['originals'])} · 원본 파일 {c['originals']} · 칸파일 {c['spaces']} · 스티커파일 {c['stickers']} · 라벨파일 {c['label_files']}/행 {c['label_rows']} | 라벨승인 점유/번호/색 {c['occupancy_approved']}/{c['number_approved']}/{c['color_approved']} · 승인 크롭 조건 {c['occupancy_ready']}/{c['number_ready']}/{c['color_ready']} · 품질 통과·승인 학습 가능 {c['selected_occupancy_ready']}/{c['selected_number_ready']}/{c['selected_color_ready']} | 이미지 {c['bytes']/1048576:.1f} MB · 여유 {free:.1f} GB · 오류 {len(result['errors'])}")
            self.storage.set(self.storage.get()+f" | 칸명 표시사진/JSON {c['annotated_spaces']}/{c['korean_labels']} · 완성 짝 {c['collection_pairs']} · 전체 표시장면 {c['annotated_scenes']}")
            self.storage.set(self.storage.get()+f" | 정보완성 {c['information_complete']} / 미확인 {c['needs_attention']} · 화질주의 {c['quality_warning_labels']} (정답률 아님)")
            self.storage.set(self.storage.get()+f" | 품질 선별 장면: 통과 {c['quality_passed']} / 보류 {c['quality_held']} / 미판정 {c['quality_unassessed']} (정답률 아님)")
            if c.get('quality_recheck_required'):self.storage.set(self.storage.get()+f" | 이전 기준 재검사 {c['quality_recheck_required']}장 · 선택 장면 저장 복구로 재검사")
            views=result.get('views',view_counts(result['entries']))
            self.view_status.set('천장 우선 · 시점별 장면 수 / 통과 / 보류: '+ ' | '.join(f"{name} {views[code]['장면수']}/{views[code]['통과']}/{views[code]['보류']}" for code,name in VIEW_NAMES.items()))
            selected=self.files.selection()
            for item in self.files.get_children():self.files.delete(item)
            for entry in result['entries'][:200]:self.files.insert('', 'end',iid=entry['id'],text=entry['id']+'.png',values=(entry['lot'],entry['time'],entry['spaces'],entry['stickers'],entry['approved'],entry['state']+' / '+entry.get('quality_selection','미판정')+' / '+VIEW_NAMES[entry.get('capture_view','unspecified')]))
            if selected and self.files.exists(selected[0]):self.files.selection_set(selected)
        elif kind=='review':
            saved,warning=result
            self.previous=saved;self.rows=copy.deepcopy(saved['spaces']);self.dirty=False
            selected=self.selected;self.selected=None;self.select_slot(selected)
            self.notice.set('검수 정답 저장 완료 · '+(warning or '자동 예측은 별도로 보존됩니다.'));self.audit_scope=None;self.refresh_stats()
        elif kind=='load':
            if token!=self.generation:return
            record,image,pred,previous=result;self.record=record;self.pred=pred;self.previous=previous;self.rows=rows_for_edit(record,previous)
            recorded_view=view_code(record)
            self.active_view=recorded_view;self.cfg['capture_view']=recorded_view;self.view.set(VIEW_NAMES[recorded_view])
            self.lot.set(record['lot_id']);self.active_lot=record['lot_id'];self.roi=record['roi'];self.reference=None;self.input_mode='saved'
            packet=FramePacket(record['camera_id'],record['serial'],record['frame_id'],record['captured_at_utc'],time.monotonic(),image,stream_epoch=record.get('stream_epoch',0),source_kind=record['source']['kind'],sensor_frame=record.get('sensor_frame'))
            self.raw=None;self.scene=(packet,self.roi);self.confirmed_epoch=None;self.selected=None;self.reset_cards();self.draw_scene()
            self.detail.set('칸을 선택하세요.');self.detail_image.configure(image='');self.occ.set('unknown');self.number.set('');self.color.set('unknown');self.dirty=False
            self.notice.set(f"{record['lot_id']}주차장 저장 장면 · {record['source']['kind']} 출처 · ROI {len(self.roi['spaces'])}/{layout(record['lot_id'])['space_count']}칸 · 원본 촬영 시각 기준. 새 분석은 카메라/사진 입력으로 전환하세요.")
            self.scene_group=(record['batch_id'],record['session_id'])
            self.refresh_stats()
        elif kind in ('export','preview_export'):
            self.notice.set(f'내보내기 완료 {result[1]}개 · {result[0]}');self.export_status.set(self.notice.get());self.refresh_stats()
        elif kind in ('collection_export','selected_export'):
            path,report=result;c=report['counts']
            self.export_status.set(f"내보내기 복사 완료: 장면 {c['scenes']} · 주차칸 사진/JSON 짝 {c['space_pairs']} · 이미지 {c['images']}(원본 {c['originals']} 포함) · JSON {c['json_files']} · 제외 {len(report['skipped'])} | {path}")
            self.notice.set('칸별 사진·한글 JSON 내보내기 완료. 원본은 유지됩니다.');self.refresh_stats()
        elif kind=='audit':
            current=(str(self.root),self.scope.get(),self.lot.get() if self.scope.get()=='현재 주차장' else None,self.session if self.scope.get()=='현재 세션' else None)
            if token!=current:return
            path,report=result;self.audit_scope=token;self.audit_result=report
            names={'occupancy':'점유','number':'번호','color':'색'}
            summary=[]
            for task,m in report['metrics'].items():
                summary.append(names[task]+(' 평가 자료 없음' if not m['compared'] else f" 일치 {m['correct']}/{m['compared']} · 보류 {m['abstained']}"))
            self.audit_text.set(' / '.join(summary)+f" | 비교 제외 {sum(report['unmatched_truth_counts'].values())} · 파일 오류 {len(report['errors'])} · 저장 진단")
            def metric_text(m):return '자료 없음' if m['accuracy_including_abstention'] is None else f"{m['accuracy_including_abstention']*100:.1f}% (정답 {m['compared']})"
            self.view_audit.set('시점별 보류 포함 대조 [점유/번호/색]: '+ ' | '.join(VIEW_NAMES[v]+': '+ '/'.join(metric_text(item['metrics'][t]) for t in ('occupancy','number','color')) for v,item in report.get('views',{}).items()))
            self.notice.set('품질·정답 대조 보고서: '+path+' (같은 이름의 Markdown 포함)')

    def poll(self):
        try:
            for worker in (self.worker,self.io_worker,self.identity_worker):
                while True:
                    try:item=worker.results.get_nowait()
                    except queue.Empty:break
                    self.accept(*item)
            while True:
                try:receipt=self.sender.results.get_nowait()
                except queue.Empty:break
                self.api_status.set('접수 결과 '+str(receipt)+' · 서버 상태/세션 확정은 별도 조회 필요')
            if self.closing:
                if not self.worker.is_alive() and not self.io_worker.is_alive() and not self.identity_worker.is_alive() and not self.sender.is_alive() and (not self.hub or self.hub.is_stopped()):self.window.destroy();return
            elif self.hub:
                packet=self.hub.get_latest_frame(self.camera_id)
                info=self.hub.statuses()[self.camera_id];self.camera_status.set(str(info.get('status',''))+' '+str(info.get('last_error') or ''))
                if packet is None and self.input_mode=='camera':
                    if self.confirmed_epoch is not None or self.live:self.generation+=1;self.sender.cancel()
                    self.confirmed_epoch=None;self.reference=None;self.live=False
                    if self.auto_run:self.auto_phase='waiting';self.auto_next_setup=time.monotonic()+3.;self.auto_status.set('연결 대기 · 복구 후 자동 구도 재검사')
                elif packet is not None:
                    if self.input_mode=='camera' and self.confirmed_epoch is not None and packet.stream_epoch!=self.confirmed_epoch:
                        self.confirmed_epoch=None;self.reference=None;self.live=False;self.generation+=1;self.sender.cancel();self.notice.set('재연결/설정 변경 · 위치와 빈 기준 다시 확인')
                        if self.auto_run:self.auto_phase='waiting';self.auto_next_setup=time.monotonic()+3.
                    if self.input_mode=='camera':
                        self.raw=packet
                        if packet.source_kind!='camera':self.camera_status.set('녹화 재생 · 운영 전송 불가')
                        if self.scene is None:self.draw_scene()
                        self.automatic_step(packet)
                    if self.live and not self.analyzing and time.monotonic()>=self.next_analysis and packet.sequence!=self.latest_seq:
                        self.analyze_once();self.next_analysis=time.monotonic()+self.cfg.get('analysis_interval',1.)
        except Exception as exc:
            logging.exception('poll');self.notice.set('상태 처리 오류: '+str(exc));self.stop_live()
            self.auto_status.set('상태 처리 오류로 자동 수집 정지: '+str(exc))
        self.window.after(120,self.poll)

    def close(self):
        if self.closing:return
        if not self.can_leave():return
        self.closing=True;self.live=False;self.auto.set(False)
        self.auto_run=False
        self.sender.stop_event.set();self.sender.cancel()
        if self.hub:self.hub.request_stop()
        self.notice.set('현재 분석·저장 완료 후 종료합니다…')
        def stop(worker):
            try:worker.jobs.put_nowait(None)
            except queue.Full:self.window.after(200,lambda:stop(worker))
        stop(self.worker);stop(self.io_worker);stop(self.identity_worker)
