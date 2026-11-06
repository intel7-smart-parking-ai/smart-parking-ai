"""Default two-camera console. The Tk thread never runs recognition or writes a scene."""
import copy,time,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import cv2
from PIL import Image,ImageTk
from camera_hub import CameraHub,find_realsense_devices
from capture_views import VIEW_NAMES
from collection_output import export_collection
from dataset19 import export_training
from dual_capture import CHANNELS,ChannelAnalyzer,SharedReader,PairHold,RegistrationHold,alignment_root,profile_specs,recover_pairs,save_pair,synchronized,continuous_pair
from empty_pair import EmptyView, disc_evidence, store_empty_pair
from layouts import layout,snapshot,TYPE_NAMES
from parking_ai.storage import atomic_json
from pair_diagnostics import save_held_pair
from collection_run import CollectionRun
from sticker_guide import waiting_for_sticker
from dual_capture import active_batches
from contracts import displayed_prediction,FIELD_SET
from pipeline import read_json

NAMES={'A':'카메라 1','B':'카메라 2'}
OCC={'vacant':'빈칸','occupied':'점유','unknown':'보류'}
COLOR={'white':'흰색','green':'초록','blue':'파랑','unknown':'미확인'}

def has_registration_view(outcomes):
    return any(isinstance(value,PairHold) for value in outcomes.values())


class PartialReferenceHold(PairHold):
    def __init__(self,message,frames,outcomes,settings):
        super().__init__(message)
        self.frames,self.outcomes,self.settings=frames,outcomes,settings


class DualDashboard:
    run_record=None
    def __init__(self,window,cfg,root,project):
        self.window=window;self.project=Path(project);self.root=Path(root).resolve()
        self.cfg={**cfg,'project':str(self.project)};self.filename=self.project/'dual_cameras.json'
        self.profile=read_json(self.filename);profile_specs(self.profile)
        self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix='parking-dual')
        reader=SharedReader(self.project,self.cfg)
        self.engines={k:ChannelAnalyzer(reader) for k in CHANNELS};self.hub=None;self.specs={};self.running=False
        self.last_keys={};self.jobs={};self.write_job=None;self.extra=None;self.closing=False;self.detail=None
        self.empty_references={}
        self.auto_guide=tk.BooleanVar(value=cfg.get('physical_sticker_set')==FIELD_SET)
        self.guide_jobs={};self.guide_last={};self.guide_next={}
        self.manual_views={}
        self.displayed_frames={};self.analysis_frames={}
        self.registration_views={}
        self.one_shot=False;self.keep_first_hold=False
        self.pending_capture=None
        self.empty_start_cancelled=False
        self.session=uuid.uuid4().hex;self.batches={};self.next_pair=0.;self.last_display={}
        self.total=0;self.held=0;self.run_count=0;self.bays={k:0 for k in CHANNELS};self.frames={};self.photos={};self.controls=[];self.last_labels={}
        self.note=tk.StringVar(value='F5를 누르면 두 카메라를 연결하고 자동으로 수집·저장합니다.')
        self.storage=tk.StringVar();self.serial={};self.lot={};self.view={};self.serial_boxes={};self.panels={};self.tables={};self.status={}
        self.skew=tk.StringVar(value=str(self.profile['max_skew_ms']));self.show_result=tk.BooleanVar(value=False)
        window.title('V51 · 주차장 동시 측정 · RealSense 카메라 2대')
        window.geometry(f'{min(1380,window.winfo_screenwidth()-35)}x{min(880,window.winfo_screenheight()-65)}');window.minsize(850,610)
        quick=ttk.Frame(window,padding=8);quick.pack(fill='x')
        self.button(quick,'F5  자동 수집',self.quick_capture)
        self.button(quick,'Space  한 번 저장',lambda:self.quick_capture(once=True))
        self.button(quick,'Esc  정지',self.stop)
        self.advanced_visible=tk.BooleanVar(value=False)
        ttk.Checkbutton(quick,text='설정·검수',variable=self.advanced_visible,command=self.toggle_advanced).pack(side='right')
        self.advanced=ttk.Frame(window)
        top=ttk.Frame(self.advanced,padding=5);top.pack(fill='x')
        for title,fn in [('장치 검색',self.discover),('두 대 미리보기',self.preview),('두 빈 주차장 기준 등록',self.register_empty),('▶ 두 대 측정 시작',self.start),('측정 정지',self.stop),('연결 해제',self.disconnect),('새 차량 배치',self.new_batch)]:
            self.button(top,title,fn)
        auto=ttk.Checkbutton(top,text='가상선 · 실물판 자동 결합',variable=self.auto_guide)
        auto.pack(side='left',padx=4);self.controls.append((auto,'normal'))
        top=ttk.Frame(self.advanced,padding=(5,0));top.pack(fill='x')
        self.button(top,'현재 두 화면 1회 측정',self.start_once)
        self.auto_after_registration=tk.BooleanVar(value=True)
        ttk.Checkbutton(top,text='구역 확정 후 자동 측정',variable=self.auto_after_registration).pack(side='left',padx=4)
        ttk.Label(top,text='허용 수신 차이(ms)').pack(side='left',padx=(12,1));limit=ttk.Entry(top,textvariable=self.skew,width=5);limit.pack(side='left');self.controls.append((limit,'normal'))
        self.stream=tk.StringVar(value=f"{self.profile['width']}x{self.profile['height']}@{self.profile['fps']}")
        choices=list(dict.fromkeys([self.stream.get(),'1920x1080@30','1280x720@30','1280x720@15','640x480@30']))
        self.stream_box=ttk.Combobox(top,textvariable=self.stream,values=choices,state='readonly',width=14);self.stream_box.pack(side='left',padx=3);self.controls.append((self.stream_box,'readonly'))
        ttk.Checkbutton(top,text='최근 측정·등록선 보기',variable=self.show_result).pack(side='right')
        self.selected_only=tk.BooleanVar(value=not self.auto_guide.get());ttk.Checkbutton(top,text='사진 전달: 두 장면 모두 품질 통과만',variable=self.selected_only).pack(side='right',padx=4)
        body=ttk.Frame(window,padding=4);body.pack(fill='both',expand=True);body.rowconfigure(0,weight=1)
        self.camera_body=body;self.camera_settings={};self.camera_frames={}
        for col,k in enumerate(CHANNELS):
            body.columnconfigure(col,weight=1,uniform='camera')
            frame=ttk.LabelFrame(body,text=NAMES[k]+' · 채널 '+k,padding=4);frame.grid(row=0,column=col,sticky='nsew',padx=3)
            self.camera_frames[k]=frame
            settings=ttk.Frame(frame);self.camera_settings[k]=settings
            row=ttk.Frame(settings);row.pack(fill='x');c=self.profile['channels'][k]
            self.serial[k]=tk.StringVar(value=c['serial']);self.lot[k]=tk.StringVar(value=c['lot']);self.view[k]=tk.StringVar(value=VIEW_NAMES[c['view']])
            for title,var,vals,width in [('시리얼',self.serial[k],[c['serial']] if c['serial'] else [],15),('주차장',self.lot[k],CHANNELS,3),('시점',self.view[k],[v for key,v in VIEW_NAMES.items() if key!='unspecified'],6)]:
                ttk.Label(row,text=title).pack(side='left');box=ttk.Combobox(row,textvariable=var,values=vals,width=width,state='readonly');box.pack(side='left',padx=(0,3));self.controls.append((box,'readonly'))
                if title=='시리얼':self.serial_boxes[k]=box
                elif title=='주차장':box.bind('<<ComboboxSelected>>',lambda _event,channel=k:self.fill_rows(channel,[],self.lot[channel].get()))
            line=ttk.Frame(settings);line.pack(fill='x')
            self.button(line,'ROI·마커 기준 등록',lambda k=k:self.register(k))
            self.status[k]=tk.StringVar(value='영상 대기');ttk.Label(line,textvariable=self.status[k]).pack(side='left',padx=4)
            self.panels[k]=tk.Canvas(frame,height=175,bg='#18222d',highlightthickness=0);self.panels[k].pack(fill='both',expand=True,pady=3)
            self.last_labels[k]=tk.StringVar(value='마지막 저장 결과: 없음');ttk.Label(frame,textvariable=self.last_labels[k]).pack(anchor='w')
            t=ttk.Treeview(frame,columns=('bay','zone','occ','number','color','issue'),show='headings',height=6)
            for key,title,width in [('bay','주차칸',58),('zone','도면 구역',65),('occ','점유',50),('number','번호',48),('color','스티커색',64),('issue','검토 사유',160)]:
                t.heading(key,text=title);t.column(key,width=width,minwidth=35,stretch=key=='issue')
            scroll=ttk.Scrollbar(frame,orient='vertical',command=t.yview);t.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');t.pack(fill='x');self.tables[k]=t
            self.fill_rows(k,[],c['lot'])
        self.review_tools=ttk.Frame(window)
        bot=ttk.Frame(self.review_tools,padding=4);bot.pack(fill='x')
        for title,fn in [('저장 폴더 선택',self.choose_root),('두 카메라 사진·JSON 내보내기',self.export_pairs),('통과·승인 학습 내보내기',self.training),('통과한 칸만 학습 내보내기',self.training_bays),('학습 폴더 전체 검사',self.check_training_folder)]:
            self.button(bot,title,fn)
        review_bar=ttk.Frame(self.review_tools,padding=(4,0));review_bar.pack(fill='x')
        for title,fn in [('저장 검수·상세 보기',self.open_detail),('미완료 측정 확인',self.recover),('현장 정답 대조',self.check_field)]:
            self.button(review_bar,title,fn)
        self.storage_label=ttk.Label(window,textvariable=self.storage,wraplength=1100)
        self.storage_label.pack(fill='x',padx=8)
        ttk.Label(window,textvariable=self.note,wraplength=1100).pack(fill='x',padx=8,pady=(0,5))
        for key in ('F5','space','Escape'):
            window.bind('<'+key+'>',self.shortcut,add='+')
        self.show_counts();self.recover();window.protocol('WM_DELETE_WINDOW',self.close);window.after(100,self.poll)
    def button(self,parent,title,fn):
        def guarded():
            try:fn()
            except Exception as e:self.note.set(str(e))
        ttk.Button(parent,text=title,command=guarded).pack(side='left',padx=2)
    def toggle_advanced(self):
        if self.advanced_visible.get():
            self.advanced.pack(fill='x',before=self.camera_body)
            self.review_tools.pack(fill='x',before=self.storage_label)
            for k,frame in self.camera_settings.items():frame.pack(fill='x',before=self.panels[k])
        else:
            self.advanced.pack_forget();self.review_tools.pack_forget()
            for frame in self.camera_settings.values():frame.pack_forget()

    def shortcut(self,event):
        if (event.widget.winfo_toplevel()!=self.window or self.window.grab_current() is not None
                or event.state & 0x000c):return
        if event.keysym=='space' and event.widget.winfo_class() in (
                'Entry','TEntry','Text','TCombobox','Spinbox','TSpinbox','Button','TButton','Checkbutton','TCheckbutton'):
            return
        try:
            if event.keysym=='Escape':self.stop()
            else:self.quick_capture(once=event.keysym=='space')
        except Exception as exc:
            self.pending_capture=None;self.note.set(str(exc))
        return 'break'

    def quick_capture(self,once=False):
        if self.closing:return
        if self.running or self.pending_capture is not None:return
        if self.busy() and not (self.extra and self.extra[0]=='recover'):
            self.note.set('현재 분석·저장을 마치는 중입니다. 완료 후 다시 누르세요.');return
        self.pending_capture='once' if once else 'continuous'
        self.show_result.set(False)
        self.note.set('한 번 저장 준비 중…' if once else '자동 수집 준비 중…')
        self.advance_quick_capture()

    def advance_quick_capture(self):
        if getattr(self,'pending_capture',None) is None or self.busy() or self.running or self.closing:return
        try:self.start()
        except Exception:
            self.pending_capture=None
            raise
    def reset_groups(self):self.batches=active_batches(self.root,renew=True)
    def load_groups(self):self.batches=active_batches(self.root)
    def finish_run(self):
        if self.run_record:
            self.run_record.checkpoint(finished=True)
            self.run_record=None
    def busy(self):return bool(self.jobs or self.write_job or self.extra)
    def idle_required(self):
        if self.closing:raise ValueError('프로그램 종료 중입니다.')
        if self.running or self.busy():raise ValueError('측정을 정지하고 현재 분석·저장 완료 후 진행하세요.')
    def show_counts(self):
        self.storage.set(f"{self.root}  |  완료 동시측정 {self.total}쌍 · 장면 {self.total*2}개 · 칸 사진/JSON {NAMES['A']} {self.bays['A']}짝 / {NAMES['B']} {self.bays['B']}짝 · 미완료 {self.held}쌍 · 이번 실행 {self.run_count}쌍")
    def fill_rows(self,k,rows,lot,bay_holds=None,slots=()):
        if k in getattr(self,'camera_frames',{}):self.camera_frames[k].configure(text=NAMES[k]+' · '+lot+'주차장')
        t=self.tables[k];t.delete(*t.get_children());data={r['space_id']:r for r in rows}
        from_neighbours={s['space_id']:s['name_from_adjacent'] for s in slots if 'name_from_adjacent' in s}
        for s in layout(lot)['spaces']:
            sid=s['space_id'];r=data.get(sid,{})
            warn=list((bay_holds or {}).get(sid,[]))
            warn+=(['전용칸 불일치'] if r.get('zone_rule')=='not_allowed' else [])+r.get('issues',[])
            if sid in from_neighbours:warn.append('칸 이름 확인 · '+' / '.join(from_neighbours[sid]))
            number=displayed_prediction(r)
            t.insert('', 'end',values=(sid,TYPE_NAMES[s['space_type']],OCC.get(r.get('occupancy'),'대기'),number,COLOR.get(r.get('color'),'—'),' / '.join(warn)))
    def config_from_ui(self):
        p=copy.deepcopy(self.profile);p['max_skew_ms']=int(self.skew.get())
        size,fps=self.stream.get().split('@');p['width'],p['height']=map(int,size.split('x'));p['fps']=int(fps)
        for k in CHANNELS:
            p['channels'][k]={'serial':self.serial[k].get(),'lot':self.lot[k].get(),'view':next(code for code,name in VIEW_NAMES.items() if name==self.view[k].get())}
        profile_specs(p);return p
    def lock_controls(self):
        for widget,normal in self.controls:widget.configure(state='disabled' if self.running or self.busy() or self.closing or (self.hub and (widget in self.serial_boxes.values() or widget==self.stream_box)) else normal)
    def background(self,kind,fn,*args):
        if self.extra:raise ValueError('진행 중인 작업 완료를 기다리세요.')
        self.extra=(kind,self.pool.submit(fn,*args));self.lock_controls()
    def recover(self):
        self.idle_required();self.background('recover',recover_pairs,self.root,self.cfg)
    def discover(self):
        self.idle_required()
        if self.hub:raise ValueError('장치 배정을 바꾸려면 먼저 연결을 해제하세요.')
        self.background('discover',find_realsense_devices);self.note.set('RealSense 시리얼 검색 중…')
    def start(self):
        if self.running and not self.one_shot:
            return
        self.idle_required();self.profile=self.config_from_ui()
        if self.hub:
            atomic_json(self.filename,self.profile);self.activate();return
        self.background('connect',find_realsense_devices);self.note.set('두 RealSense 시리얼 확인 중…')
    def preview(self):
        self.idle_required()
        if self.hub:self.show_result.set(False);self.note.set('두 대 실시간 미리보기 · 저장하지 않습니다.');return
        self.profile=self.config_from_ui();self.background('preview',find_realsense_devices);self.note.set('두 RealSense 미리보기 연결 중…')

    def start_once(self):
        self.idle_required()
        if not self.hub or self.show_result.get():
            raise PairHold('두 대 미리보기에서 현재 영상을 표시한 뒤 1회 측정하세요.')
        settings=self.config_from_ui()
        frames=self.displayed_frames.copy()
        keys,delta=synchronized(frames,self.specs,settings['max_skew_ms'],self.last_keys)
        for k,packet in frames.items():
            if not self.hub.recent_times(k,packet.stream_epoch):
                raise PairHold(f'카메라 {k}의 연결이 바뀌었습니다. 새 미리보기에서 다시 측정하세요.')
        self.profile=settings
        self.check_empty_context(frames)
        self.finish_run();self.load_groups()
        self.running=True;self.one_shot=True;self.keep_first_hold=True
        self.queue_analysis(frames,keys,delta)

    def queue_analysis(self,frames,keys,delta):
        self.last_keys=keys
        self.analysis_frames=frames.copy()
        auto=self.auto_guide.get()
        cfg={**self.cfg,'auto_sticker_guide':auto}
        self.jobs={k:self.pool.submit(self.engines[k].analyze,self.root,frames[k],copy.deepcopy(self.profile['channels'][k]),cfg,None if auto else self.empty_references.get(k)) for k in CHANNELS}
        self.note.set(f'두 프레임 동시 분석 중 · PC 수신 차이 {delta:.1f}ms')
        self.lock_controls()
    def register_empty(self):
        self.idle_required()
        self.empty_start_cancelled=False
        if not self.hub:
            raise PairHold('두 대 미리보기를 연결하고 두 주차장을 비운 뒤 등록하세요.')
        settings=self.config_from_ui()
        frames={k:self.hub.get_latest_frame(k) for k in CHANNELS}
        synchronized(frames,self.specs,settings['max_skew_ms'])
        def prepare():
            views = {}
            outcomes = {}
            problems = []
            for k in CHANNELS:
                try:
                    roi = self.engines[k].register_roi(self.root,frames[k],settings['channels'][k],self.cfg)
                    weak=()
                    if self.cfg.get('physical_sticker_set')==FIELD_SET:
                        _,weak=disc_evidence(frames[k].image,roi)
                    views[k] = EmptyView(frames[k], roi, weak)
                    outcomes[k]=views[k]
                except (PairHold, ValueError) as exc:
                    outcomes[k]=exc
                    problems.append(f'{NAMES[k]} · {exc}')
            if problems:
                if has_registration_view(outcomes):
                    raise PartialReferenceHold('빈 기준 등록 보류 · '+' / '.join(problems),frames,outcomes,settings)
                raise PairHold('빈 기준 등록 보류 · ' + ' / '.join(problems))
            return views, settings['max_skew_ms']
        self.background('empty_preview',prepare)
        self.note.set('빈 기준으로 확인할 두 프레임의 전체 주차칸을 찾는 중…')

    def activate(self):
        if self.empty_references:
            self.check_empty_context({k:self.hub.get_latest_frame(k) for k in CHANNELS})
        self.finish_run();self.load_groups()
        self.run_record=CollectionRun(self.root,self.session,copy.deepcopy(self.profile))
        self.one_shot=getattr(self,'pending_capture',None)=='once';self.pending_capture=None
        self.keep_first_hold=True;self.registration_views.clear()
        self.not_before=time.monotonic();self.next_pair=self.not_before+self.cfg.get('warmup_seconds',3.);self.running=True;self.last_display.clear()
        if not self.empty_references:
            for k in CHANNELS:self.fill_rows(k,[],self.profile['channels'][k]['lot']);self.last_labels[k].set('현재 측정의 첫 저장 대기')
        self.lock_controls();self.note.set('두 카메라 준비 중 · 최근 프레임 2개가 일치하면 자동 등록·측정합니다.')

    def check_empty_context(self, frames):
        if getattr(self,'auto_guide',None) is not None and self.auto_guide.get() is True:return
        for k, (roi, reference) in self.empty_references.items():
            setting = self.profile['channels'][k]
            pose = self.cfg['camera_pose'][k] + '__view-' + setting['view']
            frame = frames.get(k)
            if ((roi['serial'], roi['lot_id'], roi['pose']) != (setting['serial'], setting['lot'], pose)
                    or (frame is not None and (frame.serial != roi['serial'] or frame.stream_epoch != reference['epoch']))):
                raise PairHold(f'{NAMES[k]}의 연결·구도가 빈 기준과 달라졌습니다. 두 빈 주차장 기준을 다시 등록하세요.')
    def stop(self):
        self.pending_capture=None
        if self.run_record:self.run_record.stop_reason('측정 정지')
        if self.extra and self.extra[0] in ('empty_preview','empty_saved'):
            self.empty_start_cancelled=True
        if self.extra and self.extra[0] in ('preview','connect'):self.extra=('discover',self.extra[1])
        self.running=False;self.lock_controls()
        self.note.set('측정 정지 · 진행 중인 파일 쓰기는 완료하고 분석 중이던 미저장 짝은 해제합니다.')
    def disconnect(self):
        self.idle_required()
        self.pending_capture=None
        if self.hub:
            hub=self.hub;hub.request_stop()
            def release():
                hub.stop()
                while not hub.is_stopped():time.sleep(.1)
            self.note.set('두 장치 스트림 해제 중…');self.background('disconnect',release)
    def new_batch(self):
        self.idle_required();self.reset_groups();self.note.set('새 차량 배치 준비 · 시점만 바꿀 때는 새 배치를 만들지 마세요.')
    def choose_root(self):
        self.idle_required()
        if self.detail and self.detail.winfo_exists():raise ValueError('검수 창을 닫은 다음 폴더를 바꾸세요.')
        p=filedialog.askdirectory(parent=self.window,initialdir=self.root)
        if p:
            new=Path(p).resolve();config=read_json(self.project/'config.json');config['dataset_directory']=str(new);atomic_json(self.project/'config.json',config)
            self.finish_run();self.batches={}
            self.root=new;self.run_count=0;self.last_display.clear();self.total=self.held=0;self.bays=dict.fromkeys(CHANNELS,0)
            self.empty_references.clear()
            self.registration_views.clear()
            self.manual_views.clear()
            for k in CHANNELS:self.fill_rows(k,[],self.lot[k].get());self.last_labels[k].set('마지막 저장 결과: 없음')
            self.show_counts();self.recover()
    def export_pairs(self):
        self.idle_required();selected=self.selected_only.get()
        self.background('export',lambda:export_collection(self.root,paired_only=True,selected_only=selected));self.note.set('두 카메라 원본·표시사진·한글 JSON 복사 및 대조 중…')
    def check_training_folder(self):
        from field_check import inspect_training_folder
        self.idle_required()
        folder=filedialog.askdirectory(parent=self.window,initialdir=self.root/'exports',title='검사할 학습 전달 폴더 선택')
        if folder:
            self.background('training_check',inspect_training_folder,folder)
            self.note.set('번호·색·점유 사진과 전달 목록 확인 중…')
    def check_field(self):
        from field_check import field_check
        self.idle_required()
        selected=filedialog.askopenfilename(parent=self.window,initialdir=self.root,
            title='dual_measurements의 측정 기록(빈 정답표 생성) 또는 작성한 현장정답표 선택',
            filetypes=[('JSON','*.json')])
        if selected:
            self.background('field_check',field_check,self.root,selected)
            self.note.set('두 카메라 저장 자료와 현장 정답표 확인 중…')
    def training(self):
        self.idle_required();self.background('training',lambda:export_training(self.root,selected_only=True,paired_only=True))
    def training_bays(self):
        self.idle_required();self.background('training',lambda:export_training(self.root,selected_only=True,paired_only=True,per_bay=True))
    def open_detail(self):
        self.idle_required()
        if self.detail and self.detail.winfo_exists():self.detail.lift();return
        from dashboard import Dashboard
        class ReviewDashboard(Dashboard):
            def connect(self):raise ValueError('카메라 연결은 두 대 측정 창에서 관리합니다.')
            def choose_root(self):raise ValueError('저장 폴더는 검수 창을 닫고 두 대 측정 창에서 선택하세요.')
        self.detail=tk.Toplevel(self.window);self.details=ReviewDashboard(self.detail,self.cfg,self.root,self.project)
        self.detail.title('V51 저장 장면 검수 · 주차장/칸별 정답·품질·내보내기')
    def register(self,k):
        self.idle_required()
        if not self.hub:raise ValueError('먼저 두 대 측정 시작 → 정지 후 기준을 등록하세요.')
        from widgets import RoiEditor
        from automatic_setup import save_anchor
        from marker_setup import detect,enroll,template_path
        p=self.hub.get_latest_frame(k);setting=self.config_from_ui()['channels'][k]
        if p is None:raise PairHold('카메라 영상이 없습니다.')
        lot=setting['lot'];root=alignment_root(self.root,k);pose=self.cfg['camera_pose'][k]+'__view-'+setting['view']
        if template_path(root,lot).exists() and not messagebox.askyesno('마커 기준 교체',f'{NAMES[k]} {lot}주차장 기준을 현재 촬영 장면으로 교체할까요?',parent=self.window):return
        dlg=RoiEditor(self.window,p.image,lot);self.window.wait_window(dlg)
        if not dlg.result:return
        if len(dlg.result)!=layout(lot)['space_count']:raise ValueError('모든 주차칸을 등록하세요.')
        roi=snapshot(lot,k,p.serial,p.image.shape[1::-1],pose,dlg.result)
        def save():
            ids=detect(p.image,lot)
            if len(ids)>=4 or template_path(root,lot).exists():
                registered=enroll(root,p,roi)
                return k,EmptyView(p,registered['roi']),'ArUco 기준 등록 완료'
            save_anchor(root,p,roi)
            return k,EmptyView(p,roi),'고정 구도 기준 등록 완료 · 매 촬영 시 실제 영상과 대조합니다.'
        self.background('register',save)
    def handle_extra(self,kind,value):
        if kind in ('discover','preview','connect'):
            serials=sorted(d['serial'] for d in value if d['serial'] and d['serial']!='unknown')
            for k in CHANNELS:self.serial_boxes[k].configure(values=serials)
            if kind=='discover':
                self.note.set(f'RealSense {len(serials)}대 감지 · '+', '.join(serials));return
            specs=profile_specs(self.profile,value);self.specs={s.camera_id:s for s in specs}
            for s in specs:self.serial[s.camera_id].set(s.serial);self.profile['channels'][s.camera_id]['serial']=s.serial
            atomic_json(self.filename,self.profile);self.hub=CameraHub(specs);self.hub.start()
            self.last_keys.clear()
            self.empty_references.clear()
            self.manual_views.clear()
            if kind=='connect':self.activate()
            else:self.show_result.set(False);self.note.set('두 대 실시간 미리보기 · 저장하지 않습니다.')
        elif kind=='disconnect':
            self.hub=None;self.frames={};self.displayed_frames.clear();self.empty_references.clear()
            self.manual_views.clear()
            for k in CHANNELS:self.status[k].set('연결 해제')
            self.note.set('두 카메라 연결 해제')
        elif kind=='empty_preview':
            if self.empty_start_cancelled:
                self.note.set('측정 정지 · 진행 중이던 빈 기준 확인을 취소했습니다.')
                return
            views, max_skew_ms = value
            for k in CHANNELS:
                self.paint(k,views[k])
            self.window.update_idletasks()
            prompt='표시한 두 사진의 모든 주차칸이 비어 있고 차량·손·가림이 없습니까?'
            suspects=[f"{NAMES[k]} {', '.join(view.suspect_bays)}" for k,view in views.items() if view.suspect_bays]
            if suspects:prompt+='\n약한 스티커 후보가 있습니다. 다음 칸을 확대 또는 실물로 확인하세요.\n'+' / '.join(suspects)
            if self.auto_after_registration.get():prompt+='\n확인하면 구역을 고정하고 두 카메라 측정·저장을 시작합니다.'
            if messagebox.askyesno('두 빈 장면 확인',prompt,parent=self.window):
                self.background('empty_saved',store_empty_pair,self.root,views,self.cfg,max_skew_ms)
            else:
                self.note.set('빈 기준 등록 취소')
        elif kind=='empty_saved':
            for k,(_,reference) in value.items():
                current=self.hub.get_latest_frame(k) if self.hub else None
                if current is None or current.stream_epoch!=reference['epoch'] or current.serial!=reference['empty_board']['시리얼']:
                    raise PairHold('빈 기준 등록 도중 연결이 변경됐습니다. 두 빈 주차장 기준을 다시 확인하세요.')
            self.empty_references=value
            self.auto_guide.set(False)
            self.registration_views.clear()
            self.last_display.clear()
            self.show_result.set(False)
            for k,(roi,_) in value.items():
                slots=roi['spaces']
                self.fill_rows(k,[],roi['lot_id'],{s['space_id']:['구역 등록 완료 · 차량 측정 대기'] for s in slots})
                self.last_labels[k].set(f"구역 등록 완료 · {roi['lot_id']}주차장 {len(slots)}칸")
            if self.auto_after_registration.get() and not self.empty_start_cancelled:
                self.profile=self.config_from_ui()
                self.activate()
                self.note.set('두 빈 주차장 기준 등록 완료 · 자동 측정 중 · 차량을 배치하면 칸별 결과가 표에 표시됩니다.')
            else:
                self.note.set('두 빈 주차장 기준 등록 완료 · 등록선 안에 차량을 배치한 뒤 1회 측정하거나 두 대 측정을 시작하세요.')
        elif kind=='recover':
            self.total=value['complete'];self.held=value['held'];self.bays=value['bay_pairs'];self.show_counts()
            problems = value['errors'] + [p['file']+' · '+p['reason'] for p in value['unidentified_manifests']]
            self.note.set(f"완료 {self.total}쌍 · 보류 {self.held}쌍 · 측정 기록 복구 {value['restored_receipts']}쌍 · 식별 불가 장면 {len(value['unidentified_manifests'])}개 · 원본 복구 보류 {value['capture_recovery']['복구보류수']}"+(' · '+problems[0] if problems else ''))
        elif kind=='export':
            path,report=value
            sources=report['source_measurements']
            self.total=sources['complete'];self.held=sources['held'];self.bays=sources['bay_pairs'];self.show_counts()
            c=report['counts'];self.note.set(f"복사 완료 {c['measurements']}측정 · {c['scenes']}장면 · {c['space_pairs']}칸 짝 · 이미지 {c['images']} · JSON {c['json_files']} · 제외 기록 {len(report['skipped'])}개 | {path}")
        elif kind=='training':self.note.set(f'품질 통과·사람 승인 학습 자료 {value[1]}개 전달 | {value[0]}')
        elif kind=='training_check':
            samples=value[0]['samples']
            counts=[sum(s['task']==task for s in samples) for task in ('number','color','occupancy')]
            self.note.set(f'전달 폴더 검사 완료 · 번호 {counts[0]}장 / 색 {counts[1]}장 / 점유 {counts[2]}장')
        elif kind=='field_check':self.note.set(f'{value[1]} | {value[0]}')
        elif kind=='held_pair':
            self.note.set('측정 보류 · '+self.hold_reason+' · 두 원본과 등록 근거 저장 | '+str(value))
            self.next_pair=time.monotonic()+self.cfg.get('save_interval',2.)
        elif kind=='register':
            k,view,message=value
            had_reference=bool(self.empty_references)
            self.empty_references.clear()
            self.manual_views[k]=view
            self.show_result.set(False)
            self.note.set(message+(' · 변경된 구도의 두 빈 기준을 다시 등록하세요.' if had_reference else ''))
    def show_partial_registration(self,frames,outcomes,settings,freeze):
        self.registration_views.clear()
        for k,value in outcomes.items():
            partial=[]
            if isinstance(value,RegistrationHold):
                slots=value.setup.get('spaces',[])
                partial=value.setup.get('partial_spaces',[])
            elif isinstance(value,Exception):
                slots=[]
            else:
                slots=value.roi['spaces']
            lot=settings['channels'][k]['lot']
            self.registration_views[k]=EmptyView(frames[k],{'spaces':slots,'partial_spaces':partial})
            notes={s['space_id']:['전체 경계 등록'] for s in slots}
            if isinstance(value,RegistrationHold):
                from automatic_setup import missing_stages
                stages=missing_stages(value.setup) or {}
                for sid in value.setup.get('missing',[]):
                    notes[sid]=['등록 보류 · '+stages.get(sid,'글씨·주차선 확인')]
            if isinstance(value,RegistrationHold) and value.setup.get('dark_frame'):
                notes={s['space_id']:['저조도 · 위치 등록 보류'] for s in layout(lot)['spaces']}
            elif isinstance(value,Exception) and not isinstance(value,RegistrationHold):
                notes={s['space_id']:['현재 측정 보류 · '+str(value)] for s in layout(lot)['spaces']}
            for s in partial:
                notes[s['space_id']]=['일부 촬영 · 위치 등록 · 학습 보류']
            self.fill_rows(k,[],lot,notes)
            if isinstance(value,Exception) and not isinstance(value,RegistrationHold):
                self.last_labels[k].set(f'측정 보류 장면 {frames[k].sequence} · {value}')
                continue
            self.last_labels[k].set(f"등록 장면 {frames[k].sequence} · 위치 {len(slots)+len(partial)}/{layout(lot)['space_count']}칸"
                                    f" · 전체 경계 {len(slots)} · 일부 촬영 {len(partial)}")
        if freeze:
            self.show_result.set(True)

    def guide_for_preview(self,k,p):
        from sticker_guide import guide_spaces,physical_or_virtual,METHOD
        lot=self.lot[k].get()
        view=next(code for code,name in VIEW_NAMES.items() if name==self.view[k].get())
        pose=self.cfg['camera_pose'][k]+'__view-'+view
        size=p.image.shape[1::-1]
        key=(id(self.hub),self.root,p.serial,p.stream_epoch,pose,lot,size)
        now=time.monotonic()
        job=self.guide_jobs.get(k)
        if job and job[1].done():
            self.guide_jobs.pop(k)
            if job[0]==key:
                roi=job[1].result()
                self.guide_last[k]=(key,roi,job[2])
        if k not in self.guide_jobs and now>=self.guide_next.get(k,0) and not self.closing:
            self.guide_jobs[k]=(key,self.pool.submit(physical_or_virtual,alignment_root(self.root,k),p,lot,pose),now)
            self.guide_next[k]=now+1.1
        last=self.guide_last.get(k)
        if last and last[0]==key and now-last[2]<2.5:return last[1]
        return {'registration_method':METHOD,'spaces':guide_spaces(lot,*size)}

    def paint(self,k,frozen=None):
        canvas=self.panels[k];w=max(1,canvas.winfo_width());h=max(1,canvas.winfo_height())
        held=self.registration_views.get(k) if self.show_result.get() and frozen is None else None
        r=frozen or held or (self.last_display.get(k) if self.show_result.get() else None)
        p=r.packet if r is not None else (None if self.show_result.get() else self.frames.get(k))
        self.displayed_frames.pop(k,None)
        canvas.delete('all')
        if p is None:
            canvas.create_text(w/2,h/2,text='저장 장면 없음' if self.show_result.get() else '현재 영상 없음',fill='white');return
        scale=min(w/p.image.shape[1],h/p.image.shape[0]);iw=max(1,int(p.image.shape[1]*scale));ih=max(1,int(p.image.shape[0]*scale))
        small=cv2.resize(p.image,(iw,ih),interpolation=cv2.INTER_AREA)
        if r is not None:
            from board_profile import apply_lighting
            small=apply_lighting(small,(getattr(r,'prediction',None) or {}).get('measurement_lighting'))
        photo=ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(small,cv2.COLOR_BGR2RGB)))
        self.photos[k]=photo;x=(w-iw)/2;y=(h-ih)/2;canvas.create_image(x,y,image=photo,anchor='nw')
        roi=r.roi if r is not None else None
        held_region=self.registration_views.get(k)
        if r is None and (held_region is None or held_region.roi['spaces']) and (k in self.empty_references or k in self.manual_views):
            if k in self.empty_references:
                fixed,baseline=self.empty_references[k]
                epoch=baseline['epoch']
            else:
                manual=self.manual_views[k]
                fixed,epoch=manual.roi,manual.packet.stream_epoch
            view_name=VIEW_NAMES.get(fixed['pose'].rsplit('__view-',1)[-1])
            if (fixed['serial']==p.serial and epoch==p.stream_epoch and
                fixed['lot_id']==self.lot[k].get() and fixed['image_size']==list(p.image.shape[1::-1]) and
                view_name==self.view[k].get()):
                roi=fixed
                if not self.auto_guide.get() and fixed.get('registration_method')=='fixed_printed_board_v1':
                    observed=self.guide_for_preview(k,p)
                    roi=observed if observed.get('registration_method')=='fixed_printed_board_v1' else None
        if r is None and self.auto_guide.get():roi=self.guide_for_preview(k,p)
        from sticker_guide import is_guide
        virtual=is_guide(roi or {})
        if roi:
            predictions={row['space_id']:row for row in getattr(r,'prediction',{}).get('spaces',[])}
            for slot in roi['spaces']+roi.get('partial_spaces',[]):
                pts=[(x+a*scale,y+b*scale) for a,b in slot['polygon']]
                if slot.get('boundary_kind')=='partial_frame':
                    canvas.create_polygon(pts,outline='#ff912e',fill='',width=3)
                    cx=sum(a for a,b in pts)/len(pts);cy=sum(b for a,b in pts)/len(pts)
                    canvas.create_text(cx,cy,text=slot['space_id']+' 일부',fill='#ffcf88')
                    continue
                elif slot.get('boundary_kind') in ('open_u','board_open_u','virtual_open_u'):
                    canvas.create_line(pts[3],pts[0],pts[1],pts[2],fill='#74c8ff' if virtual else '#47f1b2',width=2);canvas.create_line(pts[2],pts[3],fill='#f0b83a',dash=(3,3))
                else:canvas.create_polygon(pts,outline='#47f1b2',fill='')
                label=slot['space_id']
                row=predictions.get(label)
                if slot.get('space_type') in ('electric','accessible'):label+=' '+TYPE_NAMES[slot['space_type']]
                if row and row.get('occupancy')=='occupied':
                    number=displayed_prediction(row,'번호 미확인')
                    label+=f" · {number} · {COLOR.get(row.get('color'),'미확인')}"
                canvas.create_text(*pts[0],text=label,fill='white',anchor='nw')
        caption='빈 기준 확인' if frozen else '등록 결과' if held else '저장 장면' if r else '실시간 · 등록 구역' if roi else '실시간'
        if roi and roi.get('lot_id'):caption+=' · '+roi['lot_id']+'주차장'
        if virtual:caption+=' · 가상 안내선 / 스티커 수집'
        elif roi and self.auto_guide.get() and not held:caption+=' · 실물 주차판'
        canvas.create_text(6,5,anchor='nw',text=caption+f' · {p.sequence}',fill='#ffff84')
        if r is None:self.displayed_frames[k]=p
    def step(self):
        if self.extra and self.extra[1].done():
            (kind,f),self.extra=self.extra,None
            try:
                value=f.result()
                if not self.closing:self.handle_extra(kind,value)
            except PartialReferenceHold as exc:
                if not self.closing:
                    self.show_partial_registration(exc.frames,exc.outcomes,exc.settings,True)
                    self.note.set(str(exc))
            finally:self.lock_controls()
        if self.write_job and self.write_job.done():
            f,self.write_job=self.write_job,None
            try:info=f.result()
            except Exception:
                self.running=False;self.last_display.clear();self.saving_results={}
                if self.run_record:self.run_record.data['저장실패수']+=1
                if not self.closing:self.recover()
                self.lock_controls();raise
            self.total+=1;self.run_count+=1;self.last_display=self.saving_results;self.saving_results={}
            if self.run_record:self.run_record.saved(info)
            self.registration_views.clear()
            for k,r in self.last_display.items():
                holds=info['bay_holds'][k]
                self.fill_rows(k,r.prediction['spaces'],r.roi['lot_id'],holds,r.roi['spaces']);self.bays[k]+=len(r.roi['spaces'])
                tone=r.prediction.get('measurement_lighting')
                light=f"밝기 보정 ×{tone['gain']:.2f}" if tone else '원본 밝기'
                if (r.prediction.get('reference') or {}).get('printed_board_warp'):light+=' · 실물 도면 재정합'
                if r.roi.get('registration_method')=='virtual_sticker_guide_v1':light+=' · 가상선 스티커 측정'
                self.last_labels[k].set(f"저장 장면 {r.packet.sequence} · {r.roi['lot_id']}주차장 · {VIEW_NAMES[r.cfg['capture_view']]} · {light} · 품질 {info['quality'][k]} · 보류 {len(holds)}칸")
            self.show_counts();self.note.set(f"두 장면 저장 완료 · {info['skew_ms']:.1f}ms · 품질 1: {info['quality']['A']} / 2: {info['quality']['B']} · 측정 {info['id']}")
            self.next_pair=time.monotonic()+self.cfg.get('save_interval',2.);self.lock_controls()
        if self.hub and not self.closing:
            self.frames={k:self.hub.get_latest_frame(k) for k in CHANNELS};statuses=self.hub.statuses()
            if self.run_record:self.run_record.observe_channels(self.frames)
            for k in CHANNELS:self.status[k].set(f"{statuses[k]['status']} · {self.specs[k].serial}"+(' · '+str(statuses[k]['last_error']) if statuses[k].get('last_error') else ''))
            if self.running and self.empty_references:
                try:self.check_empty_context(self.frames)
                except PairHold as exc:
                    if self.run_record:self.run_record.stop_reason(exc)
                    self.running=False;self.note.set('측정 정지 · '+str(exc));self.lock_controls()
        if self.jobs and all(j.done() for j in self.jobs.values()):
            jobs,self.jobs=self.jobs,{}
            inputs,self.analysis_frames=self.analysis_frames,{}
            if self.running and not self.closing:
                if self.one_shot:self.running=False
                outcomes={k:(job.exception() if job.exception() is not None else job.result()) for k,job in jobs.items()}
                errors=[NAMES[k]+': '+str(v) for k,v in outcomes.items() if isinstance(v,Exception)]
                if errors:
                    if self.run_record:self.run_record.data['분석보류쌍']+=1
                    self.show_partial_registration(inputs,outcomes,self.profile,self.one_shot)
                    self.hold_reason=' / '.join(errors)
                    self.next_pair=time.monotonic()+3.;self.note.set('두 대 측정 보류 · '+self.hold_reason)
                    if self.keep_first_hold:
                        self.keep_first_hold=False
                        self.background('held_pair',save_held_pair,self.root,inputs,outcomes,copy.deepcopy(self.profile),self.session)
                else:
                    results=outcomes
                    if any(not self.frames.get(k) or (self.frames[k].serial,self.frames[k].stream_epoch)!=(r.packet.serial,r.packet.stream_epoch) for k,r in results.items()):
                        if self.run_record:self.run_record.data['분석보류쌍']+=1
                        self.note.set('분석 도중 연결이 바뀌어 두 장면을 보류합니다.');self.next_pair=time.monotonic()+1.
                    elif all(waiting_for_sticker(r.roi,r.prediction) for r in results.values()):
                        if self.run_record:self.run_record.data['스티커대기쌍']+=1
                        self.last_display=results
                        for k,r in results.items():
                            self.fill_rows(k,r.prediction['spaces'],r.roi['lot_id'])
                            self.last_labels[k].set('가상 안내선 · 스티커 대기 · 이번 측정 저장 0건')
                        self.next_pair=time.monotonic()+self.cfg.get('save_interval',2.)
                        self.note.set('가상선 안에 원형 스티커가 보이도록 차량을 놓으세요.')
                    else:
                        group={k:self.batches[r.roi['lot_id']] for k,r in results.items()};self.saving_results=results
                        self.write_job=self.pool.submit(save_pair,self.root,results,group,self.session,self.profile['max_skew_ms'])
                        self.note.set('두 원본·칸별 표시사진·한글 JSON 저장 중…')
            elif self.run_record:
                self.run_record.data['정지로해제한분석쌍']+=1
            self.lock_controls()
        if self.running and not self.busy() and time.monotonic()>=self.next_pair:
            try:frames,keys,delta=continuous_pair(self.hub,self.frames,self.specs,self.profile['max_skew_ms'],self.last_keys,self.not_before)
            except PairHold as e:
                self.note.set(str(e))
                if self.run_record:self.run_record.waiting(self.frames,e)
            else:
                self.queue_analysis(frames,keys,delta)
                if self.run_record:self.run_record.data['분석시작쌍']+=1
        if self.run_record:
            if not self.running and not self.write_job and not self.jobs:self.finish_run()
            elif time.monotonic()>=self.run_record.next_checkpoint:self.run_record.checkpoint()
        if not self.closing:
            for k in CHANNELS:self.paint(k)
    def poll(self):
        try:
            self.step()
            self.advance_quick_capture()
        except Exception as e:
            self.pending_capture=None
            self.running=False
            if self.run_record:self.run_record.stop_reason(e)
            self.note.set('측정/작업 보류: '+str(e));self.lock_controls()
        if self.closing and not self.busy() and (not self.hub or self.hub.is_stopped()) and (not self.detail or not self.detail.winfo_exists()):
            self.pool.shutdown(wait=False);self.window.destroy();return
        self.window.after(160,self.poll)
    def close(self):
        if self.detail and self.detail.winfo_exists():
            self.details.close()
            if not self.details.closing:return
        self.closing=True;self.running=False;self.pending_capture=None
        if self.run_record:self.run_record.stop_reason('창 닫기')
        if self.hub:self.hub.request_stop()
        self.lock_controls();self.note.set('진행 중인 저장·장치 종료를 기다립니다…')
