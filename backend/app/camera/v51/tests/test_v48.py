"""Cross-stage regression tests with the same inputs on V47 and V48."""
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from parking_ai.storage import atomic_json
from pipeline import read_json
import test_v22
import test_v34
import test_v45


class StickerAssignment(unittest.TestCase):
    def identify_two(self, second_read):
        from round_sticker import identify
        f=test_v45
        slot={'polygon':[[0,0],[119,0],[119,119],[0,119]]}
        discs=[{'radius':16,'polygon':[[x,15],[x+30,15],[x+30,45],[x,45]],'color':'white','center':[x+15,30]}
               for x in (5,72)]
        def shape(image,disc):
            x=disc['polygon'][0][0];num='24' if x==5 else '71'
            return {'score':.95 if x==5 else .85,'number':num,'alternate':'32','margin':.04,
                    'box':[x+5,20,x+25,39],'angle':0,'views':[np.zeros((20,30),np.uint8)]*2}
        reader=Mock()
        reader.recognize.side_effect=[[f.hit('24')]]*4+[[]]*4
        e={'number_candidate':'71','status':'candidate','score':.98,'method':'sticker_color','rotation_degrees':0}
        def local(image,polygon,*args):
            self.assertEqual(polygon,discs[1]['polygon'])
            return {'status':'candidate' if second_read else 'unread','number_candidate':'71' if second_read else None,
                    'selected':e if second_read else None,'candidates':[e] if second_read else []}
        with patch('round_sticker.proposals',return_value=discs),patch('print_estimator.examine',side_effect=shape), \
             patch('sticker_ocr.read_sticker',side_effect=local):
            return identify(f.IMAGE,slot,reader,f.CFG)

    def test_two_distinct_readable_stickers_in_one_bay_hold(self):
        disc,r=self.identify_two(True)
        self.assertIsNone(disc)
        self.assertEqual(r['status'],'multiple_stickers')
        self.assertFalse(r.get('number_candidate') or r.get('estimate'))
        self.assertEqual({e['number_candidate'] for e in r['candidates']},{'24','71'})

    def test_unconfirmed_second_region_does_not_cancel_single_read(self):
        self.assertEqual(self.identify_two(False)[1]['number_candidate'],'24')

    def test_virtual_retry_preserves_same_disc_conflict_evidence(self):
        from round_sticker import identify_guide
        disc={'center':[55,55],'radius':16,'color':'white','polygon':test_v45.POLY}
        def read(value):
            e={'number_candidate':value,'status':'candidate','score':.99,'text_bbox':[42,41,62,60]}
            return disc,{'number_candidate':value,'selected':e,'candidates':[e],'status':'candidate'}
        image=test_v45.IMAGE.copy();image[45:50,45:50]=0
        with patch('round_sticker.identify',side_effect=[read('66'),read('99')]):
            d,r=identify_guide(image,{'polygon':test_v45.POLY},None,test_v45.CFG)
        self.assertIs(d,disc);self.assertEqual(r['status'],'sticker_number_unread')
        self.assertEqual({e['number_candidate'] for e in r['candidates']},{'66','99'})
        self.assertFalse(r.get('selected') or r.get('estimate'))


class CameraEvidence(unittest.TestCase):
    def test_ambiguous_virtual_pair_reaches_original_storage_and_held_labels(self):
        from concurrent.futures import Future
        from dual_dashboard import DualDashboard
        from dual_capture import read_complete_measurement
        from sticker_guide import virtual_roi
        from records import load_prediction
        from vision19 import VisionEngine
        from dataset19 import export_training
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            app=Mock();app.extra=app.write_job=app.hub=app.run_record=None
            app.running=True;app.one_shot=app.closing=False;app.jobs={};app.frames={}
            app.root=root;app.session='pair';app.batches={'A':'a','B':'b'};app.profile={'max_skew_ms':100}
            app.busy.return_value=True;app.last_labels={k:Mock() for k in 'AB'};app.cfg={}
            def submit(fn,*args):
                done=Future();done.set_result(fn(*args));return done
            app.pool.submit.side_effect=submit
            for k in 'AB':
                r=test_v34.data(k,k,100.)
                roi=virtual_roi(r.packet,k,r.roi['pose'])
                pred=VisionEngine().run(r.packet,roi,r.cfg,None,'sticker')
                if k=='A':
                    pred['spaces'][0].update(ocr_status='multiple_stickers',issues=['한 칸에 두 스티커'])
                app.frames[k]=r.packet;f=Future();f.set_result(replace(r,roi=roi,prediction=pred));app.jobs[k]=f
            DualDashboard.step(app)
            app.pool.submit.assert_called_once()
            info=app.write_job.result();members=read_complete_measurement(root,info['id'])
            self.assertEqual(set(members),{'A','B'})
            record,assets=members['A'];row=load_prediction(root,record)['spaces'][0]
            self.assertEqual((row['occupancy'],row['number'],row['review_status']),('unknown',None,'pending'))
            self.assertEqual(export_training(root)[1],0)

    def test_cached_latest_pair_is_ineligible_after_disconnect_or_new_epoch(self):
        from camera_hub import CameraHub
        from dual_capture import continuous_pair,PairHold,profile_specs
        specs={s.camera_id:s for s in profile_specs(test_v34.profile())}
        hub=CameraHub(list(specs.values()));frames={}
        for k in 'AB':
            p=test_v34.data(k,k,42.).packet
            frames[k]=p;state=hub._states[k];state.status='RUNNING';state.stream_epoch=p.stream_epoch;state.recent.append(p)
        self.assertEqual(continuous_pair(hub,frames,specs,100,{},41.,42.1)[2],0.)
        state=hub._states['A'];state.status='RECONNECTING'
        with self.assertRaises(PairHold):continuous_pair(hub,frames,specs,100,{},41.,42.1)
        state.status='RUNNING';state.stream_epoch+=1;state.recent.clear()
        with self.assertRaises(PairHold):continuous_pair(hub,frames,specs,100,{},41.,42.1)

    def test_camera_serial_and_channel_must_match_roi_before_analysis(self):
        from vision19 import VisionEngine
        r=test_v34.data('A','A',42.);engine=VisionEngine()
        for p in [replace(r.packet,serial='other'),replace(r.packet,camera_id='B')]:
            with self.subTest(camera=p.camera_id),self.assertRaises(ValueError):
                engine.run(p,r.roi,r.cfg,None,'manual')

    def test_saved_candidate_cannot_be_enriched_with_other_camera_pixels(self):
        from vision19 import VisionEngine
        r=test_v34.data('A','A',42.)
        with self.assertRaises(ValueError):
            VisionEngine().enrich(replace(r.packet,serial='other'),r.roi,r.cfg,r.prediction)

    def test_api_events_reject_mixed_camera_and_roi(self):
        from transport19 import build_events
        from datetime import datetime,timezone
        r=test_v34.data('A','A',42.)
        p=replace(r.packet,timestamp_utc=datetime.now(timezone.utc).isoformat(),camera_id='B',serial='sim-B')
        pred={**r.prediction,'captured_at_utc':p.timestamp_utc}
        cfg=dict(api_contract_confirmed=True,api_nullable_bbox=True,api_nullable_confidence=True,api_camera_ids={'B':'cam-b'})
        with self.assertRaises(ValueError):build_events(p,r.roi,pred,cfg)


class DurableEvidence(unittest.TestCase):
    def setUp(self):
        c=test_v22.DurableCollection();c.setUp();self.addCleanup(c.tearDown);self.c=c

    def test_low_score_selected_observation_rejected_even_if_two_other_views_agree(self):
        from prediction_contract import validate_prediction
        c=self.c;pred=copy.deepcopy(c.pred);row=pred['spaces'][0]
        original=row['selected_ocr'];extra={**original,'status':'low_score','score':.01,'method':'other','selected_in_view':False}
        row['ocr_candidates'].append(extra);row['selected_ocr']=extra
        with self.assertRaises(ValueError):validate_prediction(pred,c.record)

    def journal(self):
        from capture_recovery import journal_payload
        c=self.c;path=c.root/'capture_journal'/(c.record['image_id']+'.json')
        atomic_json(path,journal_payload(c.record,c.pred))
        return path

    def test_recovery_preserves_disagreeing_but_valid_saved_prediction(self):
        from capture_recovery import recover_captures
        c=self.c;journal=self.journal();other=copy.deepcopy(c.pred);other['spaces'][0]['issues'].append('different analysis')
        target=c.root/'predictions'/(c.record['image_id']+'.json');atomic_json(target,other)
        before=target.read_bytes();summary=recover_captures(c.root,c.cfg)
        self.assertEqual((summary['복구완료수'],summary['복구보류수']),(0,1))
        self.assertTrue(journal.is_file());self.assertEqual(target.read_bytes(),before)

    def test_matching_journal_can_be_finished_and_missing_snapshot_restored(self):
        from capture_recovery import recover_captures
        c=self.c;journal=self.journal();target=c.root/'predictions'/(c.record['image_id']+'.json')
        target.unlink()
        self.assertEqual(recover_captures(c.root,c.cfg)['복구완료수'],1)
        self.assertEqual(read_json(target),c.pred);self.assertFalse(journal.exists())

    def test_manual_rebuild_cannot_remove_conflicting_journal(self):
        from dataset19 import rebuild_scene_assets
        c=self.c;journal=self.journal();other=copy.deepcopy(c.pred);other['spaces'][0]['issues'].append('different analysis')
        target=c.root/'predictions'/(c.record['image_id']+'.json');atomic_json(target,other)
        with self.assertRaisesRegex(ValueError,'복구 기록'):rebuild_scene_assets(c.root,c.record['image_id'],c.cfg)
        self.assertTrue(journal.exists())

    def delivery(self, approved='23'):
        from review import save_review
        from dataset19 import export_training
        c=self.c;save_review(c.root,c.record,c.rows(approved),'tester')
        target,_=export_training(c.root)
        return Path(target)

    def compare(self,target,truth='23'):
        from field_check import check_training
        c=self.c;sid=c.rows()[0]['space_id']
        report={'칸별결과':[{'카메라':'A','칸':sid,'점유':'차량','번호':int(truth)}]}
        return check_training(c.root,(target/'manifest.json').relative_to(c.root).as_posix(),{'A':(c.record,None)},report,{'A':c.pred})

    def rewrite_manifest(self,target,edit):
        path=target/'manifest.json';doc=read_json(path)
        number=next(r for r in doc['samples'] if r['task']=='number');edit(doc,number);atomic_json(path,doc)
        status=target/'EXPORT_STATUS.json';receipt=read_json(status)
        row=next(r for r in receipt['파일목록'] if r['파일']=='manifest.json')
        payload=path.read_bytes();row.update(SHA256=hashlib.sha256(payload).hexdigest(),바이트=len(payload));atomic_json(status,receipt)

    def test_truth_verification_distinguishes_present_from_correct(self):
        target=self.delivery('42');out=self.compare(target)
        self.assertEqual((out['현장정답불일치'],out['미전달정답번호관측수']),(1,0))
        self.assertEqual((out['검증통과번호관측수'],out['미검증정답번호관측수']),(0,1))

    def test_valid_number_is_counted_once_as_verified(self):
        out=self.compare(self.delivery())
        self.assertEqual((out['번호사진'],out['검증통과번호관측수'],out['미검증정답번호관측수']),(1,1,0))

    def test_reformatting_an_old_review_does_not_destroy_its_exported_revision(self):
        from dataset19 import export_training
        from review import save_review
        c=self.c;save_review(c.root,c.record,c.rows(),'tester')
        path=c.root/'reviews'/(c.record['image_id']+'.json')
        payload=b'\xef\xbb\xbf'+json.dumps(read_json(path),ensure_ascii=False,separators=(',',':')).encode('utf8')
        path.write_bytes(payload)
        export,_=export_training(c.root)
        save_review(c.root,c.record,c.rows('42'),'tester')
        self.assertIn(payload,[h.read_bytes() for h in (path.parent/'history').glob('*.json')])
        self.assertEqual(self.compare(Path(export))['검증통과번호관측수'],1)

    def test_duplicate_receipt_paths_do_not_count_as_verified_files(self):
        target=self.delivery();path=target/'EXPORT_STATUS.json';data=read_json(path)
        data['파일목록'].append(data['파일목록'][0].copy());data['검증파일수']+=1;atomic_json(path,data)
        with self.assertRaisesRegex(ValueError,'중복'):self.compare(target)

    def test_duplicate_manifest_sample_cannot_multiply_field_result(self):
        target=self.delivery()
        self.rewrite_manifest(target,lambda doc,row:doc['samples'].append(row.copy()))
        with self.assertRaisesRegex(ValueError,'중복'):self.compare(target)

    def test_changed_split_metadata_is_not_an_accepted_number_delivery(self):
        target=self.delivery()
        self.rewrite_manifest(target,lambda doc,row:row.update(split='test' if row['split']=='train' else 'train'))
        with self.assertRaisesRegex(ValueError,'분할|경로'):self.compare(target)
