"""One entry point: capture/ROI, image import, OCR, review and exports."""
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import io
import logging
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import uuid
import cv2
import numpy as np
from PIL import Image,ImageOps
from camera_hub import CameraHub,FramePacket,load_camera_specs
from layouts import initialize,LOT_NAMES,layout,load_calibration,save_calibration,snapshot,calibration_key
from pipeline import read_json,Writer,Analyzer,export_csv,needs_analysis
from parking_ai.storage import atomic_json
from widgets import photo,RoiEditor,ReviewWindow
from review import export_reviewed

ROOT=Path(__file__).resolve().parent


def load_config():
    from settings import validate_config
    return validate_config(read_json(ROOT/'config.json'),ROOT)


class App:
    def __init__(self,window,cfg,root):
        self.window=window;self.cfg=cfg;self.root=root;self.root.mkdir(parents=True,exist_ok=True)
        self.hub=None;self.analyzer=None;self.writer=Writer(root);self.writer.start();self.closing=False
        self.quality_windows={};self.review_windows={};self.save_errors=[];self.calibration_checked={};self.last_camera_errors={}
        self.packets={};self.rois={};self.confirmed={c:False for c in ('A','B')};self.armed={c:False for c in ('A','B')}
        self.confirmed_epochs={c:None for c in ('A','B')}
        self.busy={c:False for c in ('A','B')};self.next_at={c:0. for c in ('A','B')};self.last={c:-1 for c in ('A','B')};self.counts={c:0 for c in ('A','B')}
        self.session=uuid.uuid4().hex;self.batch='batch-'+uuid.uuid4().hex[:12];self.latest_record=None;self.image_refs={}
        window.title('V51 주차장 기존 수집 화면 — ROI · 번호 · 스티커색');window.geometry('1120x820')
        self.notice=tk.StringVar(value='카메라 연결 후 주차장 매핑과 ROI를 확인하세요. 사진 입력은 카메라 없이 가능합니다.')
        self.ocr_status=tk.StringVar(value='OCR 대기 — [OCR 시작] 버튼으로 모델 로딩')
        notebook=ttk.Notebook(window);notebook.pack(fill='both',expand=True)
        capture=ttk.Frame(notebook);data=ttk.Frame(notebook);notebook.add(capture,text='카메라 · ROI · 사진 수집');notebook.add(data,text='저장 자료 · 검수 · 내보내기')
        ttk.Label(capture,text='① 카메라 연결 → ② 주차장 선택 → ③ 주차칸 ROI 등록 → ④ 위치 확인 → ⑤ 한 장 저장 → ⑥ OCR 시작 → ⑦ 검수',wraplength=1050).pack(fill='x',padx=10,pady=6)
        toolbar=ttk.Frame(capture);toolbar.pack(fill='x',padx=8,pady=8)
        for label,command in [('카메라 연결',self.start_cameras),('OCR 시작 / 재시도',self.start_ocr),('전체 일시정지',self.pause),('새 배치',self.new_batch),('최근 분석 검수',self.open_latest),('여러 차량 결과',lambda:self.guard(self.open_summary))]:ttk.Button(toolbar,text=label,command=command).pack(side='left',padx=3)
        self.lots={};self.panels={};self.states={}
        cameras=ttk.Frame(capture);cameras.pack(fill='x')
        for c in ('A','B'):
            panel=ttk.LabelFrame(cameras,text='카메라 '+c);panel.pack(side='left',fill='both',expand=True,padx=5)
            bar=ttk.Frame(panel);bar.pack(fill='x')
            ttk.Label(bar,text='보는 주차장').pack(side='left');self.lots[c]=tk.StringVar(value=cfg['camera_lots'][c])
            combo=ttk.Combobox(bar,textvariable=self.lots[c],values=['A','B'],width=5,state='readonly');combo.pack(side='left');combo.bind('<<ComboboxSelected>>',lambda e,c=c:self.change_lot(c))
            ttk.Label(bar,text='A=가 / B=나 (카메라 ID와 별개)').pack(side='left')
            self.panels[c]=ttk.Label(panel,text='카메라 연결 전');self.panels[c].pack(pady=5)
            self.states[c]=tk.StringVar();ttk.Label(panel,textvariable=self.states[c],wraplength=500).pack()
            buttons=ttk.Frame(panel);buttons.pack(pady=5)
            for label,fn in [('③ 주차칸 ROI 등록',self.edit_roi),('④ 위치 확인',self.confirm),('⑤ 한 장 저장',self.capture_once),('연속 수집 시작',self.start_capture)]:ttk.Button(buttons,text=label,command=lambda c=c,fn=fn:self.guard(lambda:fn(c))).pack(side='left')
            ttk.Button(panel,text='화질·카메라 설정 / 원본 확대',command=lambda c=c:self.guard(lambda:self.open_quality(c))).pack(pady=4)
        imports=ttk.LabelFrame(capture,text='휴대폰 사진 / 가상 주차장 이미지');imports.pack(fill='x',padx=8,pady=10)
        self.import_lot=tk.StringVar(value='A');self.import_kind=tk.StringVar(value='phone')
        ttk.Label(imports,text='주차장 A=가 B=나 C=다 D=라').pack(side='left',padx=5)
        ttk.Combobox(imports,textvariable=self.import_lot,values=list(LOT_NAMES),width=5,state='readonly').pack(side='left')
        ttk.Combobox(imports,textvariable=self.import_kind,values=['phone','virtual','other_image'],width=12,state='readonly').pack(side='left',padx=5)
        ttk.Button(imports,text='사진 열기 → ROI 지정 → 저장',command=lambda:self.guard(self.import_image)).pack(side='left')
        ttk.Label(capture,text='ROI·번호(01~100) OCR을 함께 저장합니다. 색상은 검수에서 지정한 스티커 내부만 측정합니다.\n가 12칸 / 나 16칸 / 다 12칸 / 라 10칸 · 흰색=일반 / 초록=전기차 / 파랑=장애인').pack(pady=8)
        ttk.Label(capture,textvariable=self.ocr_status,wraplength=1000).pack(fill='x',padx=10)
        dbar=ttk.Frame(data);dbar.pack(fill='x',padx=5,pady=8)
        for label,fn in [('목록 새로고침',self.refresh),('선택 사진 검수',self.open_selected),('여러 차량 결과',self.open_summary),('후보 CSV 저장',self.export_candidates),('승인 자료 내보내기',self.export_approved)]:ttk.Button(dbar,text=label,command=lambda fn=fn:self.guard(fn)).pack(side='left',padx=4)
        self.tree=ttk.Treeview(data,columns=('lot','camera','source','batch','status'),show='tree headings');self.tree.heading('#0',text='사진 ID')
        for col,label in [('lot','주차장'),('camera','카메라'),('source','출처'),('batch','배치'),('status','분석')]:self.tree.heading(col,text=label);self.tree.column(col,width=110)
        self.tree.pack(fill='both',expand=True,padx=5);self.tree.bind('<Double-1>',lambda e:self.guard(self.open_selected))
        ttk.Label(data,text='자동 후보와 사람이 승인한 정답을 별도 저장합니다. 내보내기는 팀 공통 JSONL이며 Geti 전용 형식은 아닙니다.').pack(pady=5)
        ttk.Label(window,textvariable=self.notice,wraplength=1080).pack(fill='x',padx=8,pady=8)
        window.protocol('WM_DELETE_WINDOW',self.close);self.refresh();window.after(100,self.poll)
    def guard(self,fn):
        if self.closing:return
        try:fn()
        except Exception as exc:logging.exception('UI action');messagebox.showerror('확인 필요',str(exc),parent=self.window)
    def pause(self):
        self.armed={c:False for c in self.armed};self.notice.set('수집 정지. 제출된 원본 저장은 완료합니다.')
    def new_batch(self):self.pause();self.batch='batch-'+uuid.uuid4().hex[:12];self.notice.set('새 배치: '+self.batch)
    def start_cameras(self):
        if self.closing or self.hub:return
        self.guard(self._start_cameras)
    def _start_cameras(self):
        self.hub=CameraHub(load_camera_specs(ROOT/'cameras.json'));self.hub.start();self.notice.set('연결 중. cameras.json의 시리얼이 실제 장치와 일치해야 합니다.')
    def start_ocr(self):
        if self.closing:return
        if not self.analyzer or not self.analyzer.is_alive():
            self.analyzer=Analyzer(ROOT,self.root,self.cfg);self.analyzer.request_retry_errors();self.analyzer.start()
        else:self.analyzer.request_retry_errors()
        self.notice.set('OCR 오류 자료 재시도를 요청했습니다. 진행 중 작업 뒤 한 번씩 재시도합니다.')
    def change_lot(self,c):
        if self.closing:return
        self.armed[c]=False;self.confirmed[c]=False;self.rois.pop(c,None);self.calibration_checked.pop(c,None);self.cfg['camera_lots'][c]=self.lots[c].get()
        self.guard(lambda:atomic_json(ROOT/'config.json',self.cfg))
    def open_quality(self,c):
        from camera_tools import CameraTools
        if not self.hub:raise ValueError('먼저 카메라를 연결하세요.')
        old=self.quality_windows.get(c)
        if old is not None and old.winfo_exists():old.lift();return
        self.pause();self.confirmed[c]=False
        self.quality_windows[c]=CameraTools(self.window,self.hub,c,ROOT)
    def edit_roi(self,c):
        self.pause()
        tool=self.quality_windows.get(c)
        if tool is not None and tool.winfo_exists():raise ValueError('카메라 설정 창을 먼저 닫으세요.')
        packet=self.hub.get_latest_frame(c) if self.hub else None
        if packet is None:raise ValueError('새 카메라 프레임이 없습니다.')
        lot=self.lots[c].get();self.confirmed[c]=False
        dialog=RoiEditor(self.window,packet.image,lot,(self.rois.get(c) or {}).get('spaces',[]));self.window.wait_window(dialog)
        if dialog.result:
            roi=snapshot(lot,c,packet.serial,packet.image.shape[1::-1],self.cfg['camera_pose'][c],dialog.result)
            save_calibration(ROOT,roi);self.rois[c]=roi;self.notice.set('ROI 저장. 실시간 화면과 맞는지 확인한 뒤 [위치 확인]을 누르세요.')
    def capture_packet(self,c,require_confirmed=True):
        from records import validate_capture
        if self.closing:raise ValueError('종료 중에는 새 촬영을 제출할 수 없습니다.')
        tool=self.quality_windows.get(c)
        if tool is not None and tool.winfo_exists():raise ValueError('카메라 설정 창을 닫은 뒤 위치 확인하세요.')
        packet=self.hub.get_latest_frame(c) if self.hub else None
        roi=self.rois.get(c)
        if packet is None or not roi or (require_confirmed and not self.confirmed[c]):raise ValueError('새 프레임과 확인된 ROI가 필요합니다.')
        if require_confirmed and self.confirmed_epochs[c]!=packet.stream_epoch:
            self.confirmed[c]=False;self.armed[c]=False
            raise ValueError('카메라 연결/설정이 변경되었습니다. 새 영상에서 위치를 다시 확인하세요.')
        if roi['lot_id']!=self.lots[c].get() or roi['pose']!=self.cfg['camera_pose'][c]:raise ValueError('주차장/설치가 변경되었습니다. ROI를 다시 확인하세요.')
        validate_capture(packet,roi)
        return packet
    def confirm(self,c):
        packet=self.capture_packet(c,False)
        self.confirmed_epochs[c]=packet.stream_epoch
        self.confirmed[c]=True;self.notice.set(f'카메라 {c} → {LOT_NAMES[self.lots[c].get()]} 주차장 위치 확인 완료')
    def start_capture(self,c):
        self.capture_packet(c)
        self.armed[c]=True;self.next_at[c]=time.monotonic()+self.cfg['warmup_seconds']
    def submit(self,c):
        packet=self.capture_packet(c)
        if self.busy[c] or self.last[c]==packet.sequence:return False
        cfg=copy.deepcopy(self.cfg);cfg['capture_source']={'kind':packet.source_kind,'captured_time_kind':'host_receive_utc' if packet.source_kind=='camera' else 'replay_receive_utc'}
        try:self.writer.jobs.put_nowait((packet,copy.deepcopy(self.rois[c]),self.batch,self.session,cfg))
        except queue.Full:return False
        self.busy[c]=True;self.last[c]=packet.sequence;return True
    def capture_once(self,c):
        self.armed[c]=False
        if not self.submit(c):self.notice.set('저장 중이거나 새 프레임이 없습니다. 잠시 뒤 다시 시도하세요.')
    def import_image(self):
        self.pause()
        path=filedialog.askopenfilename(parent=self.window,filetypes=[('이미지','*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff')])
        if not path:return
        path=Path(path)
        source_bytes=path.read_bytes()
        source_digest=hashlib.sha256(source_bytes).hexdigest()
        with Image.open(io.BytesIO(source_bytes)) as im: image=cv2.cvtColor(np.array(ImageOps.exif_transpose(im).convert('RGB')),cv2.COLOR_RGB2BGR)
        lot=self.import_lot.get();dialog=RoiEditor(self.window,image,lot);self.window.wait_window(dialog)
        if self.closing or not dialog.result:return
        roi=snapshot(lot,'IMPORT',path.name,image.shape[1::-1],'per-image',dialog.result)
        packet=FramePacket('IMPORT',path.name,0,datetime.now(timezone.utc).isoformat(),time.monotonic(),image,source_kind=self.import_kind.get())
        cfg=copy.deepcopy(self.cfg);cfg['capture_source']={'kind':self.import_kind.get(),'original_filename':path.name,'sha256':source_digest,'captured_time_kind':'import_time_not_camera_capture'}
        self.writer.jobs.put_nowait((packet,roi,self.batch,self.session,cfg));self.notice.set('사진 저장 요청 완료. OCR 시작 후 분석됩니다.')
    def refresh(self):
        selected=self.tree.selection()
        for item in self.tree.get_children():self.tree.delete(item)
        for path in sorted((self.root/'manifests').glob('*.json')):
            try:
                r=read_json(path);self.tree.insert('',0,iid=path.stem,text=path.stem[:12],values=(r['lot_name'],r['camera_id'],r['source']['kind'],r['batch_id'][:18],r['analysis_status']))
            except Exception:logging.exception('Invalid manifest %s',path)
        keep=[iid for iid in selected if self.tree.exists(iid)]
        if keep:self.tree.selection_set(keep)
    def open_review(self,record):
        identifier=record['image_id'];old=self.review_windows.get(identifier)
        if old is not None and old.winfo_exists():old.lift();old.focus_set();return
        self.review_windows[identifier]=ReviewWindow(self.window,self.root,record)
    def open_summary(self):
        from records import validate_record,validate_analysis
        from contracts import CONTRACT_REVISION
        from multi_vehicle import summarize
        selected=self.tree.selection()
        record=read_json(self.root/'manifests'/(selected[0]+'.json')) if selected else self.latest_record
        if not record:raise ValueError('분석한 사진을 선택하세요. 원본 한 장에 모든 보이는 칸의 ROI를 등록해야 합니다.')
        validate_record(record,self.root)
        result=read_json(self.root/'analysis'/(record['image_id']+'.json'))
        if result.get('contract_revision')!=CONTRACT_REVISION:raise ValueError('OCR을 다시 실행하세요.')
        validate_analysis(result,record);summary=summarize(result['spaces'])
        dialog=tk.Toplevel(self.window);dialog.title('한 사진 전체 칸 결과 · '+record['lot_name']+' 주차장')
        ttk.Label(dialog,text=f"등록 {summary['registered_spaces']}칸 · 번호 후보 {summary['number_candidate_spaces']}칸 · 번호 미확인 {summary['missing_number_spaces']}칸\n자동 결과 확인 대상 {summary['needs_review_spaces']}칸 · 번호 후보도 사람의 검수가 필요합니다(검수 진행률 아님).\n숫자 미인식은 빈칸이 아닙니다. 여러 후보는 모두 표시하며 차량 대수로 확정하지 않습니다.").pack(padx=10,pady=10)
        table=ttk.Treeview(dialog,columns=('number','status','all'),show='tree headings',height=16)
        table.heading('#0',text='칸');table.heading('number',text='번호 후보');table.heading('status',text='상태');table.heading('all',text='검출된 모든 글자 후보')
        for row in result['spaces']:
            texts=' / '.join(str(c['text'])+' ['+c['status']+']' for c in row.get('candidates',[]))
            table.insert('', 'end',iid=row['space_id'],text=row['space_id'],values=(row.get('number_candidate') or '미확인',row['status'],texts or '없음'))
        table.pack(fill='both',expand=True)
        def review_slot(event=None):
            selection=table.selection()
            if not selection:return
            self.open_review(record);editor=self.review_windows[record['image_id']]
            editor.choice.set(selection[0]);editor.select();editor.lift()
        table.bind('<Double-1>',review_slot)
        ttk.Button(dialog,text='선택 칸 검수',command=review_slot).pack(pady=8)
        dialog.geometry('1000x540')
    def open_selected(self):
        selected=self.tree.selection()
        if selected:self.open_review(read_json(self.root/'manifests'/(selected[0]+'.json')))
    def open_latest(self):
        if self.latest_record:self.guard(lambda:self.open_review(self.latest_record))
        else:self.notice.set('아직 완료된 분석이 없습니다. 저장 자료 탭에서는 분석 전 사진도 검수할 수 있습니다.')
    def export_candidates(self):self.notice.set('저장: '+str(export_csv(self.root)))
    def export_approved(self):
        path,count=export_reviewed(self.root);self.notice.set(f'승인 {count}개 칸 내보내기: {path} (제외 항목은 .report.json 확인)')
        report=read_json(path.with_suffix('.report.json'))
        folder=path.with_suffix('.images')
        if count:
            message=(f"승인 {count}개 칸 / 사진 {len(report['exported_images'])}장 저장 완료\n"
                     f"제외 항목: {len(report['skipped'])}개 (report.json 참고)\n\n"
                     "originals: 학습용 원본 사진\npreviews: 주차칸·번호·색 표시 사진 (사람 검수 정답)\n\n"
                     f"{folder}\n\n저장 폴더를 열까요?")
            if messagebox.askyesno('사진과 승인 자료 내보내기 완료',message,parent=self.window):
                import os
                os.startfile(str(folder.resolve()))
        else:
            messagebox.showinfo('내보내기 결과',f'내보낼 승인 자료가 없습니다.\n제외 항목: {len(report["skipped"])}개\n{path.with_suffix(".report.json")}',parent=self.window)
    def drain_saves(self):
        changed=False
        while True:
            try:c,path,error=self.writer.results.get_nowait()
            except queue.Empty:break
            changed=True
            if c in self.busy:self.busy[c]=False;self.next_at[c]=time.monotonic()+self.cfg['save_interval']
            if error:
                self.save_errors.append({'camera':c,'error':error})
                if c in self.armed:self.armed[c]=False
                self.notice.set('저장 오류: '+error)
            else:
                if c in self.counts:self.counts[c]+=1
                self.notice.set('원본+ROI 저장: '+Path(path).stem[:12])
        return changed
    def poll_camera(self,c,now):
        packet=self.hub.get_latest_frame(c);self.packets[c]=packet
        if packet is None:
            self.armed[c]=False;self.confirmed[c]=False
            info=self.hub.statuses().get(c,{}) if hasattr(self.hub,'statuses') else {}
            self.panels[c].configure(image='',text='새 영상 없음 / 재연결 대기')
            self.states[c].set('수집 정지 · '+str(info.get('last_error') or '재연결 후 위치 확인 필요'))
            return
        lot=self.lots[c].get();size=packet.image.shape[1::-1];key=calibration_key(lot,c,packet.serial,size,self.cfg['camera_pose'][c])
        if self.confirmed[c] and self.confirmed_epochs[c]!=packet.stream_epoch:
            self.confirmed[c]=False;self.armed[c]=False
            self.notice.set('카메라 '+c+' 연결/설정 변경 · 새 영상에서 위치를 다시 확인하세요.')
        if self.calibration_checked.get(c)!=key:
            self.confirmed[c]=False;self.armed[c]=False;self.rois.pop(c,None)
            roi=load_calibration(ROOT,lot,c,packet.serial,size,self.cfg['camera_pose'][c])
            self.rois[c]=roi;self.calibration_checked[c]=key
        view=packet.image.copy();roi=self.rois.get(c)
        for space in (roi or {}).get('spaces',[]):
            pts=np.array(space['polygon'],np.int32);cv2.polylines(view,[pts],True,(0,255,0),3);cv2.putText(view,space['space_id'],tuple(pts[0]),0,.8,(0,255,0),2)
        image,_,_=photo(view,(520,300));self.image_refs[c]=image;self.panels[c].configure(image=image,text='')
        self.states[c].set(f'{LOT_NAMES[lot]} 주차장 · ROI {len((roi or {}).get("spaces",[]))}/{layout(lot)["space_count"]} · 확인 {self.confirmed[c]} · 자동 {self.armed[c]} · 저장 {self.counts[c]}')
        if self.armed[c] and now>=self.next_at[c]:self.submit(c)
    def poll(self):
        if self.closing:return
        try:
            changed=self.drain_saves()
            if self.analyzer:
                self.ocr_status.set(self.analyzer.status)
                while True:
                    try:self.latest_record,_=self.analyzer.latest.get_nowait();changed=True
                    except queue.Empty:break
            if changed:self.refresh()
            if self.hub:
                for c in ('A','B'):
                    try:self.poll_camera(c,time.monotonic());self.last_camera_errors.pop(c,None)
                    except Exception as exc:
                        self.armed[c]=False;self.confirmed[c]=False
                        self.states[c].set('카메라 '+c+' 오류: '+str(exc))
                        if self.last_camera_errors.get(c)!=str(exc):logging.exception('Camera panel %s failed',c)
                        self.last_camera_errors[c]=str(exc)
        except Exception as exc:logging.exception('UI poll');self.notice.set('화면 처리 오류: '+str(exc))
        self.window.after(100,self.poll)
    def close(self):
        if self.closing:return
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        for child in descendants(self.window):
            if isinstance(child,RoiEditor):
                messagebox.showinfo('편집 중','ROI/스티커 편집을 완료하거나 취소한 뒤 종료하세요.',parent=self.window);return
        for child in list(self.window.winfo_children()):
            if isinstance(child,ReviewWindow) and child.winfo_exists() and not child.close():return
        self.closing=True;self.pause();self.notice.set('원본 저장과 현재 OCR 작업을 마치고 종료 중…')
        def disable(widget):
            for child in widget.winfo_children():
                disable(child)
                try:child.configure(state='disabled')
                except tk.TclError:pass
        disable(self.window)
        if self.hub:self.hub.request_stop()
        if self.analyzer:self.analyzer.stop_event.set()
        self.finish_close(False)
    def finish_close(self,sentinel):
        self.drain_saves()
        if not sentinel:
            try:self.writer.jobs.put_nowait(None);sentinel=True
            except queue.Full:pass
        if self.writer.is_alive() or (self.analyzer and self.analyzer.is_alive()) or (self.hub and hasattr(self.hub,'is_stopped') and not self.hub.is_stopped()):self.window.after(200,lambda:self.finish_close(sentinel));return
        self.drain_saves()
        if self.save_errors:
            try:
                atomic_json(self.root/'last_save_errors.json',self.save_errors)
                detail='last_save_errors.json을 확인하세요.'
            except OSError:
                logging.exception('Save error report failed');detail='오류 기록도 저장하지 못했습니다: '+self.save_errors[-1]['error']
            messagebox.showwarning('저장 오류 확인',f'{len(self.save_errors)}건 저장 실패. '+detail,parent=self.window)
        try:export_csv(self.root)
        except Exception:logging.exception('Final CSV failed')
        self.window.destroy()


def main():
    parser=argparse.ArgumentParser(description='V51 주차장 통합 데이터 수집: ROI + 번호판/스티커색');parser.add_argument('--check',action='store_true');parser.add_argument('--export-csv',action='store_true');parser.add_argument('--analyze-only',action='store_true');parser.add_argument('--retry-errors',action='store_true')
    parser.add_argument('--check-device',action='store_true');parser.add_argument('--check-ocr',action='store_true')
    parser.add_argument('--demo',action='store_true',help='A12/B16 synthetic demo without camera')
    parser.add_argument('--single',action='store_true',help='Open the legacy single-camera collection/review window')
    parser.add_argument('--lot',choices=['A','B'],default='A',help='Initial lot for the synthetic demo')
    args=parser.parse_args()
    if args.demo:
        from demo import main as demo_main
        demo_main(args.lot);return
    initialize(ROOT);cfg,root=load_config()
    if args.check_device:
        from intel_ocr import device_report
        import json
        print(json.dumps(device_report(),ensure_ascii=False,indent=2));return
    if args.check_ocr:
        from analysis import make_reader
        import json
        reader,info=make_reader(ROOT,cfg)
        print(json.dumps(info,ensure_ascii=False,indent=2));return
    if args.check:
        from quality import read_quality_config
        read_quality_config(ROOT/'camera_quality.json')
        load_camera_specs(ROOT/'cameras.json')
        from dual_capture import profile_specs
        profile_specs(read_json(ROOT/'dual_cameras.json'))
        if not hasattr(cv2,'aruco') or not hasattr(cv2.aruco,'ArucoDetector'):
            raise RuntimeError('OpenCV ArUco API가 없습니다. requirements.txt로 환경을 갱신하세요.')
        print('V51 config/layouts/camera settings OK: dual RealSense A/B; A=12 B=16 C=12 D=64. Hardware/models not opened.');return
    if args.export_csv:print(export_csv(root));return
    if args.analyze_only:
        from analysis import make_reader
        worker=Analyzer(ROOT,root,cfg);worker.reader,worker.engine=make_reader(ROOT,cfg);failures=0
        from records import validate_record
        for path in sorted((root/'manifests').glob('*.json')):
            try:
                record=validate_record(read_json(path),root,path.stem)
                if needs_analysis(record,args.retry_errors,root/'analysis'/path.name):worker.process(path);print(path.stem)
            except Exception as exc:failures+=1;print('ERROR',path.name,exc)
        print(export_csv(root))
        if failures:raise SystemExit(1)
        return
    (ROOT/'logs').mkdir(exist_ok=True);logging.basicConfig(filename=ROOT/'logs'/'app.log',level=logging.INFO,encoding='utf-8')
    if args.single:
        from dashboard import Dashboard as Console
    else:
        from dual_dashboard import DualDashboard as Console
    window=tk.Tk();Console(window,cfg,root,ROOT);window.mainloop()

if __name__=='__main__':main()
