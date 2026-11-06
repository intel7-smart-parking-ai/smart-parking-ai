"""Two-camera save/recovery and data correspondence at the public export boundary."""
import copy
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from collection_output import export_collection
from dual_capture import recover_pairs, save_pair
from parking_ai.storage import atomic_json
from pipeline import read_json
from test_v34 import data
from vision19 import geometry_candidates
from quality import dark_registration_frame


class DualStorage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = {k: data(k, k, 100+i*.01) for i,k in enumerate('AB')}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def save(self):
        return save_pair(self.root, self.frames, {'A':'a','B':'b'}, 'test', 100)

    def exported(self):
        return export_collection(self.root, paired_only=True)[1]['counts']

    def test_both_originals_recover_after_interruption_before_first_derived_photo(self):
        with patch('dataset19.render_collection', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.save()
        self.assertEqual(self.exported()['measurements'], 0)
        before = {p:p.read_bytes() for p in (self.root/'raw').rglob('*.png')}
        self.assertEqual(len(before), 2)
        recovery = recover_pairs(self.root, {})
        self.assertEqual((recovery['complete'], recovery['held']), (1,0))
        self.assertEqual(self.exported()['space_pairs'], 28)
        self.assertEqual(recover_pairs(self.root, {})['restored_receipts'], 0)
        self.assertTrue(all(p.read_bytes()==payload for p,payload in before.items()))

    def test_rehashed_wrong_crop_is_excluded_with_its_peer(self):
        info = self.save()
        path = self.root/'assets'/(info['members']['B']['image_id']+'.json')
        assets = read_json(path)
        item = assets['items'][0]
        target = self.root/item['path']
        pixels = cv2.imdecode(np.frombuffer(target.read_bytes(),np.uint8),cv2.IMREAD_COLOR)
        pixels[0,0,0] ^= 1
        payload = cv2.imencode('.png',pixels)[1].tobytes()
        target.write_bytes(payload)
        item['sha256'] = hashlib.sha256(payload).hexdigest()
        atomic_json(path,assets)
        self.assertEqual(self.exported()['scenes'], 0)
        self.assertIn('픽셀 불일치', recover_pairs(self.root,{})['errors'][0])

    def test_wrong_korean_crop_coordinates_even_in_matching_aggregate_are_excluded(self):
        info = self.save()
        bundle_path = self.root/'bundles'/(info['members']['A']['image_id']+'.json')
        bundle = read_json(bundle_path)
        bay = next(i for i in bundle['items'] if i['kind']=='korean_label')
        scene = next(i for i in bundle['items'] if i['kind']=='scene_label')
        for item in (bay,scene):
            path = self.root/item['path']
            value = read_json(path)
            label = value if item is bay else next(r for r in value['주차칸별결과'] if r['주차칸명']==bay['space_id'])
            label['칸크롭_원본시작점_px'][0] += 1
            atomic_json(path,value)
            item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(bundle_path,bundle)
        self.assertEqual(self.exported()['measurements'], 0)
        self.assertIn('칸크롭_원본시작점_px', recover_pairs(self.root,{})['errors'][0])

    def test_wrong_crop_metadata_with_valid_pixels_holds_the_pair(self):
        info = self.save()
        path = self.root/'assets'/(info['members']['B']['image_id']+'.json')
        assets = read_json(path)
        assets['items'][0]['origin'][0] += 3
        atomic_json(path,assets)
        self.assertEqual(self.exported()['measurements'], 0)


class ChangeGeometry(unittest.TestCase):
    def test_overlap_shared_edges_and_exterior_match_full_frame_raster_counts(self):
        rng = np.random.default_rng(40)
        samples = [np.zeros((122,183),np.uint8) for _ in range(24)]
        spaces = [{'space_id':str(i),'polygon':[[x,12],[x+34,18],[x+15,90],[x-19,83]]}
                  for i,x in enumerate((-4,31,61,97,180,250))]
        originals = copy.deepcopy(spaces)
        for mask in samples:
            for x,y in rng.integers([0,0],[182,121],size=(5,2)):
                cv2.circle(mask,(int(x),int(y)),int(rng.integers(4,22)),255,-1)
            rasters = {}
            for s in spaces:
                raster = np.zeros_like(mask)
                cv2.fillPoly(raster,[np.int32(s['polygon'])],255)
                rasters[s['space_id']] = raster>0
            union = np.logical_or.reduce(list(rasters.values()))
            expected = []
            for c in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
                if cv2.contourArea(c)<max(80,mask.size*.0003):
                    continue
                pixels = np.zeros_like(mask)
                cv2.drawContours(pixels,[c],-1,255,-1)
                changed = pixels>0
                touched = [k for k,r in rasters.items() if (changed & r).sum()>max(30,changed.sum()*.12)]
                reason = '여러 칸 걸침 변화 후보' if len(touched)>1 else '칸 경계/외부 변화 후보' if (changed & ~union).sum()>changed.sum()*.2 else None
                if reason:
                    x,y,w,h = cv2.boundingRect(c)
                    expected.append((reason,touched,[x,y,x+w,y+h]))
            found,by_slot = geometry_candidates(mask,spaces)
            self.assertEqual([(r['reason'],r['spaces'],r['bbox']) for r in found],expected)
            self.assertEqual(by_slot,{k:[r for r,ids,_ in expected if k in ids] for k in rasters if any(k in ids for _,ids,_ in expected)})
        self.assertEqual(spaces, originals)


class DarkCamera(unittest.TestCase):
    def test_nearly_black_pair_skips_ocr_saves_exact_diagnostic_and_no_training(self):
        from dual_capture import ChannelAnalyzer, RegistrationHold
        from dual_dashboard import has_registration_view
        from pair_diagnostics import save_held_pair
        from test_v34 import profile
        cfg = profile()
        frames, outcomes = {}, {}
        with tempfile.TemporaryDirectory() as tmp, patch('automatic_setup.prepare_auto_scene') as register:
            root = Path(tmp)
            for k in 'AB':
                row = data(k,k,100)
                im = np.full_like(row.packet.image,3)
                im[0,0] = 255
                frames[k] = replace(row.packet,image=im)
                with self.assertRaisesRegex(RegistrationHold,'조명·렌즈·노출') as raised:
                    ChannelAnalyzer().analyze(root,frames[k],cfg['channels'][k],row.cfg)
                outcomes[k] = raised.exception
            register.assert_not_called()
            self.assertTrue(has_registration_view(outcomes))
            folder = save_held_pair(root,frames,outcomes,cfg,'dark')
            members = read_json(folder/'진단.json')['카메라']
            for k,row in members.items():
                self.assertEqual(row['위치등록칸수'],0)
                self.assertEqual(row['저조도측정'],dark_registration_frame(frames[k].image))
                self.assertEqual(row['원본_SHA256'],hashlib.sha256((folder/row['원본사진']).read_bytes()).hexdigest())
            self.assertEqual(export_collection(root,paired_only=True,selected_only=True)[1]['counts']['images'],0)

    def test_low_average_alone_does_not_skip_a_visible_region(self):
        im = np.zeros((100,100,3),np.uint8)
        im[:2] = 100
        self.assertIsNone(dark_registration_frame(im))
        self.assertIsNone(dark_registration_frame(np.full_like(im,24)))


class PrintedNameBand(unittest.TestCase):
    def read(self, turns, tail):
        from bay_name_retry import localized_read
        from test_v38 import hit
        image=np.full((120,160,3),25,np.uint8)
        image[40:78,53:104]=170
        reader=Mock()
        answers=iter([('B-06',.42),('B-06',.67),('B-06',.58)]+tail)
        reader.readtext.side_effect=lambda pixels,**kwargs:[hit(pixels,*next(answers))]
        return localized_read(image,reader,'B-06',np.array([80,60]),np.array([46,25]),turns,.70)

    def test_both_center_band_reads_have_original_coordinates_in_all_four_rotations(self):
        from bay_name_retry import validate_local_reads
        for turns in range(4):
            row=self.read(turns,[('B-06',.93),('B-06',.84)])
            validate_local_reads(row,[160,120],.70)
            self.assertEqual([r['mode'] for r in row['local_reads']],['band_inverse','band_toned'])
            for evidence in row['local_reads']:
                box=evidence['bbox']
                np.testing.assert_allclose((np.array(box[:2])+box[2:])/2,[80,60],atol=2)

    def test_weak_trigger_cannot_supply_missing_second_confirmation_or_override_conflict(self):
        self.assertIsNone(self.read(0,[('B-06',.93),('B-06',.69)]))
        self.assertIsNone(self.read(0,[('B-06',.93),('B-05',.94)]))
