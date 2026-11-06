"""Integrity at the transition from two stored scenes to one usable measurement."""
import csv
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from collection_output import export_collection, read_collection
from dataset19 import DatasetIndex, export_training, rebuild_scene_assets
from dual_capture import PairHold, profile_specs, recover_pairs, save_pair, synchronized
from parking_ai.storage import atomic_json
from pipeline import export_csv, read_json
from review import export_reviewed, rows_for_edit, save_review
from test_v34 import data, profile


class PairIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {k: data(k, k, 100 + i*.02) for i, k in enumerate(('A', 'B'))}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        info = save_pair(self.root, self.results, {'A':'ba','B':'bb'}, 'session', 100)
        self.receipt = self.root/'dual_measurements'/(info['id']+'.json')
        self.paths = [self.root/'manifests'/(info['members'][k]['image_id']+'.json') for k in ('A','B')]
        self.a, self.b = map(read_json, self.paths)

    def assert_state(self, complete, held):
        status = recover_pairs(self.root, {})
        self.assertEqual((status['complete'], status['held']), (complete, held))
        self.assertEqual(status['bay_pairs'], {'A':12*complete, 'B':16*complete})
        return status

    def test_missing_receipt_restores_exact_bytes_only_once(self):
        before = self.receipt.read_bytes()
        self.receipt.unlink()
        self.assertEqual(export_collection(self.root,paired_only=True)[1]['source_measurements']['held'],1)
        self.assertEqual(self.assert_state(1, 0)['restored_receipts'], 1)
        self.assertEqual(self.receipt.read_bytes(), before)
        self.assertEqual(self.assert_state(1, 0)['restored_receipts'], 0)

    def test_one_remaining_manifest_cannot_reconstruct_pair(self):
        self.receipt.unlink()
        self.paths[1].unlink()
        self.assert_state(0, 1)
        self.assertFalse(self.receipt.exists())

    def test_conflicting_manifests_do_not_recreate_receipt(self):
        self.receipt.unlink()
        self.b['analysis_config']['dual_measurement']['max_skew_ms'] = 500
        atomic_json(self.paths[1], self.b)
        self.assert_state(0, 1)
        self.assertFalse(self.receipt.exists())

    def test_malformed_receipt_remains_available_for_inspection(self):
        self.receipt.write_bytes(b'{')
        self.assert_state(0, 1)
        self.assertEqual(self.receipt.read_bytes(), b'{')

    def test_receipt_without_either_manifest_is_reported_on_export(self):
        for path in self.paths:
            path.unlink()
        self.assert_state(0, 1)
        report = export_collection(self.root,paired_only=True)[1]
        self.assertEqual(report['source_measurements']['held'], 1)
        self.assertEqual(report['counts']['measurements'], 0)
        self.assertTrue(report['skipped'])

    def test_corrupt_peer_not_used_by_any_export_then_repair(self):
        rows = rows_for_edit(self.a)
        rows[0].update(occupancy='vacant',review_status='approved')
        save_review(self.root,self.a,rows,'tester')
        index = DatasetIndex()
        self.assertEqual(index.scan(self.root,lot='A')['counts']['partial'],0)
        bundle = read_collection(self.root,self.b)
        label = next(i for i in bundle['items'] if i['kind']=='korean_label')
        (self.root/label['path']).write_bytes(b'{}')
        self.assert_state(0, 1)
        self.assertEqual(index.scan(self.root,lot='A')['counts']['partial'],1)
        for kwargs in ({'lot':'A'}, {'paired_only':True}, {'paired_only':True,'selected_only':True}):
            report = export_collection(self.root,**kwargs)[1]
            self.assertEqual(report['counts']['scenes'],0)
        self.assertEqual(export_training(self.root,lot='A')[1],0)
        self.assertEqual(export_reviewed(self.root,lot='A')[1],0)
        with export_csv(self.root).open(encoding='utf-8-sig',newline='') as f:
            self.assertEqual(list(csv.DictReader(f)),[])
        rebuild_scene_assets(self.root,self.b['image_id'],{})
        self.assert_state(1, 0)
        self.assertEqual(index.scan(self.root,lot='A')['counts']['partial'],0)
        self.assertEqual(export_training(self.root,lot='A')[1],1)
        self.assertEqual(export_reviewed(self.root,lot='A')[1],1)
        with export_csv(self.root).open(encoding='utf-8-sig',newline='') as f:
            self.assertEqual(len(list(csv.DictReader(f))),28)

    def test_raw_crop_and_display_image_bytes_checked_on_complete_pair(self):
        assets = read_json(self.root/'assets'/self.paths[1].name)
        bundle = read_collection(self.root,self.b)
        for relative in (self.b['raw_path'],assets['items'][0]['path'],
                         next(i['path'] for i in bundle['items'] if i['kind']=='annotated_space')):
            with self.subTest(path=relative):
                path = self.root/relative
                before = path.read_bytes()
                path.write_bytes(before[:-1]+bytes([before[-1]^1]))
                self.assert_state(0,1)
                path.write_bytes(before)
                self.assert_state(1,0)

    def test_storage_and_quality_counters_are_independent_and_pair_checked_once(self):
        from dual_capture import verified_scene_bundle
        with patch('dual_capture.verified_scene_bundle',wraps=verified_scene_bundle) as verify:
            report = export_collection(self.root,paired_only=True,selected_only=True)[1]
        self.assertEqual(verify.call_count,2)
        self.assertEqual(report['source_measurements'],{'complete':1,'held':0,'bay_pairs':{'A':12,'B':16}})
        self.assertEqual(report['counts']['measurements'],0)

    def test_invalid_manifest_does_not_hide_other_measurements(self):
        (self.root/'manifests'/'broken.json').write_text('null',encoding='utf-8')
        result = self.assert_state(1,0)
        self.assertEqual(result['unidentified_manifests'][0]['file'],'broken.json')

    def test_mutation_of_second_raw_while_copying_first_fails_entire_delivery(self):
        import collection_output
        write = collection_output.atomic_bytes
        def cut(path, payload):
            write(path,payload)
            if path.as_posix().endswith(self.a['raw_path']):
                (self.root/self.b['raw_path']).write_bytes(b'damaged during export')
        with patch.object(collection_output,'atomic_bytes',side_effect=cut), self.assertRaises(ValueError):
            export_collection(self.root,paired_only=True)
        target, = (self.root/'exports').iterdir()
        self.assertEqual(read_json(target/'EXPORT_STATUS.json')['상태'],'실패')
        self.assertFalse((target/'내보내기_결과.json').exists())

    def test_save_does_not_publish_completion_if_written_image_is_unreadable(self):
        from dataset19 import persist_scene
        root = self.root/'another'
        def cut(root, packet, *args, **kwargs):
            record, receipt = persist_scene(root, packet, *args, **kwargs)
            if packet.camera_id == 'B':
                (root/record['raw_path']).write_bytes(b'damaged before final receipt')
            return record, receipt
        with patch('dataset19.persist_scene',side_effect=cut), self.assertRaises(ValueError):
            save_pair(root,self.results,{'A':'ba','B':'bb'},'session',100)
        path, = (root/'dual_measurements').glob('*.json')
        self.assertEqual(read_json(path)['status'],'partial')
        self.assertEqual(recover_pairs(root,{})['held'],1)
        self.assertEqual(export_collection(root,paired_only=True)[1]['counts']['measurements'],0)


class PairSequence(unittest.TestCase):
    def test_reject_earlier_sequence_and_epoch_accept_next_connection(self):
        specs = {s.camera_id:s for s in profile_specs(profile())}
        frames = {k:replace(data(k,k,100).packet,stream_epoch=2,sequence=4) for k in ('A','B')}
        last = {'A':('sim-A',2,5)}
        for epoch, seq in ((2,4),(2,5),(1,30)):
            with self.subTest(epoch=epoch,seq=seq), self.assertRaises(PairHold):
                synchronized({**frames,'A':replace(frames['A'],stream_epoch=epoch,sequence=seq)},specs,100,last,now=100.2)
        for epoch, seq in ((2,6),(3,1)):
            keys,_ = synchronized({**frames,'A':replace(frames['A'],stream_epoch=epoch,sequence=seq)},specs,100,last,now=100.2)
            self.assertEqual(keys['A'],('sim-A',epoch,seq))
