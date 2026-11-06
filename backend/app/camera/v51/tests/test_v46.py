"""Field evaluation and collection safeguards, with controlled images and clocks."""
import copy
import csv
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from camera_hub import CameraHub
from collection_run import CollectionRun
from dataset19 import export_training
from dual_capture import active_batches,continuous_pair,profile_specs
from empty_pair import EmptyView,store_empty_pair
from field_check import compare_layout,form_for
from pipeline import read_json
from prediction_contract import validate_prediction
from review import rows_for_edit,save_review
from sticker_guide import virtual_roi
from test_v19 import fixture
from test_v34 import data,profile
from vision19 import VisionEngine


class FieldMetrics(unittest.TestCase):
    def setUp(self):
        self.members={};self.predictions={}
        for channel in 'AB':
            r=data(channel,'A',100.)
            self.members[channel]=(dict(image_id=channel,raw_sha256=channel,lot_id='A',roi=r.roi),None)
            self.predictions[channel]=r.prediction
        self.template=form_for('pair',self.members)
        self.form=copy.deepcopy(self.template);self.form['조명조건']='합성'
        for row in self.form['주차칸']:row['점유']='빈칸'
    def compare(self):
        return compare_layout(self.form,self.template,self.members,self.predictions)
    def test_counts_distinct_roster_ids_at_two_views_and_legacy_missing_id(self):
        for r in self.form['주차칸']:
            r.update(점유='차량',번호=23,스티커색='초록',차량식별표=r['칸'])
        counts=self.compare()['차량식별대조']
        self.assertEqual((counts['차량관측칸'],counts['서로다른번호수'],counts['정답표상_고유차량수']),(24,1,12))
        del self.form['주차칸'][0]['차량식별표']
        self.assertIsNone(self.compare()['차량식별대조']['정답표상_고유차량수'])
    def test_extra_warning_counts_even_when_one_kind_matches(self):
        row=self.form['주차칸'][0];row['주차선']='선침범'
        self.predictions['A'].update(mode='reference',reference={'key':'test'},reference_error=None,
            geometry=[dict(reason=r,spaces=[row['칸']]) for r in ('주차선 침범 변화 후보','주차칸 입구 돌출 변화 후보')])
        r=self.compare();self.assertEqual(r['주차선대조']['오경보'],1)
        self.assertEqual(r['주차선유형별대조']['선침범']['적중'],1)
        self.assertEqual(r['주차선유형별대조']['입구돌출']['오경보'],1)
        row['주차선']=['선침범','입구돌출'];self.assertEqual(self.compare()['주차선대조']['일치'],1)
        row['주차선']=['정상','선침범']
        with self.assertRaises(ValueError):self.compare()
    def test_mixed_views_have_separate_physical_occupancy_denominator(self):
        self.template['원본']['B']['위치근거']='가상 안내선 · 스티커 시험'
        self.form['원본']=copy.deepcopy(self.template['원본'])
        self.assertEqual(self.compare()['실물점유대조']['비교칸'],12)
    def test_sticker_score_is_independent_of_unknown_occupancy(self):
        row=self.form['주차칸'][0];row.update(점유='차량',번호=23,스티커색='초록')
        self.predictions['A']['spaces'][0].update(number='23',color='green',occupancy='unknown')
        r=self.compare()
        self.assertEqual(r['스티커번호색대조']['실물칸']['직접일치'],1)
        self.assertEqual(r['위치근거별_차량관측대조']['실물칸']['판독보류'],1)


class CollectionSafety(unittest.TestCase):
    def test_closest_pair_includes_both_histories_and_uses_newest_equal_skew(self):
        specs={s.camera_id:s for s in profile_specs(profile())};hub=CameraHub(list(specs.values()))
        _,p,_=fixture();frames={}
        for k,times in {'A':[0.,.25,.283,.316],'B':[.005,.038,.071,.115]}.items():
            state=hub._states[k];state.status='RUNNING';state.stream_epoch=p.stream_epoch
            for i,t in enumerate(times):
                frames[k]=replace(p,camera_id=k,serial=specs[k].serial,sequence=i+1,received_monotonic=42+t)
                state.recent.append(frames[k])
        _,keys,delta=continuous_pair(hub,frames,specs,100,{},42-1,42.4)
        self.assertEqual((keys['A'][2],keys['B'][2],delta),(1,1,5))
        for k in 'AB':
            frames[k]=replace(frames[k],sequence=6,received_monotonic=42.40+.005*(k=='B'))
            hub._states[k].recent.append(frames[k])
        _,keys,delta=continuous_pair(hub,frames,specs,100,{},42-1,42.44)
        self.assertEqual((keys['A'][2],keys['B'][2],delta),(6,6,5))
    def test_weak_empty_baseline_requires_the_same_reviewed_bays(self):
        from contracts import FIELD_SET
        with tempfile.TemporaryDirectory() as tmp:
            views={k:EmptyView(r.packet,r.roi) for k in 'AB' for r in [data(k,k,100.)]}
            with patch('empty_pair.disc_evidence',return_value=([],('A-01',))):
                with self.assertRaisesRegex(ValueError,'약한 스티커'):store_empty_pair(tmp,views,{'physical_sticker_set':FIELD_SET})
                self.assertFalse(list(Path(tmp).rglob('reference.json')))
                result=store_empty_pair(tmp,{k:replace(v,suspect_bays=('A-01',)) for k,v in views.items()},{'physical_sticker_set':FIELD_SET})
                self.assertEqual(set(result),{'A','B'})
                member=read_json(next(Path(tmp).rglob('reference.json')))['장면']['A']
                self.assertEqual(member['촬영자_재확인칸'],['A-01'])
    def test_virtual_coordinates_cannot_be_a_reference_or_emit_physical_events(self):
        from transport19 import build_events
        r=data('A','A',100.);p=r.packet;roi=virtual_roi(p,'A',r.roi['pose'])
        with self.assertRaisesRegex(ValueError,'가상'):VisionEngine().run(p,roi,r.cfg,{},'reference')
        b=data('B','B',100.)
        with tempfile.TemporaryDirectory() as tmp,self.assertRaisesRegex(ValueError,'실물 주차칸'):
            store_empty_pair(tmp,{'A':EmptyView(p,roi),'B':EmptyView(b.packet,b.roi)})
        pred=VisionEngine().run(p,roi,r.cfg,None,'sticker')
        self.assertEqual(build_events(p,roi,pred,{'api_contract_confirmed':True}),[])
        pred['geometry']=[{}]
        with self.assertRaisesRegex(ValueError,'가상'):
            validate_prediction(pred,dict(roi=roi,frame_id=p.sequence,captured_at_utc=p.timestamp_utc,
                                lot_id='A',stream_epoch=p.stream_epoch,image_size=roi['image_size']))
    def test_restart_reuses_batch_until_operator_changes_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids=active_batches(tmp);self.assertEqual(ids,active_batches(tmp))
            self.assertNotEqual(ids,active_batches(tmp,renew=True))
            (Path(tmp)/'dual_batches.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'배치 ID'):active_batches(tmp)
    def test_virtual_approvals_stay_out_of_physical_counts_and_csv_marks_location(self):
        from dataset_audit import build_audit
        from dataset19 import DatasetIndex,persist_scene
        from pipeline import export_csv
        r=data('A','A',100.);p=r.packet;roi=virtual_roi(p,'A',r.roi['pose'])
        pred=VisionEngine().run(p,roi,r.cfg,None,'sticker')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record,_=persist_scene(root,p,roi,'v','s',r.cfg,pred)
            rows=rows_for_edit(record);rows[0].update(occupancy='vacant',review_status='approved')
            save_review(root,record,rows,'test')
            counts=DatasetIndex().scan(root)['counts']
            self.assertEqual((counts['occupancy_approved'],counts['occupancy_ready']),(0,0))
            metric=build_audit(root)['metrics']['occupancy'];self.assertEqual(metric['compared'],0)
            self.assertEqual(export_training(root)[1],0)
            with export_csv(root).open(encoding='utf-8-sig') as f:
                values=list(csv.DictReader(f))
            self.assertEqual({v['position_basis'] for v in values},{'virtual_guide'})
            self.assertTrue(all(v['space_label'].startswith('가상 배치') for v in values))
    def test_timing_summary_deduplicates_waits_and_includes_final_gap(self):
        _,p,_=fixture()
        with tempfile.TemporaryDirectory() as tmp,patch('collection_run.time.monotonic',return_value=10.) as clock:
            run=CollectionRun(tmp,'session',{});run.waiting({'A':p},'B 대기');run.waiting({'A':p},'B 대기')
            clock.return_value=15.;run.saved({'id':'one','skew_ms':4.})
            clock.return_value=22.;run.checkpoint(True)
            r=read_json(run.path)
            self.assertEqual((r['실제경과_초'],r['최장저장공백_초'],r['짝선택대기관측수'],r['완료측정쌍'],r['상태']),(12.,7.,1,1,'종료'))
    def test_identical_number_crop_in_different_splits_aborts_export(self):
        from test_v22 import DurableCollection
        from dataset19 import persist_scene
        from hashlib import sha256
        case=DurableCollection();case.setUp();self.addCleanup(case.tearDown)
        bucket=lambda g:int(sha256(g.encode()).hexdigest()[:8],16)%100<70
        other=next('audit'+str(i) for i in range(1000) if bucket('audit'+str(i))!=bucket('batch'))
        save_review(case.root,case.record,case.rows(),'test')
        image=case.p.image.copy();image[24:28,24:28]=120
        p=replace(case.p,image=image,sequence=case.p.sequence+1)
        record,_=persist_scene(case.root,p,case.roi,other,'session',case.cfg)
        rows=rows_for_edit(record);rows[0].update(case.rows()[0]);save_review(case.root,record,rows,'test')
        with self.assertRaisesRegex(ValueError,'동일 학습 크롭 분할 누수.*number/'):export_training(case.root)
        target=next((case.root/'exports').iterdir())
        self.assertEqual(read_json(target/'EXPORT_STATUS.json')['상태'],'실패')
        self.assertFalse((target/'manifest.json').exists())
