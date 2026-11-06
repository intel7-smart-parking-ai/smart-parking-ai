"""Regression tests for the same capture, input pixels and operator reports on V48/V49."""
import copy
import csv
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from parking_ai.storage import atomic_json
from pipeline import read_json
import test_v22
import test_v34
import test_v44
import test_v45
import test_v46
import test_v48


class CaptureHolds(unittest.TestCase):
    def setUp(self):
        from capture_recovery import journal_payload
        from review import save_review
        case=test_v22.DurableCollection()
        case.setUp()
        self.addCleanup(case.tearDown)
        self.case=case
        save_review(case.root,case.record,case.rows(),'operator')
        self.journal=case.root/'capture_journal'/(case.record['image_id']+'.json')
        other=copy.deepcopy(case.pred)
        other['spaces'][0]['issues'].append('다른 저장 판독')
        atomic_json(self.journal,journal_payload(case.record,other))

    def test_pending_single_cannot_deliver_any_training_crop(self):
        from dataset19 import export_training
        target,n=export_training(self.case.root)
        self.assertEqual(n,0)
        self.assertIn('복구',str(read_json(Path(target)/'manifest.json')['skipped']))

    def test_pending_single_cannot_deliver_collection(self):
        self.assertEqual(self.case.exported(),0)

    def test_pending_single_cannot_deliver_review_or_csv(self):
        from review import export_reviewed
        from pipeline import export_csv
        c=self.case
        self.assertEqual(export_reviewed(c.root)[1],0)
        with export_csv(c.root).open(encoding='utf-8-sig') as f:
            self.assertEqual(list(csv.DictReader(f)),[])

    def test_pending_review_keeps_original_approval(self):
        from review import save_review
        c=self.case
        path=c.root/'reviews'/self.journal.name
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'복구'):
            save_review(c.root,c.record,c.rows('42'),'operator')
        self.assertEqual(path.read_bytes(),before)

    def test_pending_single_excluded_from_audit_and_ready_counts(self):
        from dataset_audit import build_audit
        from dataset19 import DatasetIndex
        c=self.case
        self.assertEqual(build_audit(c.root)['metrics']['number']['compared'],0)
        self.assertEqual(DatasetIndex().scan(c.root)['counts']['number_ready'],0)

    def test_recover_verified_single_then_export_again(self):
        from capture_recovery import journal_payload,recover_captures
        from dataset19 import export_training
        c=self.case
        self.assertEqual(recover_captures(c.root,c.cfg)['복구보류수'],1)
        self.assertTrue(self.journal.exists())
        atomic_json(self.journal,journal_payload(c.record,c.pred))
        self.assertEqual(recover_captures(c.root,c.cfg)['복구완료수'],1)
        self.assertEqual((c.exported(),export_training(c.root)[1]),(1,3))


class ImageEvidence(unittest.TestCase):
    def guide(self,inner):
        from round_sticker import identify_guide
        case=test_v45
        image=case.IMAGE.copy();image[45:50,45:50]=0
        first=None,{'status':'round_sticker_unread','guide_ink':True,'candidates':[]}
        with patch('round_sticker.identify',side_effect=[first,*inner]):
            return identify_guide(image,{'polygon':case.POLY},None,case.CFG)

    def test_second_scale_multiple_regions_stay_on_review_queue(self):
        from sticker_guide import METHOD,waiting_for_sticker
        candidates=[{'number_candidate':'24'},{'number_candidate':'71'}]
        d,r=self.guide([(None,{'status':'multiple_stickers','candidates':candidates})])
        self.assertEqual((d,r['status'],r['candidates']),(None,'multiple_stickers',candidates))
        self.assertFalse(waiting_for_sticker({'registration_method':METHOD},{'errors':[],'spaces':[{'ocr_status':r['status']}]}))

    def test_conflicting_numbers_on_same_disc_retain_both_readings(self):
        disc={'center':[50,50],'radius':14,'color':'green'}
        def read(n):
            return disc,{'status':'candidate','number_candidate':n,'candidates':[{'number_candidate':n}]}
        d,r=self.guide([read('24'),read('71')])
        self.assertEqual((d,r['status'],r.get('number_candidate')),(disc,'sticker_number_unread',None))
        self.assertEqual([e['number_candidate'] for e in r['candidates']],['24','71'])

    def test_corroborating_views_must_refer_to_same_sticker(self):
        from prediction_contract import validate_prediction
        c=test_v22.DurableCollection();c.setUp();self.addCleanup(c.tearDown)
        r=c.pred['spaces'][0]
        for e in r['ocr_candidates']:
            if e is not r['selected_ocr'] and e['method']!=r['selected_ocr']['method']:
                e['text_bbox']=[1,1,10,10]
        with self.assertRaisesRegex(ValueError,'스티커'):
            validate_prediction(c.pred,c.record)

    def test_selected_reading_cannot_survive_unassigned_number(self):
        from prediction_contract import validate_prediction
        c=test_v22.DurableCollection();c.setUp();self.addCleanup(c.tearDown)
        row=c.pred['spaces'][0]
        row.update(number=None,plate_bbox=None,ocr_status='multiple_stickers')
        with self.assertRaises(ValueError):validate_prediction(c.pred,c.record)

    def test_exposure_status_must_match_measured_pixels(self):
        from sticker_exposure import diagnose,validate
        image=test_v45.IMAGE.copy();image[:]=255
        data=diagnose(image,image,{'polygon':test_v45.POLY})
        self.assertEqual(validate(data)['status'],'raw_saturated_without_detail')
        data['status']='no_clip_evidence'
        with self.assertRaises(ValueError):validate(data)


class OperatorReports(unittest.TestCase):
    def field(self,virtual):
        f=test_v46.FieldMetrics();f.setUp()
        for k in virtual:f.template['원본'][k]['위치근거']='가상 안내선 · 스티커 시험'
        f.form['원본']=copy.deepcopy(f.template['원본'])
        return f

    def test_only_virtual_observations_have_no_physical_accuracy(self):
        f=self.field('AB')
        f.form['주차칸'][0].update(점유='차량',번호=23,스티커색='초록')
        f.predictions['A']['spaces'][0].update(occupancy='occupied',number='23',color='green')
        report=f.compare()
        self.assertIsNone(report['차량칸_번호색점유_일치율'])
        self.assertEqual(report['스티커번호색대조']['가상배치']['직접일치'],1)

    def test_mixed_physical_score_excludes_virtual_success(self):
        f=self.field('B')
        for k in 'AB':
            row=next(r for r in f.form['주차칸'] if r['카메라']==k)
            row.update(점유='차량',번호=23,스티커색='초록')
        f.predictions['B']['spaces'][0].update(occupancy='occupied',number='23',color='green')
        report=f.compare()
        self.assertEqual((report['차량칸_번호색점유_일치율'],report['번호색점유_실물차량칸수']),(0,1))
        self.assertEqual(report['결과']['직접일치'],1)

    def test_virtual_empty_cannot_add_to_false_physical_occupancy(self):
        f=self.field('AB')
        f.predictions['A']['spaces'][0].update(occupancy='occupied',ocr_status='multiple_stickers')
        counts=f.compare()['결과']
        self.assertEqual(counts['빈칸오점유'],0)
        self.assertEqual(counts['가상빈배치_스티커관측'],1)

    def test_truth_with_duplicate_json_key_is_rejected(self):
        from field_check import field_check
        f=test_v44.FieldWorkflow()
        f.setUpClass();self.addCleanup(f.tearDownClass)
        path=f.root/'duplicate_truth.json'
        data=json.dumps(f.filled(),ensure_ascii=False)
        data=data.replace('"조명조건":','"조명조건":"충돌", "조명조건":',1)
        path.write_text(data,'utf8')
        with self.assertRaisesRegex(ValueError,'중복'):field_check(f.root,path)

    def test_late_write_does_not_extend_gap_during_measurement(self):
        from collection_run import CollectionRun
        with tempfile.TemporaryDirectory() as tmp,patch('collection_run.time.monotonic',return_value=10.) as clock:
            run=CollectionRun(tmp,'session',{})
            clock.return_value=15.;run.saved({'id':'first','skew_ms':2})
            clock.return_value=19.;run.stop_reason('정지')
            clock.return_value=70.;run.saved({'id':'last','skew_ms':3});run.checkpoint(True)
            doc=read_json(run.path)
            self.assertEqual((doc['측정활성_최장저장공백_초'],doc['최장저장공백_초']),(5,55))
            self.assertEqual(doc['완료측정쌍'],2)

    def test_one_shot_requires_current_connection_without_substituting_frames(self):
        from dual_capture import profile_specs,PairHold
        from dual_dashboard import DualDashboard
        app=Mock();app.profile=test_v34.profile()
        app.hub.recent_times.side_effect=lambda k,epoch: () if k=='B' else ((1,42.),)
        app.show_result.get.return_value=False;app.config_from_ui.return_value=app.profile
        app.specs={s.camera_id:s for s in profile_specs(app.profile)};app.last_keys={}
        now=time.monotonic()
        app.displayed_frames={k:replace(test_v34.data(k,k,42.).packet,received_monotonic=now) for k in 'AB'}
        with self.assertRaises(PairHold):DualDashboard.start_once(app)
        app.queue_analysis.assert_not_called()


class TrainingSummary(unittest.TestCase):
    def setUp(self):
        self.fixture=test_v48.DurableEvidence()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.target=self.fixture.delivery()

    def test_changed_group_summary_rejected(self):
        self.fixture.rewrite_manifest(self.target,lambda doc,row:doc['groups'].update({row['group']:'other'}))
        with self.assertRaises(ValueError):self.fixture.compare(self.target)

    def test_false_label_coverage_rejected(self):
        def alter(doc,row):
            doc['과제별_라벨_분할수']['number'][row['label']][row['split']]=True
        self.fixture.rewrite_manifest(self.target,alter)
        with self.assertRaises(ValueError):self.fixture.compare(self.target)

    def test_number_receipt_byte_count_rechecked(self):
        path=self.target/'EXPORT_STATUS.json';doc=read_json(path)
        next(r for r in doc['파일목록'] if r['파일'].startswith('number/'))['바이트']+=1
        atomic_json(path,doc)
        with self.assertRaises(ValueError):self.fixture.compare(self.target)

    def test_false_virtual_location_in_delivery_rejected(self):
        self.fixture.rewrite_manifest(self.target,lambda doc,row:row.update(실물칸확인=False))
        with self.assertRaises(ValueError):self.fixture.compare(self.target)

    def test_raw_pixel_identity_rechecked(self):
        self.fixture.rewrite_manifest(self.target,lambda doc,row:row.update(raw_pixels_sha256='0'*64))
        with self.assertRaises(ValueError):self.fixture.compare(self.target)


class EmptyReferenceEvidence(unittest.TestCase):
    def setUp(self):
        from empty_pair import EmptyView
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.views={}
        for i,k in enumerate('AB'):
            r=test_v34.data(k,k,42.+.012*i)
            self.views[k]=EmptyView(r.packet,r.roi)

    def test_wrong_valid_png_cannot_be_registered_as_empty(self):
        import numpy as np
        from empty_pair import store_empty_pair
        from dataset19 import write_png
        with patch('dataset19.write_png',side_effect=lambda path,pixels:write_png(path,np.zeros_like(pixels))):
            with self.assertRaisesRegex(ValueError,'픽셀'):store_empty_pair(self.root,self.views)
        self.assertFalse(list(self.root.rglob('reference.json')))

    def test_reference_stores_both_receipts_and_limit(self):
        from empty_pair import read_reference_source,store_empty_pair
        refs=store_empty_pair(self.root,self.views,max_skew_ms=30)
        e=refs['A'][1]['empty_board'];timing=e['빈기준_수신시각']
        self.assertEqual(timing,{'PC수신_ns':{'A':42000000000,'B':42012000000},'허용차_ms':30,'수신차_ms':12.})
        self.assertEqual(read_reference_source(self.root,e)[0]['근거'],e)

    def test_replay_rejects_inconsistent_empty_reference_timing(self):
        from empty_pair import read_reference_source,store_empty_pair
        store_empty_pair(self.root,self.views)
        path=next(self.root.rglob('reference.json'));doc=read_json(path)
        for member in doc['장면'].values():
            member['근거']['빈기준_수신시각']={'PC수신_ns':{'A':42000000000,'B':42012000000},'허용차_ms':100,'수신차_ms':0}
        atomic_json(path,doc)
        with self.assertRaisesRegex(ValueError,'수신'):read_reference_source(self.root,doc['장면']['A']['근거'])

    def test_legacy_reference_without_optional_pixel_or_time_evidence_loads(self):
        from empty_pair import read_reference_source,store_empty_pair
        store_empty_pair(self.root,self.views)
        path=next(self.root.rglob('reference.json'));doc=read_json(path)
        for member in doc['장면'].values():
            member['근거'].pop('빈기준_수신시각',None)
            member['근거'].pop('기준RGB_SHA256',None)
        atomic_json(path,doc)
        self.assertTrue(read_reference_source(self.root,doc['장면']['A']['근거'])[1])
