"""Korean ROI and review UI shared by camera and photo inputs."""
import copy
import tkinter as tk
from tkinter import ttk,messagebox
import cv2
import numpy as np
from PIL import Image,ImageTk
from layouts import layout,validate_spaces,LOT_NAMES,TYPE_NAMES


def photo(image,max_size=(960,600)):
    h,w=image.shape[:2];scale=min(max_size[0]/w,max_size[1]/h,1.)
    width,height=max(1,round(w*scale)),max(1,round(h*scale))
    pil=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB)).resize((width,height))
    return ImageTk.PhotoImage(pil),width/w,height/h


class RoiEditor(tk.Toplevel):
    def __init__(self,parent,image,lot,initial=None,box_only=False,sticker_only=False,focus_polygon=None,inspect_only=False):
        self.inspect_only=inspect_only
        if inspect_only:box_only=True
        super().__init__(parent);self.title('현재 프레임 원본 확대 · 영역 화질(정지 사진)' if inspect_only else '스티커 내부 네 모서리 지정' if sticker_only else '번호판 전체 박스 지정' if box_only else f'{LOT_NAMES[lot]} 주차장 ROI 등록')
        self.image=image;self.lot=lot;self.box_only=box_only;self.sticker_only=sticker_only;self.result=None;self.points=[]
        self.spaces={s['space_id']:copy.deepcopy(s) for s in (initial or [])}
        self.catalog={s['space_id']:s for s in layout(lot)['spaces']}
        self.choice=tk.StringVar(value=next(iter(self.catalog)))
        ttk.Label(self,text='정지 원본입니다. 휠/버튼으로 확대하고 스티커 주변의 왼쪽 위·오른쪽 아래를 클릭한 뒤 [선택 영역 화질 확인]을 누르세요.' if inspect_only else '바닥·차체가 들어가지 않게 스티커 테두리 안쪽 네 모서리를 순서대로 클릭하세요.' if sticker_only else '번호판 전체의 왼쪽 위·오른쪽 아래 2점을 클릭하세요.' if box_only else '칸을 선택하고 네 모서리를 테두리 순서로 클릭 → [현재 칸 등록]. 보이는 칸만 등록할 수 있습니다.').pack(padx=8,pady=5)
        bar=ttk.Frame(self);bar.pack(fill='x')
        if not box_only and not sticker_only:
            combo=ttk.Combobox(bar,textvariable=self.choice,values=list(self.catalog),state='readonly',width=12);combo.pack(side='left');combo.bind('<<ComboboxSelected>>',lambda e:self.clear())
            ttk.Button(bar,text='현재 칸 등록',command=self.register).pack(side='left')
            ttk.Button(bar,text='현재 칸 삭제',command=self.remove).pack(side='left')
        ttk.Button(bar,text='원본 1:1',command=lambda:self.zoom(1/self.sx)).pack(side='left')
        if inspect_only:ttk.Button(bar,text='선택 영역 화질 확인',command=self.inspect_quality).pack(side='left')
        ttk.Button(bar,text='점 취소',command=self.undo).pack(side='left')
        ttk.Button(bar,text='닫기' if inspect_only else '완료',command=self.finish).pack(side='right')
        ttk.Button(bar,text='취소',command=self.destroy).pack(side='right')
        self.base_image=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB))
        h,w=image.shape[:2];self.vw=min(960,w);self.vh=min(600,h)
        self.fit_scale=min(self.vw/w,self.vh/h,1.);self.sx=self.sy=self.fit_scale
        self.ox=self.oy=0.;self.focus_polygon=focus_polygon;self.pan_anchor=None
        ttk.Button(bar,text='확대 +',command=lambda:self.zoom(1.5)).pack(side='left')
        ttk.Button(bar,text='축소 −',command=lambda:self.zoom(1/1.5)).pack(side='left')
        ttk.Button(bar,text='전체 보기',command=self.fit_view).pack(side='left')
        if focus_polygon is not None:ttk.Button(bar,text='선택 칸 확대',command=self.focus_view).pack(side='left')
        self.canvas=tk.Canvas(self,width=self.vw,height=self.vh,highlightthickness=0);self.canvas.pack()
        ttk.Label(self,text='왼쪽 클릭: 점 지정 · 휠: 확대/축소 · 오른쪽 버튼 드래그: 화면 이동 · 점 번호를 따라 테두리 순서로 지정').pack()
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<ButtonPress-3>',self.pan_start)
        self.canvas.bind('<B3-Motion>',self.pan_move)
        self.note=tk.StringVar();ttk.Label(self,textvariable=self.note).pack()
        self.canvas.bind('<Button-1>',self.click)
        if focus_polygon is not None:self.focus_view()
        else:self.draw()
        self.transient(parent);self.grab_set()
    def screen_to_image(self,x,y):return self.ox+x/self.sx,self.oy+y/self.sy
    def image_to_screen(self,x,y):return (x-self.ox)*self.sx,(y-self.oy)*self.sy
    def clamp_view(self):
        h,w=self.image.shape[:2]
        self.ox=max(0.,min(self.ox,max(0.,w-self.vw/self.sx)))
        self.oy=max(0.,min(self.oy,max(0.,h-self.vh/self.sy)))
    def fit_view(self):
        self.sx=self.sy=self.fit_scale;self.ox=self.oy=0.;self.draw()
    def zoom(self,factor,x=None,y=None):
        x=self.vw/2 if x is None else x;y=self.vh/2 if y is None else y
        ix,iy=self.screen_to_image(x,y)
        self.sx=self.sy=max(self.fit_scale,min(8.,self.sx*factor))
        self.ox=ix-x/self.sx;self.oy=iy-y/self.sy;self.clamp_view();self.draw()
    def wheel(self,event):
        if event.delta:self.zoom(1.25 if event.delta>0 else .8,event.x,event.y)
        return 'break'
    def focus_view(self):
        if not self.focus_polygon:self.fit_view();return
        points=np.asarray(self.focus_polygon);low=points.min(axis=0);high=points.max(axis=0)
        width,height=np.maximum(high-low,1)*1.25
        self.sx=self.sy=max(self.fit_scale,min(8.,self.vw/width,self.vh/height))
        center=(low+high)/2;self.ox=float(center[0]-self.vw/(2*self.sx));self.oy=float(center[1]-self.vh/(2*self.sy))
        self.clamp_view();self.draw()
    def pan_start(self,event):self.pan_anchor=(event.x,event.y,self.ox,self.oy)
    def pan_move(self,event):
        if self.pan_anchor:
            x,y,ox,oy=self.pan_anchor
            self.ox=ox-(event.x-x)/self.sx;self.oy=oy-(event.y-y)/self.sy;self.clamp_view();self.draw()
    def click(self,event):
        limit=2 if self.box_only else 4
        if len(self.points)>=limit:return
        h,w=self.image.shape[:2];x,y=self.screen_to_image(event.x,event.y)
        if not (0<=event.x<self.vw and 0<=event.y<self.vh and 0<=x<w and 0<=y<h):return
        self.points.append([min(w-1,round(x)),min(h-1,round(y))]);self.draw()
    def clear(self): self.points=[];self.draw()
    def undo(self):
        if self.points:self.points.pop()
        self.draw()
    def remove(self):self.spaces.pop(self.choice.get(),None);self.clear()
    def register(self):
        try:
            sid=self.choice.get();trial={**self.spaces,sid:{**self.catalog[sid],'polygon':copy.deepcopy(self.points)}}
            validate_spaces(self.lot,list(trial.values()),self.image.shape[1::-1]);self.spaces=trial;self.points=[];self.draw()
        except Exception as exc: messagebox.showerror('ROI 확인',str(exc),parent=self)
    def draw(self):
        self.canvas.delete('all')
        view=self.base_image.transform((self.vw,self.vh),Image.Transform.AFFINE,
            (1/self.sx,0,self.ox,0,1/self.sy,self.oy),resample=Image.Resampling.BILINEAR)
        self.picture=ImageTk.PhotoImage(view);self.canvas.create_image(0,0,image=self.picture,anchor='nw')
        for sid,s in self.spaces.items():
            pts=[self.image_to_screen(x,y) for x,y in s['polygon']]
            self.canvas.create_polygon(pts,fill='',outline='lime',width=2)
            self.canvas.create_text(*pts[0],text=f'{sid} {TYPE_NAMES[s["space_type"]]}',fill='yellow',anchor='nw')
        pts=[self.image_to_screen(x,y) for x,y in self.points]
        if self.box_only and len(pts)==2:self.canvas.create_rectangle(*pts[0],*pts[1],outline='cyan',width=2,tags='selection')
        elif len(pts)>1:
            coords=[v for point in pts for v in point]
            if len(pts)==4:coords+=list(pts[0])
            self.canvas.create_line(*coords,fill='orange',width=3,tags='selection')
        for i,(x,y) in enumerate(pts,1):
            self.canvas.create_oval(x-4,y-4,x+4,y+4,fill='red',outline='white')
            self.canvas.create_text(x+10,y-10,text=str(i),fill='yellow',font=('Arial',12,'bold'))
        zoom=f' · 원본 대비 배율 {self.sx:.2f}x'
        if self.sticker_only:
            state='테두리 안쪽을 순서대로 클릭하세요'
            if len(self.points)==4:
                from analysis import validate_sticker_polygon
                try:validate_sticker_polygon(self.points,self.image.shape[1::-1]);state='영역 확인 완료 · [완료]를 누르세요'
                except ValueError:state='점 순서/영역 오류 · [점 취소] 후 다시 지정하세요'
            else:state='테두리 안쪽을 순서대로 클릭하세요'
            self.note.set(f'스티커 내부 선택점 {len(pts)}/4 · {state}'+zoom)
        elif self.inspect_only:self.note.set(f'정지 프레임 · 스티커 주변 왼쪽 위/오른쪽 아래 두 점 선택 {len(pts)}/2 · 원본 1:1은 원본 한 픽셀을 화면 한 픽셀로 표시'+zoom)
        elif self.box_only:self.note.set(f'번호판 박스 선택점 {len(pts)}/2 · 왼쪽 위 / 오른쪽 아래'+zoom)
        else:self.note.set(f'등록 {len(self.spaces)}/{len(self.catalog)}칸 · 현재 {self.choice.get()} · 선택점 {len(pts)}/4'+zoom)
    def inspect_quality(self):
        if len(self.points)!=2:
            messagebox.showinfo('영역 지정','스티커 주변의 왼쪽 위와 오른쪽 아래를 클릭하세요.',parent=self);return
        from quality import image_quality
        x=sorted(p[0] for p in self.points);y=sorted(p[1] for p in self.points)
        if x[0]==x[1] or y[0]==y[1]:return
        q=image_quality(self.image[y[0]:y[1]+1,x[0]:x[1]+1])
        messagebox.showinfo('선택 영역 화질 · 참고용',f"원본 영역 {q['width']}×{q['height']} px\n선명도 지표 {q['laplacian_variance']:.1f}\n어두운 픽셀 {q['dark_ratio']:.1%} / 밝은 픽셀 {q['bright_ratio']:.1%}\n"+(' / '.join(q['warnings']) or '기준상 경고 없음')+'\n숫자가 실제로 읽히는지 원본 1:1에서 직접 비교하세요.',parent=self)
    def finish(self):
        if self.inspect_only:self.destroy();return
        if self.sticker_only:
            from analysis import validate_sticker_polygon
            try:validate_sticker_polygon(self.points,self.image.shape[1::-1])
            except ValueError as exc:
                messagebox.showerror('스티커 영역',str(exc),parent=self);return
            self.result=copy.deepcopy(self.points)
        elif self.box_only:
            if len(self.points)!=2:return
            x=sorted(p[0] for p in self.points);y=sorted(p[1] for p in self.points)
            if x[0]==x[1] or y[0]==y[1]:return
            self.result=[x[0],y[0],x[1],y[1]]
        else:
            if self.points:
                messagebox.showinfo('미등록 점','현재 점을 [현재 칸 등록]하거나 취소하세요.',parent=self);return
            if not self.spaces:return
            self.result=list(self.spaces.values())
        self.destroy()

class ReviewWindow(tk.Toplevel):
    def __init__(self,parent,root,record):
        from pipeline import read_json
        from review import rows_for_edit,review_revision,original_image
        from contracts import CONTRACT_REVISION
        self.root_path=root;self.record=record
        self.image=original_image(root,record)
        review_path=root/'reviews'/(record['image_id']+'.json')
        previous=read_json(review_path) if review_path.exists() else None
        self.expected_revision=review_revision(previous)
        self.rows=rows_for_edit(record,previous)
        if previous:
            from review import validate_review
            validate_review(record,self.rows,previous.get('reviewer'),self.image)
        prediction_path=root/'analysis'/(record['image_id']+'.json')
        try:
            prediction=read_json(prediction_path) if prediction_path.exists() else {'spaces':[]}
            if prediction.get('contract_revision')==CONTRACT_REVISION:
                from records import validate_analysis
                validate_analysis(prediction,record)
        except (OSError,ValueError,TypeError,AttributeError):prediction={'spaces':[]}
        self.predictions={r['space_id']:r for r in prediction['spaces']} if prediction.get('contract_revision')==CONTRACT_REVISION else {}
        super().__init__(parent);self.title(f'저장 사진 검수 · {LOT_NAMES[record["lot_id"]]} 주차장 · {record["image_id"][:8]}')
        self.choice=tk.StringVar(value=self.rows[0]['space_id']);self.current=self.rows[0]
        self.reviewer=tk.StringVar(value=previous['reviewer'] if previous else '')
        self.occupancy=tk.StringVar();self.number=tk.StringVar();self.color=tk.StringVar();self.approved=tk.BooleanVar()
        self.box=None;self.sticker_polygon=None;self.measurement=None;self.dirty=False
        self.feedback=tk.StringVar(value='① 점유·번호 확인 → ② 스티커 내부 지정 → ③ 색 확인 → ④ 승인 저장')
        if previous and any(r.get('review_status')=='approved' and r.get('occupancy')=='unknown' for r in previous['spaces']):
            self.feedback.set('이전 점유 미확인 승인을 초안으로 표시합니다. occupied/vacant를 확인한 뒤 다시 승인하세요.')
        head=ttk.Frame(self);head.pack(fill='x',padx=8,pady=6)
        ttk.Label(head,text='칸').pack(side='left')
        combo=ttk.Combobox(head,textvariable=self.choice,values=[r['space_id'] for r in self.rows],state='readonly',width=10);combo.pack(side='left');combo.bind('<<ComboboxSelected>>',self.select)
        ttk.Label(head,text='검수자').pack(side='left');ttk.Entry(head,textvariable=self.reviewer,width=16).pack(side='left')
        ttk.Button(head,text='초안/검수 저장 (Ctrl+S)',command=self.save).pack(side='right')
        ttk.Button(head,text='승인 저장 → 다음 칸',command=self.approve_next).pack(side='right',padx=4)
        ttk.Button(head,text='다음 칸',command=lambda:self.move_slot(1)).pack(side='left')
        ttk.Button(head,text='이전 칸',command=lambda:self.move_slot(-1)).pack(side='left')
        self.picture,self.sx,self.sy=photo(self.image,(900,440));self.canvas=tk.Canvas(self,width=self.picture.width(),height=self.picture.height());self.canvas.pack()
        self.hint=tk.StringVar();ttk.Label(self,textvariable=self.hint,wraplength=900).pack(fill='x',padx=8)
        form=ttk.Frame(self);form.pack(fill='x',padx=8,pady=5)
        for title,var,values in [('점유',self.occupancy,['unknown','occupied','vacant']),('스티커색',self.color,['unknown','white','green','blue'])]:
            ttk.Label(form,text=title).pack(side='left');ttk.Combobox(form,textvariable=var,values=values,state='readonly',width=10).pack(side='left')
        ttk.Label(form,text='번호 01~100' if record['lot_id'] in ('A','B') else '가상 번호판 / 표시 번호').pack(side='left');ttk.Entry(form,textvariable=self.number,width=16).pack(side='left')
        ttk.Checkbutton(form,text='이 칸 직접 확인·승인',variable=self.approved).pack(side='left')
        actions=ttk.Frame(self);actions.pack(fill='x',padx=8,pady=6)
        ttk.Button(actions,text='자동 후보를 입력란에 복사',command=self.copy_candidate).pack(side='left')
        ttk.Button(actions,text='번호판 전체 박스 지정',command=self.edit_box).pack(side='left')
        ttk.Button(actions,text='박스 삭제',command=self.clear_box).pack(side='left')
        colors=ttk.Frame(self);colors.pack(fill='x',padx=8,pady=4)
        ttk.Button(colors,text='스티커 내부 네 모서리 지정 + 색 측정',command=self.edit_sticker).pack(side='left')
        ttk.Button(colors,text='지정 영역 색 재측정',command=self.measure_color).pack(side='left')
        ttk.Button(colors,text='스티커 영역 삭제',command=self.clear_sticker).pack(side='left')
        ttk.Label(self,textvariable=self.feedback,wraplength=900,foreground='#174c8c').pack(fill='x',padx=8,pady=4)
        self._field_traces=[]
        for var in (self.number,self.color,self.occupancy):
            token=var.trace_add('write',lambda *args:self.approved.set(False))
            self._field_traces.append((var,token))
        ttk.Label(self,text='white=일반 · green=전기차 · blue=장애인 | 자동 OCR 박스는 글자 영역입니다. 색상은 사진별 스티커 내부만 측정합니다(바닥 제외).').pack(pady=5)
        self.bind('<Control-s>',lambda event:self.save())
        self.protocol('WM_DELETE_WINDOW',self.close)
        self.load_row();self.transient(parent)
        self.saved_state=self.edit_state()
    def destroy(self):
        # Remove Tcl callbacks on the UI thread; avoid retaining closed windows.
        for var,token in getattr(self,'_field_traces',[]):
            try:var.trace_remove('write',token)
            except tk.TclError:pass
        self._field_traces=[]
        self.picture=None
        super().destroy()
    def commit(self):
        self.current.update(occupancy=self.occupancy.get(),plate_text=self.number.get().strip(),color=self.color.get(),plate_bbox=copy.deepcopy(self.box),sticker_polygon=copy.deepcopy(self.sticker_polygon),review_status='approved' if self.approved.get() else 'pending')
    def select(self,event=None):
        self.commit();self.current=next(r for r in self.rows if r['space_id']==self.choice.get());self.load_row()
    def load_row(self):
        r=self.current;self.occupancy.set(r['occupancy']);self.number.set(r['plate_text']);self.color.set(r['color']);self.approved.set(r['review_status']=='approved');self.box=copy.deepcopy(r['plate_bbox']);self.sticker_polygon=copy.deepcopy(r.get('sticker_polygon'));self.measurement=None;self.draw()
    def draw(self):
        self.canvas.delete('all');self.canvas.create_image(0,0,image=self.picture,anchor='nw')
        slot=next(s for s in self.record['roi']['spaces'] if s['space_id']==self.current['space_id'])
        self.canvas.create_polygon([(x*self.sx,y*self.sy) for x,y in slot['polygon']],fill='',outline='lime',width=3)
        candidate=self.predictions.get(slot['space_id'],{})
        for item in candidate.get('candidates',[]):
            x1,y1,x2,y2=item['text_bbox'];self.canvas.create_rectangle(x1*self.sx,y1*self.sy,x2*self.sx,y2*self.sy,outline='magenta',width=2)
        if self.box:
            x1,y1,x2,y2=self.box;self.canvas.create_rectangle(x1*self.sx,y1*self.sy,x2*self.sx,y2*self.sy,outline='cyan',width=3)
        if self.sticker_polygon:
            self.canvas.create_polygon([(x*self.sx,y*self.sy) for x,y in self.sticker_polygon],fill='',outline='orange',width=3)
        region_status='지정됨' if self.sticker_polygon else '미지정: 색/종류 미확인'
        self.hint.set(f'{LOT_NAMES[self.record["lot_id"]]} 주차장 {slot["space_id"]} · {TYPE_NAMES[slot["space_type"]]}칸 | 자동 후보: 번호 {candidate.get("number_candidate") or "미확인"}, 색 {candidate.get("color_candidate","unknown")}, 상태 {candidate.get("status","분석 대기")} | 스티커 영역 {region_status} | 점유는 사람이 확인하세요.')
    def copy_candidate(self):
        c=self.predictions.get(self.current['space_id'],{})
        matches=[r for r in c.get('candidates',[]) if r.get('status')=='candidate' and r.get('number_candidate')==c.get('number_candidate')]
        self.number.set(matches[0]['text'] if len(matches)==1 else c.get('number_candidate') or '')
        self.approved.set(False)  # No inherited color from an unbounded OCR crop.
    def edit_box(self):
        dialog=RoiEditor(self,self.image,self.record['lot_id'],box_only=True);self.wait_window(dialog)
        if dialog.result:self.box=dialog.result;self.approved.set(False);self.draw()
    def edit_sticker(self):
        slot=next(s for s in self.record['roi']['spaces'] if s['space_id']==self.current['space_id'])
        dialog=RoiEditor(self,self.image,self.record['lot_id'],sticker_only=True,focus_polygon=slot['polygon']);self.wait_window(dialog)
        if dialog.result:
            self.sticker_polygon=dialog.result;self.approved.set(False);self.measure_color()
    def measure_color(self):
        from analysis import color_candidate
        try:
            self.measurement=color_candidate(self.image,self.sticker_polygon)
            self.color.set(self.measurement['name']);self.approved.set(False);self.draw()
        except ValueError as exc:messagebox.showerror('색상 측정',str(exc),parent=self)
    def clear_sticker(self):
        self.sticker_polygon=None;self.measurement=None;self.color.set('unknown');self.approved.set(False);self.draw()
    def clear_box(self):self.box=None;self.approved.set(False);self.draw()
    def edit_state(self):
        import json
        self.commit()
        return json.dumps([self.rows,self.reviewer.get()],sort_keys=True,ensure_ascii=False)
    def persist(self):
        from review import save_review_with_receipt,review_revision
        self.commit()
        data,warning=save_review_with_receipt(self.root_path,self.record,self.rows,self.reviewer.get(),expected_revision=self.expected_revision)
        self.expected_revision=review_revision(data)
        self.saved_state=self.edit_state()
        return warning
    def save(self):
        try:
            warning=self.persist()
            self.feedback.set(self.current['space_id']+' 검수 정답 저장 완료 · '+(warning or '계속 검수하거나 창을 닫으세요.'))
        except Exception as exc:messagebox.showerror('검수 확인',str(exc),parent=self)
    def move_slot(self,delta):
        index=next(i for i,r in enumerate(self.rows) if r['space_id']==self.current['space_id'])
        target=index+delta
        if 0<=target<len(self.rows):
            self.choice.set(self.rows[target]['space_id']);self.select()
    def approve_next(self):
        previous=self.approved.get()
        self.approved.set(True)
        try:warning=self.persist()
        except Exception as exc:
            self.approved.set(previous);self.commit()
            messagebox.showerror('승인 확인',str(exc),parent=self);return
        sid=self.current['space_id'];self.move_slot(1)
        self.feedback.set(sid+' 승인 저장 완료 · '+(warning or ('마지막 칸입니다.' if self.current['space_id']==sid else '다음 칸을 확인하세요.')))
    def close(self):
        if any(isinstance(child,RoiEditor) for child in self.winfo_children()):
            messagebox.showinfo('편집 중','스티커/번호판 영역 편집을 완료하거나 취소하세요.',parent=self);return False
        if self.edit_state()==self.saved_state:self.destroy();return True
        answer=messagebox.askyesnocancel('검수 종료','변경 내용을 저장할까요?',parent=self)
        if answer is None:return False
        if answer:
            try:
                warning=self.persist()
                if warning:messagebox.showwarning('검수 정답 저장 완료',warning,parent=self)
            except Exception as exc:messagebox.showerror('저장 실패',str(exc),parent=self);return False
        self.destroy();return True
