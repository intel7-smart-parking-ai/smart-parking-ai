"""Review commit boundaries and the integrity of re-exported training data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import test_v22
from dashboard import Dashboard
from dataset19 import export_training, persist_scene, rebuild_scene_assets
from contracts import FIELD_SET
from dataset_audit import build_audit
from export_integrity import ExportTransaction
from parking_ai.storage import atomic_json
from pipeline import read_json
from review import review_revision, rows_for_edit, save_review, save_review_with_receipt
from saved_scenes import load_saved_scene


class ReviewTransactions(unittest.TestCase):
    def setUp(self):
        self.case = test_v22.DurableCollection()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.root, self.record = self.case.root, self.case.record
        self.name = self.record['image_id'] + '.json'
        self.rows = self.case.rows()
        self.rows[1].update(occupancy='vacant', review_status='approved')

    def test_failed_review_commit_preserves_previous_complete_files_and_retry(self):
        saved = save_review(self.root, self.record, self.rows, 'first')
        paths = [self.root / folder / self.name for folder in ('manifests', 'reviews', 'bundles')]
        before = [p.read_bytes() for p in paths]
        edits = rows_for_edit(self.record, saved)
        edits[0]['plate_text'] = '42'
        def fail_review(path, value):
            if Path(path) == paths[1]:
                raise OSError('disk full')
            atomic_json(path, value)
        with patch('review.atomic_json', side_effect=fail_review), self.assertRaises(OSError):
            save_review(self.root, self.record, edits, 'next', review_revision(saved))
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertEqual(self.case.exported(), 1)
        new = save_review(self.root, self.record, edits, 'next', review_revision(saved))
        self.assertEqual(new['spaces'][1]['review_status'], 'approved')
        self.assertEqual(self.case.label()['차량번호'], 42)

    def test_durable_review_with_failed_photo_reports_repair_then_reexports(self):
        with patch('collection_output.render_collection', side_effect=OSError('photo refresh')):
            saved, warning = save_review_with_receipt(self.root, self.record, self.rows, 'reviewer')
        self.assertIn('photo refresh', warning)
        self.assertEqual(saved, read_json(self.root/'reviews'/self.name))
        self.assertEqual(self.case.exported(), 0)
        rebuild_scene_assets(self.root, self.record['image_id'], self.case.cfg)
        self.assertEqual(self.case.exported(), 1)
        self.assertEqual(self.case.label()['차량번호'], 23)

    def test_manifest_failure_after_label_commit_blocks_old_bundle(self):
        def fail_manifest(path, value):
            if Path(path) == self.root/'manifests'/self.name:
                raise OSError('manifest locked')
            atomic_json(path, value)
        with patch('review.atomic_json', side_effect=fail_manifest):
            saved, warning = save_review_with_receipt(self.root, self.record, self.rows, 'reviewer')
        self.assertIn('manifest locked', warning)
        self.assertEqual(self.case.exported(), 0)
        self.assertEqual(review_revision(saved), review_revision(read_json(self.root/'reviews'/self.name)))
        _, warning = save_review_with_receipt(self.root, self.record, self.rows, 'reviewer', review_revision(saved))
        self.assertEqual(warning, '')
        self.assertEqual(self.case.exported(), 1)

    def test_wrong_number_crop_held_for_new_and_editable_for_legacy(self):
        saved = save_review(self.root, self.record, self.rows, 'reviewer')
        bad = copy.deepcopy(saved)
        bad['spaces'][0]['plate_bbox'] = [38, 180, 67, 202]
        with self.assertRaisesRegex(ValueError, '스티커 밖'):
            save_review(self.root, self.record, bad['spaces'], 'reviewer')
        atomic_json(self.root/'reviews'/self.name, bad)
        record, _, _, previous = load_saved_scene(self.root, self.record['image_id'])
        edits = rows_for_edit(record, previous)
        self.assertEqual([r['review_status'] for r in edits], ['pending','approved'])
        target, _ = export_training(self.root)
        report = read_json(Path(target)/'manifest.json')
        self.assertEqual([r['task'] for r in report['samples']], ['occupancy'])
        self.assertEqual(report['skipped'][0]['space_id'], self.rows[0]['space_id'])
        save_review(self.root, record, self.rows, 'reviewer', review_revision(previous))
        target, _ = export_training(self.root)
        self.assertEqual(sum(r['task']=='number' for r in read_json(Path(target)/'manifest.json')['samples']), 1)

    def test_failed_ui_save_preserves_other_approvals_and_edited_draft(self):
        app = MagicMock()
        app.generation, app.record = 4, self.record
        app.previous = save_review(self.root, self.record, self.rows, 'first')
        app.rows = rows_for_edit(self.record, app.previous)
        app.selected = self.rows[0]['space_id']
        app.rows[0].update(plate_text='42', review_status='pending')
        app.require_review.return_value = app.rows[0]
        Dashboard.persist_review(app, True)
        _, args, token = app.submit.call_args.args
        self.assertEqual(args[2][0]['review_status'], 'approved')
        self.assertEqual(app.rows[0]['review_status'], 'pending')
        Dashboard.accept(app, 'review', token, None, 'disk full')
        self.assertEqual(app.rows[1]['review_status'], 'approved')
        self.assertEqual(app.rows[0]['plate_text'], '42')
        self.assertTrue(app.dirty)

    def test_open_number_set_needs_sticker_to_approve_a_number_crop(self):
        cfg=dict(self.case.cfg, physical_sticker_set=FIELD_SET)
        record,_=persist_scene(self.root,self.case.p,self.case.roi,'new','session',cfg)
        rows=rows_for_edit(record)
        rows[0].update(occupancy='occupied', plate_text='91', plate_bbox=[38,180,67,202], review_status='approved')
        with self.assertRaisesRegex(ValueError, '스티커 영역'):
            save_review(self.root,record,rows,'tester')
        # A number transcribed without a crop can still be kept for review.
        rows[0]['plate_bbox']=None
        saved=save_review(self.root,record,rows,'tester')
        self.assertEqual(saved['spaces'][0]['car_id'],'91')
        target,_=export_training(self.root)
        self.assertFalse(any(r['task']=='number' for r in read_json(Path(target)/'manifest.json')['samples']))

    def test_review_result_for_another_scene_cannot_reset_labels(self):
        app = MagicMock()
        app.generation, app.record = 4, self.record
        app.rows = copy.deepcopy(self.rows)
        Dashboard.accept(app, 'review', (3, self.record['image_id']), None, 'disk full')
        self.assertEqual(app.rows, self.rows)
        app.notice.set.assert_not_called()

    def test_dataset_audit_separates_estimate_from_direct_number(self):
        save_review(self.root, self.record, self.rows, 'reviewer')
        # Supply a previously validated estimate at the audit boundary. The
        # end-to-end replay separately checks actual OCR and prediction files.
        pred = copy.deepcopy(self.case.pred)
        pred['spaces'][0].update(number=None, number_estimate={'number':'23'})
        with patch('dataset_audit.validate_prediction', return_value=pred):
            result = build_audit(self.root)
        self.assertEqual(result['number_estimates_with_truth']['correct'], 1)
        self.assertEqual(result['metrics']['number']['correct'], 0)
        self.assertEqual(result['metrics']['number']['abstained'], 1)


class ExportReport(unittest.TestCase):
    def test_duplicate_report_keys_do_not_hide_a_bad_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,'중복'), ExportTransaction(root) as transfer:
                (root/'report.json').write_text('{"counts":{"cars":99,"cars":1}}','utf8')
                transfer.report('report.json',{'counts':{'cars':1}})
                transfer.finish()

    def test_review_detail_compares_the_estimate_shown_to_the_operator(self):
        app=MagicMock()
        app.reviewing=False;app.scene=None;app.raw=None;app.roi=None
        app.rows=[dict(space_id='A-05',occupancy='occupied',plate_text='77',color='blue',review_status='approved')]
        app.pred={'spaces':[dict(space_id='A-05',occupancy='occupied',number=None,
            number_estimate={'number':'77'},color='blue',occupancy_reason='',issues=[],geometry_status='')]}
        Dashboard.select_slot(app,'A-05')
        self.assertIn('추정 번호 일치', app.detail.set.call_args.args[0])
        self.assertNotIn('불일치', app.detail.set.call_args.args[0])
        app.rows[0]['plate_text']='71'
        Dashboard.select_slot(app,'A-05')
        self.assertIn('추정 번호 불일치', app.detail.set.call_args.args[0])

    def test_numeric_json_types_are_verified_recursively(self):
        for value in (False, 0.0):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                with self.assertRaises(ValueError), ExportTransaction(root) as transfer:
                    atomic_json(root/'report.json', {'counts':[value]})
                    transfer.report('report.json', {'counts':[0]})
                    transfer.finish()
                self.assertEqual(read_json(root/'EXPORT_STATUS.json')['상태'], '실패')

    def test_report_accepts_valid_equivalent_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with ExportTransaction(root) as transfer:
                (root/'report.json').write_text(json.dumps({'b':2,'a':1},indent=4), 'utf8')
                transfer.report('report.json', {'a':1,'b':2})
                transfer.finish()
            self.assertEqual(read_json(root/'EXPORT_STATUS.json')['상태'], '완료')
