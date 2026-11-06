"""Camera adjustment UI. SDK mutations run only on the camera worker."""
import json,queue,time
import tkinter as tk
from tkinter import ttk,messagebox
from pathlib import Path
from widgets import photo,RoiEditor
from quality import image_quality,read_quality_config
from parking_ai.storage import atomic_json

class CameraTools(tk.Toplevel):
    def __init__(self,parent,hub,camera,project):
        super().__init__(parent);self.title('카메라 '+camera+' · 화질 확인 / 설정');self.hub=hub;self.camera=camera;self.project=Path(project)
        self.receipt=None;self.pending_save=False;self.last_settings=None;self.actual={};self.timer=None
        self.auto_exposure=tk.BooleanVar(value=True);self.auto_wb=tk.BooleanVar(value=True)
        ttk.Label(self,text='수집을 일시정지했습니다. 설정 후 창을 닫고 [위치 확인]을 다시 누르세요. 값의 단위/범위는 센서 반환값을 따릅니다.').pack(padx=8,pady=5)
        controls=ttk.Frame(self);controls.pack(fill='x');self.entries={};self.ranges={}
        ttk.Checkbutton(controls,text='자동 노출·게인',variable=self.auto_exposure).grid(row=0,column=0)
        ttk.Checkbutton(controls,text='자동 화이트밸런스',variable=self.auto_wb).grid(row=0,column=1)
        for i,(key,label) in enumerate([('exposure','노출'),('gain','게인'),('white_balance','화이트밸런스'),('sharpness','샤프니스'),('power_line_frequency','플리커 0=끔 1=50Hz 2=60Hz')],1):
            ttk.Label(controls,text=label).grid(row=i,column=0,sticky='w');v=tk.StringVar();self.entries[key]=v
            ttk.Entry(controls,textvariable=v,width=12).grid(row=i,column=1)
            r=tk.StringVar(value='조회 대기');self.ranges[key]=r;ttk.Label(controls,textvariable=r).grid(row=i,column=2,sticky='w')
        buttons=ttk.Frame(self);buttons.pack(fill='x',pady=5)
        for text,fn in [('현재 실제값 조회',lambda:self.request({})),('적용만',lambda:self.apply(False)),('적용 후 설정 저장',lambda:self.apply(True)),('현재 사진 원본 확대·영역 화질',self.inspect)]:ttk.Button(buttons,text=text,command=fn).pack(side='left',padx=3)
        self.preview=ttk.Label(self);self.preview.pack()
        self.status=tk.StringVar(value='카메라 설정 조회 중');ttk.Label(self,textvariable=self.status,wraplength=900).pack(pady=5)
        self.metrics=tk.StringVar();ttk.Label(self,textvariable=self.metrics,wraplength=900).pack()
        self.protocol('WM_DELETE_WINDOW',self.destroy)
        self.poll();self.request({})
    def request(self,changes,save=False):
        if self.receipt is not None:self.status.set('앞선 설정 요청의 응답을 기다리고 있습니다.');return
        try:self.receipt=self.hub.request_quality(self.camera,changes);self.pending_save=save;self.last_settings=changes;self.deadline=time.monotonic()+10;self.status.set('장치 응답 대기…')
        except Exception as exc:messagebox.showerror('카메라 설정',str(exc),parent=self)
    def apply(self,save):
        try:
            values={k:float(v.get()) for k,v in self.entries.items() if v.get().strip() and (not self.actual.get(k,{}).get('read_only') or (k in ('exposure','gain') and not self.auto_exposure.get()) or (k=='white_balance' and not self.auto_wb.get()))}
            if self.auto_exposure.get():values.pop('exposure',None);values.pop('gain',None)
            elif not all(k in values for k in ('exposure','gain')):raise ValueError('수동 노출과 게인 값을 모두 입력하세요.')
            if self.auto_wb.get():values.pop('white_balance',None)
            elif 'white_balance' not in values:raise ValueError('수동 화이트밸런스 값을 입력하세요.')
            values.update(enable_auto_exposure=int(self.auto_exposure.get()),enable_auto_white_balance=int(self.auto_wb.get()))
            self.request(values,save)
        except Exception as exc:messagebox.showerror('설정 입력',str(exc),parent=self)
    def inspect(self):
        packet=self.hub.get_latest_frame(self.camera)
        if packet is None:messagebox.showerror('영상 없음','새 프레임이 없습니다.',parent=self);return
        RoiEditor(self,packet.image,'A',inspect_only=True)
    def receive(self,result):
        self.actual=result.get('actual',{})
        if not result['ok']:self.status.set('적용 실패/일부 적용 가능: '+result.get('error','')+' · 실제값을 다시 조회하세요.');return
        for key,var in self.entries.items():
            entry=self.actual.get(key,{})
            if entry.get('supported'):
                var.set(str(entry['value']));self.ranges[key].set(f"실제 {entry['value']} · 범위 {entry['min']}~{entry['max']} · 단계 {entry['step']}" )
            else:var.set('');self.ranges[key].set('미지원/조회 실패')
        for name,var in [('enable_auto_exposure',self.auto_exposure),('enable_auto_white_balance',self.auto_wb)]:
            if self.actual.get(name,{}).get('supported'):var.set(bool(self.actual[name]['value']))
        self.status.set('장치 응답 확인 · 실제 적용값은 위 범위 옆에 표시됩니다.')
        if self.pending_save:
            try:
                path=self.project/'camera_quality.json';data=read_quality_config(path)
                if not isinstance(data,dict) or not isinstance(data.get(self.camera,{}),dict):raise ValueError('camera_quality.json 형식 오류')
                for mode,manual in [('enable_auto_exposure',('exposure','gain')),('enable_auto_white_balance',('white_balance',))]:
                    entry=self.actual.get(mode,{})
                    if not entry.get('supported'):raise ValueError('자동/수동 상태를 읽지 못해 재연결용 설정을 저장할 수 없습니다: '+mode)
                    if not entry['value'] and any(not self.actual.get(k,{}).get('supported') for k in manual):
                        raise ValueError('수동 설정 실제값이 없어 저장할 수 없습니다: '+mode)
                # Keep contrast, saturation, brightness and other options absent
                # from this editor. Replacing the whole camera object lost them.
                settings=dict(data.get(self.camera,{}))
                settings.update({k:v['value'] for k,v in self.actual.items() if v.get('supported') and k not in ('enable_auto_exposure','enable_auto_white_balance')})
                if self.actual['enable_auto_exposure']['value']:settings.update(exposure=None,gain=None)
                if self.actual['enable_auto_white_balance']['value']:settings['white_balance']=None
                data[self.camera]=settings;atomic_json(path,data)
                self.status.set('적용 및 camera_quality.json 저장 완료 · 다음 연결에도 사용합니다.')
            except Exception as exc:self.status.set('장치 적용은 완료했으나 설정 파일 저장 실패: '+str(exc))
            else:
                try:atomic_json(self.project/'logs'/('camera_'+self.camera+'_quality_actual.json'),self.actual)
                except OSError as exc:self.status.set('설정 파일 저장 완료 · 실제값 로그 저장 실패: '+str(exc))
    def poll(self):
        if self.timer:
            try:self.after_cancel(self.timer)
            except tk.TclError:pass
            self.timer=None
        if self.receipt is not None:
            try:result=self.receipt.get_nowait()
            except queue.Empty:
                if time.monotonic()>self.deadline:self.status.set('응답 지연 · 연결 상태를 확인하세요. 미실행 요청은 창을 닫거나 연결이 끊기면 취소됩니다. 이미 실행 중인 설정은 되돌리지 않습니다.')
            else:self.receipt=None;self.receive(result)
        packet=self.hub.get_latest_frame(self.camera)
        if packet is not None:
            self.picture,_,_=photo(packet.image,(760,330));self.preview.configure(image=self.picture,text='')
            q=image_quality(packet.image);self.metrics.set('전체 화면 참고 지표: '+str(round(q['laplacian_variance'],1))+' · '+(' / '.join(q['warnings']) or '경고 없음')+' · 스티커 부분은 원본 확대창에서 따로 확인하세요.')
        else:
            self.picture=None;self.preview.configure(image='',text='새 영상 없음 · 연결 상태를 확인하세요.');self.metrics.set('화질 측정 대기')
        self.timer=self.after(500,self.poll)
    def destroy(self):
        if self.receipt is not None:setattr(self.receipt,'cancelled',True)
        if self.timer:
            try:self.after_cancel(self.timer)
            except tk.TclError:pass
        self.picture=None;super().destroy()
