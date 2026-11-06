import hashlib
import gc
import copy
import tempfile
import time
import unittest
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np
from collection_output import export_collection
from dual_capture import CHANNELS, PairHold, RegistrationHold, profile_specs, recover_pairs
from dual_dashboard import DualDashboard
from open_boundaries import matches_name_spacing
from pair_diagnostics import save_held_pair
from pipeline import read_json
from parking_ai.storage import atomic_json
from test_v19 import fixture
from test_v34 import data, profile
from test_v23_open_u import open_board


class HiddenBaySpacing(unittest.TestCase):
    def test_field_b03_with_covered_b02(self):
        boxes={'B-01':[1296,132,1364,164],'B-03':[1301,400,1375,432],'B-04':[1308,538,1380,568]}
        names={key:{'bbox':box} for key,box in boxes.items()}
        merged=[[1468,221],[1477,487],[1204,495],[1200,229]]
        single=[[1471,353],[1477,487],[1204,495],[1202,359]]
        for polygon,expected in ((merged,False),(single,True)):
            self.assertEqual(matches_name_spacing({'polygon':polygon},'B-03',names),expected)
            for order in (polygon,polygon[1:]+polygon[:1],polygon[::-1]):
                self.assertEqual(matches_name_spacing({'polygon':order,'_painted_closed':True},'B-03',names),expected)

    def test_several_hidden_numbers_and_row_ends(self):
        names={f'A-{n:02}':{'bbox':[100*n-4,96,100*n+4,104]} for n in (1,4,6)}
        for n in (1,4,6):
            x=n*100
            def candidate(left):
                return {'polygon':[[left,50],[x+50,50],[x+52,150],[left+2,150]]}
            self.assertTrue(matches_name_spacing(candidate(x-50),f'A-{n:02}',names))
            self.assertFalse(matches_name_spacing(candidate(x-150),f'A-{n:02}',names))


class CoveredMarkings(unittest.TestCase):
    def test_covered_interior_name_on_both_layouts_survives_storage_and_export(self):
        from automatic_setup import prepare_auto_scene
        from dataset19 import persist_scene
        from empty_pair import EmptyView,store_empty_pair
        from layouts import validate_snapshot
        from vision19 import VisionEngine
        for lot in CHANNELS:
            with self.subTest(lot=lot), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cfg,p,reader=open_board(lot)
                reader.observations=[r for r in reader.observations if r[1]!='A-03']
                image=p.image.copy()
                cv2.rectangle(image,(433,154),(547,233),(72,27,19),-1)
                p=replace(p,image=image)
                setup=prepare_auto_scene(root,p,lot,'pose',lambda:reader)
                self.assertTrue(setup['ready'],(setup['missing'],setup['errors']))
                roi=setup['roi']
                self.assertEqual(roi['registration_revision'],'roi-registration-v4')
                self.assertFalse((root/'calibration_anchors').exists())
                self.assertNotIn('A-03',[r['space_id'] for r in setup['text_evidence']])
                bad=copy.deepcopy(roi)
                slot=next(s for s in bad['spaces'] if s['space_id']=='A-03')
                slot['name_from_adjacent']=['A-01','A-04']
                with self.assertRaises(ValueError):validate_snapshot(bad)
                # The renderer and export validator must preserve the two real anchors.
                cfg['capture_source']={'kind':'import'}
                pred=VisionEngine().run(p,roi,cfg,mode='sticker')
                rec,_=persist_scene(root,p,roi,'b','s',cfg,pred)
                target,report=export_collection(root)
                self.assertEqual(report['counts']['space_pairs'],len(roi['spaces']))
                row=next(read_json(path) for path in (Path(target)/'collected').rglob('*.json')
                         if read_json(path).get('주차칸명')=='A-03')
                self.assertEqual(row['칸명확인에_사용한_양옆칸'],['A-02','A-04'])
                self.assertNotEqual(row['전용칸종류비교'],'종류 불일치 의심')
                self.assertTrue((root/rec['raw_path']).exists())
                peer=data('B','B',p.received_monotonic)
                # Only the first channel is needed to reject this as a new empty reference.
                camera=replace(p,camera_id='A',source_kind='camera')
                from layouts import calibration_key
                cam_roi={**roi,'camera_id':'A','calibration_key':calibration_key(lot,'A',camera.serial,roi['image_size'],'pose')}
                with self.assertRaisesRegex(ValueError,'칸 이름이 가려'):
                    store_empty_pair(root,{'A':EmptyView(camera,cam_roi),'B':EmptyView(peer.packet,peer.roi)},cfg)

    def test_missing_line_adjacent_holes_and_end_bay_remain_unassigned(self):
        from automatic_setup import detect_spaces
        _,packet,reader=open_board()
        reader.observations=[r for r in reader.observations if r[1] not in {'A-01','A-03','B-03','B-04'}]
        image=packet.image.copy()
        cv2.rectangle(image,(400,0),(599,254),(65,65,65),-1)
        result=detect_spaces(image,'A',reader)
        self.assertFalse(result['ready'])
        self.assertTrue({'A-01','A-03','B-03','B-04'}<=set(result['missing']))

    def test_car_over_known_print_cannot_replace_confirmed_empty_baseline(self):
        from automatic_setup import prepare_auto_scene
        from empty_pair import EmptyView,store_empty_pair
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg,p,reader=open_board()
            p=replace(p,camera_id='A',source_kind='camera')
            roi=prepare_auto_scene(root,p,'A','pose',lambda:reader)['roi']
            peer=data('B','B',p.received_monotonic)
            views={'A':EmptyView(p,roi),'B':EmptyView(peer.packet,peer.roi)}
            saved=store_empty_pair(root,views,cfg)
            frame=p.image.copy()
            box=next(r['bbox'] for r in roi['auto_validation']['text_evidence'] if r['space_id']=='B-06')
            a,b,c,d=box
            frame[b:d,a:c]=47
            with self.assertRaisesRegex(ValueError,'B-06'):
                store_empty_pair(root,{**views,'A':EmptyView(replace(p,image=frame),roi)},cfg)
            receipts=list((root/'references'/'dual').glob('*/reference.json'))
            self.assertEqual(len(receipts),1)
            self.assertEqual(receipts[0].parent.name,saved['A'][1]['empty_board']['빈기준ID'])


class HeldDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        now=time.monotonic()
        cls.results={k:data(k,k,now+i*.014) for i,k in enumerate(CHANNELS)}

    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.frames={k:r.packet for k,r in self.results.items()}
        self.profile=profile()
        self.hold=RegistrationHold(self.frames['A'],'A',{
            'detected_count':1,'expected_count':12,'spaces':self.results['A'].roi['spaces'][:1],
            'missing':[s['space_id'] for s in self.results['A'].roi['spaces'][1:]]})
        self.outcomes={'A':self.hold,'B':self.results['B']}

    def test_failed_pair_retains_both_originals_coordinates_and_hashes_but_exports_zero(self):
        path=save_held_pair(self.root,self.frames,self.outcomes,self.profile,'field')
        record=read_json(path/'진단.json')
        self.assertEqual(record['진단ID'],path.name)
        self.assertAlmostEqual(record['두프레임_수신차_ms'],14.,places=4)
        for k in CHANNELS:
            r=record['카메라'][k]
            self.assertEqual(r['찾은칸수'],1 if k=='A' else 16)
            self.assertEqual(r['후보칸'][0]['꼭짓점_px'], self.results[k].roi['spaces'][0]['polygon'])
            raw=(path/r['원본사진']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),r['원본_SHA256'])
            np.testing.assert_array_equal(cv2.imdecode(np.frombuffer(raw,np.uint8),1),self.frames[k].image)
            marked=(path/r['등록표시사진']).read_bytes()
            self.assertEqual(hashlib.sha256(marked).hexdigest(),r['등록표시_SHA256'])
            self.assertNotEqual(raw,marked)
        self.assertEqual(recover_pairs(self.root,{})['complete'],0)
        self.assertEqual(export_collection(self.root,paired_only=True)[1]['counts']['space_pairs'],0)

    def test_unknown_reason_is_not_an_observed_count_and_retry_is_separate(self):
        outcomes={**self.outcomes,'A':ValueError('마커 부족')}
        first=save_held_pair(self.root,self.frames,outcomes,self.profile,'a')
        second=save_held_pair(self.root,self.frames,outcomes,self.profile,'a')
        self.assertNotEqual(first,second)
        self.assertIsNone(read_json(first/'진단.json')['카메라']['A']['찾은칸수'])
        self.assertTrue((second/'진단.json').is_file())

    def test_disk_interruption_does_not_publish_partial_pair(self):
        with patch('pair_diagnostics.atomic_json',side_effect=OSError('disk')), self.assertRaises(OSError):
            save_held_pair(self.root,self.frames,self.outcomes,self.profile,'a')
        self.assertEqual(list((self.root/'diagnostics'/'held_pairs').iterdir()),[])

    def test_publish_retries_temporary_directory_lock_and_reports_persistent_failure(self):
        from parking_ai.storage import publish_directory
        stage=self.root/'staging';target=self.root/'complete'
        stage.mkdir()
        (stage/'진단.json').write_bytes(b'original')
        rename=Path.rename
        calls=[]
        def locked_once(source,destination):
            calls.append(source)
            if len(calls)==1:raise PermissionError('transient file lock')
            return rename(source,destination)
        with patch.object(Path,'rename',locked_once):
            publish_directory(stage,target)
        self.assertEqual(len(calls),2)
        self.assertEqual((target/'진단.json').read_bytes(),b'original')
        stage.mkdir()
        with self.assertRaises(FileExistsError):publish_directory(stage,target)
        with patch.object(Path,'rename',side_effect=PermissionError('persistent')),patch('parking_ai.storage.time.sleep'):
            with self.assertRaises(PermissionError):publish_directory(stage,self.root/'blocked')
        self.assertTrue(stage.exists())
        self.assertFalse((self.root/'blocked').exists())

    def test_diagnostic_pixel_file_must_match_digest_before_publication(self):
        from dataset19 import write_png
        def corrupt(path,image):
            digest=write_png(path,image)
            if path.name=='B_등록표시.png':path.write_bytes(b'damaged')
            return digest
        with patch('pair_diagnostics.write_png',side_effect=corrupt),self.assertRaisesRegex(OSError,'검증 실패'):
            save_held_pair(self.root,self.frames,self.outcomes,self.profile,'a')
        self.assertEqual(list((self.root/'diagnostics'/'held_pairs').iterdir()),[])

    def test_click_uses_displayed_pair_only_and_rejects_stale_or_saved_view(self):
        app=Mock(spec=DualDashboard)
        app.hub=Mock();app.config_from_ui.return_value=self.profile
        app.specs={s.camera_id:s for s in profile_specs(self.profile)}
        app.last_keys={};app.show_result=Mock();app.show_result.get.return_value=False
        now=time.monotonic()
        app.displayed_frames={k:replace(p,received_monotonic=now-.02) for k,p in self.frames.items()}
        DualDashboard.start_once(app)
        frames=app.queue_analysis.call_args.args[0]
        self.assertIs(frames['A'],app.displayed_frames['A'])
        app.hub.get_latest_frame.assert_not_called()
        self.assertTrue(app.one_shot)
        app.displayed_frames['B']=replace(app.displayed_frames['B'],received_monotonic=now-10)
        with self.assertRaisesRegex(PairHold,'만료'):DualDashboard.start_once(app)
        app.show_result.get.return_value=True
        with self.assertRaisesRegex(PairHold,'미리보기'):DualDashboard.start_once(app)

    def test_single_click_writes_one_measurement_then_a_separate_hold_for_visible_frames(self):
        project=self.root/'project'
        atomic_json(project/'dual_cameras.json',self.profile)
        cfg,_,_=fixture()
        root=self.root/'dataset'
        window=tk.Tk();window.withdraw()
        app=DualDashboard(window,cfg,root,project)
        def wait():
            deadline=time.monotonic()+30
            while app.busy() and time.monotonic()<deadline:
                window.update();time.sleep(.01)
            self.assertFalse(app.busy(),app.note.get())
        try:
            wait()
            app.hub=Mock()
            app.specs={s.camera_id:s for s in profile_specs(self.profile)}
            app.hub.statuses.return_value={k:{'status':'RUNNING'} for k in CHANNELS}
            def display(packets):
                now=time.monotonic()
                current={k:replace(p,received_monotonic=now-.01) for k,p in packets.items()}
                app.hub.get_latest_frame.side_effect=current.get
                app.step()
                return current
            current=display(self.frames)
            for k in CHANNELS:
                app.engines[k].analyze=Mock(return_value=replace(self.results[k],packet=current[k]))
            app.start_once();wait()
            self.assertFalse(app.running)
            self.assertEqual((app.run_count,app.bays),(1,{'A':12,'B':16}))
            clicked=display({k:replace(p,sequence=p.sequence+1) for k,p in current.items()})
            for k in CHANNELS:app.engines[k].analyze.side_effect=ValueError('보드 가림')
            app.start_once()
            newer={k:replace(p,sequence=p.sequence+90,image=np.zeros_like(p.image)) for k,p in clicked.items()}
            app.hub.get_latest_frame.side_effect=newer.get
            wait()
            self.assertFalse(app.running)
            self.assertEqual(app.run_count,1)
            diagnostic,=(root/'diagnostics'/'held_pairs').glob('*/진단.json')
            for k,row in read_json(diagnostic)['카메라'].items():
                self.assertEqual(row['프레임번호'],clicked[k].sequence)
                self.assertIs(app.engines[k].analyze.call_args.args[1],clicked[k])
                pixels=cv2.imdecode(np.frombuffer((diagnostic.parent/row['원본사진']).read_bytes(),np.uint8),1)
                np.testing.assert_array_equal(pixels,clicked[k].image)
            self.assertEqual(export_collection(root,paired_only=True)[1]['counts']['space_pairs'],28)
        finally:
            app.hub=None;app.close();window.mainloop()
            del app,window
            gc.collect()
