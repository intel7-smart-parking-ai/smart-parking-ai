"""Final audit: approval boundaries, original identity, imports and summary counts."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import main
import test_integration as fixture
import test_v9_gui as gui
from analysis import analyze
from contracts import CONTRACT_REVISION
from multi_vehicle import summarize
from parking_ai.storage import atomic_json
from pipeline import Analyzer, read_json, needs_analysis
from records import validate_record
from review import default_review, save_review, export_reviewed, rows_for_edit, original_image


class ApprovalIntegrity(unittest.TestCase):
    def test_corrupt_review_does_not_abort_other_exports(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=fixture.Contracts().fixture(root)
            atomic_json(root/'reviews'/path.name,{'image_id':record['image_id'],'lot_id':record['lot_id'],
                'contract_revision':CONTRACT_REVISION,'spaces':[None],'reviewer':'tester'})
            _,good=fixture.Contracts().fixture(root)
            rows=default_review(good);rows[0].update(occupancy='vacant',review_status='approved')
            save_review(root,good,rows,'tester')
            target,count=export_reviewed(root)
            self.assertEqual(count,1)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['image_id'],good['image_id'])
            self.assertEqual(len(read_json(target.with_suffix('.report.json'))['skipped']),1)

    def test_unknown_occupancy_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); _,record=fixture.Contracts().fixture(root)
            rows=default_review(record); rows[0]['review_status']='approved'
            with self.assertRaisesRegex(ValueError,'점유'):
                save_review(root,record,rows,'tester')
            self.assertFalse((root/'reviews'/(record['image_id']+'.json')).exists())

    def test_unknown_can_be_draft_and_known_occupied_can_be_partially_labeled(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); _,record=fixture.Contracts().fixture(root)
            rows=default_review(record)
            save_review(root,record,rows,'tester')
            rows[0].update(occupancy='occupied',review_status='approved')
            saved=save_review(root,record,rows,'tester')
            self.assertIsNone(saved['spaces'][0]['car_id'])
            self.assertEqual(saved['spaces'][0]['color'],'unknown')

    def test_old_unknown_approval_reopens_as_draft_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as d:
            _,record=fixture.Contracts().fixture(Path(d))
            rows=default_review(record);rows[0]['review_status']='approved'
            previous={'image_id':record['image_id'],'lot_id':record['lot_id'],
                      'contract_revision':CONTRACT_REVISION,'spaces':rows,'reviewer':'tester'}
            self.assertEqual(rows_for_edit(record,previous)[0]['review_status'],'pending')
            self.assertEqual(previous['spaces'][0]['review_status'],'approved')

    def test_export_skips_unknown_approval_but_keeps_valid_sibling(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=fixture.Contracts().fixture(root)
            record['roi']['spaces']=[
                {**fixture.SPACE,'polygon':[[10,10],[40,10],[40,90],[10,90]]},
                {**fixture.SPACE,'space_id':'A-02','polygon':[[55,10],[90,10],[90,90],[55,90]]}]
            atomic_json(path,record)
            rows=default_review(record)
            rows[0]['review_status']='approved'
            rows[1].update(review_status='approved',occupancy='vacant')
            legacy={'image_id':record['image_id'],'lot_id':record['lot_id'],
                    'contract_revision':CONTRACT_REVISION,'spaces':rows,'reviewer':'tester'}
            review_path=root/'reviews'/path.name;atomic_json(review_path,legacy)
            before=review_path.read_bytes()
            target,count=export_reviewed(root)
            self.assertEqual(count,1)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['space_id'],'A-02')
            self.assertTrue(any(s.get('space_id')=='A-01' for s in read_json(target.with_suffix('.report.json'))['skipped']))
            self.assertEqual(review_path.read_bytes(),before)


class OriginalIdentity(unittest.TestCase):
    def prepared(self,root):
        path,record=fixture.Contracts().fixture(root)
        # Explicitly populate the new optional field when reproducing on V16.
        record['raw_sha256']=hashlib.sha256((root/record['raw_path']).read_bytes()).hexdigest()
        atomic_json(path,record)
        return path,record

    def replace_image(self,root,record):
        ok,data=cv2.imencode('.png',np.full((100,100,3),255,np.uint8));self.assertTrue(ok)
        (root/record['raw_path']).write_bytes(data.tobytes())

    def test_new_capture_has_digest_of_exact_saved_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=fixture.Contracts().fixture(root)
            self.assertEqual(record.get('raw_sha256'),hashlib.sha256((root/record['raw_path']).read_bytes()).hexdigest())

    def test_review_rejects_same_size_replaced_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.prepared(root);self.replace_image(root,record)
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                original_image(root,record)

    def test_analyzer_rejects_same_size_replaced_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,record=self.prepared(root);self.replace_image(root,record)
            worker=Analyzer(root,root,fixture.CFG);worker.reader=fixture.Reader([fixture.HIT]);worker.engine={}
            with self.assertRaisesRegex(ValueError,'SHA-256'):worker.process(path)
            self.assertFalse((root/'analysis'/path.name).exists())

    def test_export_reports_replaced_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.prepared(root)
            rows=default_review(record);rows[0].update(occupancy='vacant',review_status='approved')
            save_review(root,record,rows,'tester');self.replace_image(root,record)
            target,count=export_reviewed(root)
            self.assertEqual(count,0)
            self.assertIn('SHA-256',read_json(target.with_suffix('.report.json'))['skipped'][0]['reason'])

    def test_bad_digest_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _,record=self.prepared(Path(d));record['raw_sha256']='bad-digest'
            with self.assertRaises(ValueError):validate_record(record)

    def test_legacy_without_digest_is_still_readable(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,record=self.prepared(root);record.pop('raw_sha256')
            self.assertEqual(original_image(root,record).shape,(100,100,3))


class SummaryIntegrity(unittest.TestCase):
    def test_number_candidate_does_not_reduce_human_review_count(self):
        rows=analyze(np.zeros((100,100,3),np.uint8),[fixture.SPACE],fixture.Reader([fixture.HIT]),fixture.CFG)
        result=summarize(rows)
        self.assertEqual(result['number_candidate_spaces'],1)
        self.assertEqual(result['needs_review_spaces'],1)

    def test_summary_separates_missing_number_from_pending_review(self):
        rows=analyze(np.zeros((100,100,3),np.uint8),[fixture.SPACE],fixture.Reader(),fixture.CFG)
        result=summarize(rows)
        self.assertEqual(result.get('missing_number_spaces'),1)
        self.assertEqual(result['needs_review_spaces'],1)

    def test_old_summary_triggers_reanalysis(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path,_=fixture.Contracts().fixture(root)
            worker=Analyzer(root,root,fixture.CFG);worker.reader=fixture.Reader([fixture.HIT]);worker.engine={};worker.process(path)
            result_path=root/'analysis'/path.name;result=read_json(result_path)
            result['frame_summary']['needs_review_spaces']=0
            result['frame_summary'].pop('missing_number_spaces',None)
            atomic_json(result_path,result)
            self.assertTrue(needs_analysis(read_json(path),result_path=result_path))


class GUIIntegrity(unittest.TestCase):
    app_context=gui.GUIRegressions.app_context

    def test_unknown_approval_button_shows_error_and_does_not_save(self):
        with self.app_context() as (app,window,project):
            _,record=fixture.Contracts().fixture(app.root);app.open_review(record)
            editor=app.review_windows[record['image_id']];editor.withdraw();editor.reviewer.set('tester')
            with patch('widgets.messagebox.showerror') as error:editor.approve_next()
            self.assertTrue(error.called)
            self.assertFalse(editor.approved.get())
            self.assertFalse((app.root/'reviews'/(record['image_id']+'.json')).exists())

    def test_old_unknown_approval_window_opens_as_pending(self):
        with self.app_context() as (app,window,project):
            path,record=fixture.Contracts().fixture(app.root);rows=default_review(record);rows[0]['review_status']='approved'
            atomic_json(app.root/'reviews'/path.name,{'image_id':record['image_id'],'lot_id':record['lot_id'],
                'contract_revision':CONTRACT_REVISION,'spaces':rows,'reviewer':'tester'})
            app.open_review(record);editor=app.review_windows[record['image_id']];editor.withdraw()
            self.assertFalse(editor.approved.get())

    def test_import_metadata_matches_bytes_loaded_before_roi_dialog(self):
        with self.app_context() as (app,window,project):
            source=project/'phone.png'
            ok,data=cv2.imencode('.png',np.zeros((100,100,3),np.uint8));self.assertTrue(ok)
            original=data.tobytes();source.write_bytes(original)
            def editor(*args,**kwargs):
                # External editor changes the source while the ROI dialog is open.
                ok,changed=cv2.imencode('.png',np.full((100,100,3),255,np.uint8))
                source.write_bytes(changed.tobytes())
                return SimpleNamespace(result=[copy.deepcopy(fixture.SPACE)])
            with patch('main.filedialog.askopenfilename',return_value=str(source)),patch('main.RoiEditor',side_effect=editor),patch.object(window,'wait_window'):
                app.import_image()
            deadline=time.monotonic()+3
            paths=[]
            while not paths and time.monotonic()<deadline:
                paths=list((app.root/'manifests').glob('*.json'));time.sleep(.01)
            self.assertEqual(len(paths),1);record=read_json(paths[0])
            self.assertEqual(record['source']['sha256'],hashlib.sha256(original).hexdigest())
            self.assertFalse(original_image(app.root,record).any())

    def test_small_tall_image_preview_has_nonzero_width(self):
        with self.app_context() as (app,window,project):
            from widgets import photo
            preview,_,_=photo(np.zeros((2000,1,3),np.uint8),(520,300))
            self.assertGreaterEqual(preview.width(),1)


if __name__=='__main__':unittest.main()
