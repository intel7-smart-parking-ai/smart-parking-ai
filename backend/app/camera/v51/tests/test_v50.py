from dataclasses import replace
from pathlib import Path
import hashlib
import io
import tempfile
import unittest
from unittest.mock import Mock, patch

from parking_ai.storage import atomic_json
from pipeline import read_json
import test_v34
import test_v45
import test_v48


class FrameEvidence(unittest.TestCase):
    meta = dict(frame_number=27,timestamp_ms=23.25,timestamp_domain='hardware_clock',exposure_us=900)

    def test_rgb_sdk_values_are_read_from_one_frame(self):
        from camera_hub import rgb_frame_metadata
        f=Mock()
        f.get_timestamp.return_value=23.25
        f.get_frame_timestamp_domain.return_value='timestamp_domain.hardware_clock'
        f.get_frame_number.return_value=27
        f.get_frame_metadata.return_value=900
        self.assertEqual(rgb_frame_metadata(f,Mock()),self.meta)
        f.supports_frame_metadata.return_value=False
        self.assertNotIn('exposure_us',rgb_frame_metadata(f,Mock()))
        f.get_timestamp.side_effect=RuntimeError()
        self.assertIsNone(rgb_frame_metadata(f,Mock()))

    def test_import_invalid_metadata_and_legacy(self):
        from records import validate_sensor_frame
        validate_sensor_frame(None,'import')
        for kind,value in [('import',self.meta),('camera',dict(self.meta,timestamp_ms=float('nan'))),
                           ('camera',dict(self.meta,frame_number=True)),('camera',dict(self.meta,exposure_us=0)),
                           ('camera',dict(self.meta,timestamp_domain='unknown'))]:
            with self.subTest(kind=kind,value=value),self.assertRaises(ValueError):
                validate_sensor_frame(value,kind)

    def test_device_clocks_do_not_change_pair_selection(self):
        from dual_capture import PairHold,synchronized
        case=test_v34.PairTiming()
        case.setUp()
        frames={k:replace(p,sensor_frame=dict(self.meta,timestamp_ms=i*200000.))
                for i,(k,p) in enumerate(case.frames.items())}
        self.assertEqual(synchronized(frames,case.specs,100,now=100.2)[1],100.)
        frames['B']=replace(frames['B'],sensor_frame=frames['A'].sensor_frame,received_monotonic=100.100001)
        with self.assertRaises(PairHold):synchronized(frames,case.specs,100,now=100.2)

    def test_saved_pair_roundtrip_and_mismatched_evidence(self):
        from dual_capture import pair_path,save_pair,read_complete_measurement
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            results={k:test_v34.data(k,k,35+i*.01) for i,k in enumerate('AB')}
            for result in results.values():
                result.packet=replace(result.packet,sensor_frame=dict(self.meta))
            info=save_pair(root,results,dict(A='a',B='b'),'qa',100)
            members=read_complete_measurement(root,info['id'])
            self.assertEqual(members['A'][0]['sensor_frame'],self.meta)
            path=pair_path(root,info['id'])
            receipt=read_json(path)
            receipt['info']['members']['A']['sensor_frame']['frame_number']+=1
            atomic_json(path,receipt)
            with self.assertRaises(ValueError):read_complete_measurement(root,info['id'])


class ConflictingDigits(unittest.TestCase):
    def test_disagreeing_detector_views_keep_color_and_hold_number(self):
        from round_sticker import identify
        f=test_v45
        disc=dict(radius=20,polygon=f.POLY,color='white')
        shape=dict(score=.95,margin=.08,number='34',alternate='14',box=[35,35,65,60],views=[])
        candidates=[dict(number_candidate=v,status='candidate',score=.95,selected_in_view=True)
                    for v in ('34','14')]
        with patch('round_sticker.proposals',return_value=[disc]),patch('print_estimator.examine',return_value=shape),patch('sticker_reference.compare',return_value=None),patch('sticker_ocr.read_sticker',return_value={'status':'conflicting_views','selected':None,'candidates':candidates}):
            found,result=identify(f.IMAGE,{'polygon':f.POLY},Mock(),f.CFG)
        self.assertEqual((found['color'],result['status']),('white','sticker_number_unread'))
        self.assertFalse(result.get('number_candidate') or result.get('estimate'))
        self.assertEqual(result['candidates'],candidates)


class WholeDelivery(unittest.TestCase):
    def setUp(self):
        self.fixture=test_v48.DurableEvidence()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.folder=self.fixture.delivery()

    def test_all_three_tasks_are_opened(self):
        from field_check import inspect_training_folder
        manifest,files=inspect_training_folder(self.folder)
        self.assertEqual({s['task'] for s in manifest['samples']},{'number','color','occupancy'})
        self.assertEqual(len(files),len(manifest['samples'])+1)

    def test_corrupt_color_blocks_number_verification(self):
        crop=next((self.folder/'color').rglob('*.png'))
        crop.write_bytes(crop.read_bytes()[:-1])
        with self.assertRaises(ValueError):self.fixture.compare(self.folder)

    def test_missing_occupancy_blocks_number_verification(self):
        next((self.folder/'occupancy').rglob('*.png')).unlink()
        with self.assertRaises(ValueError):self.fixture.compare(self.folder)

    def test_unlisted_file_blocks_number_verification(self):
        (self.folder/'extra.png').write_bytes(b'bad')
        with self.assertRaises(ValueError):self.fixture.compare(self.folder)

    def test_matching_receipt_cannot_hide_a_non_png_color_file(self):
        from export_integrity import ExportTransaction
        manifest=read_json(self.folder/'manifest.json')
        row=next(r for r in manifest['samples'] if r['task']=='color')
        crop=self.folder/row['path']
        crop.write_bytes(b'invalid RGB PNG')
        row['sha256']=hashlib.sha256(crop.read_bytes()).hexdigest()
        self.reseal(manifest,ExportTransaction)
        with self.assertRaisesRegex(ValueError,'PNG'):self.fixture.compare(self.folder)

    def reseal(self,manifest,transaction):
        with transaction(self.folder) as export:
            for row in manifest['samples']:
                export.verify(row['path'],row['sha256'])
            atomic_json(self.folder/'manifest.json',manifest)
            export.report('manifest.json',manifest)
            export.finish()

    def test_reencoded_equal_pixels_with_conflicting_label_are_rejected(self):
        from PIL import Image
        from export_integrity import ExportTransaction
        manifest=read_json(self.folder/'manifest.json')
        row=next(r for r in manifest['samples'] if r['task']=='color')
        label='blue' if row['label']!='blue' else 'white'
        clone=dict(row,image_id='another_image',label=label)
        split=clone['split']
        clone['path']=f"color/{split}/{label}/another_image_{row['space_id']}.png"
        target=self.folder/clone['path']
        target.parent.mkdir(parents=True,exist_ok=True)
        with Image.open(self.folder/row['path']) as im:
            buffer=io.BytesIO()
            im.save(buffer,'PNG',compress_level=0)
        target.write_bytes(buffer.getvalue())
        clone['sha256']=hashlib.sha256(buffer.getvalue()).hexdigest()
        manifest['samples'].append(clone)
        counts=dict(train=0,validation=0,test=0)
        counts[split]=1
        manifest['과제별_라벨_분할수']['color'][label]=counts
        self.reseal(manifest,ExportTransaction)
        with self.assertRaisesRegex(ValueError,'충돌'):self.fixture.compare(self.folder)
