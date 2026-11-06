"""Semantic sidecar integrity, truthful copy counts and safe marker enrollment."""
import copy,hashlib,json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import cv2
from test_v19 import fixture
from test_v27 import full_fixture
from test_v31 import board
from dataset19 import persist_scene,DatasetIndex,rebuild_scene_assets,export_training
from collection_output import read_collection,export_collection,render_collection
from review import rows_for_edit,save_review
from pipeline import read_json
from parking_ai.storage import atomic_json
from marker_setup import enroll,template_path,load_template,locate,validate_template,digest,outside_bays


def rewrite_labels(root,record,mutate):
    """Model internally consistent sidecars from a bad writer, not random bit rot."""
    bundlepath=root/'bundles'/(record['image_id']+'.json');bundle=read_json(bundlepath)
    for item in bundle['items']:
        if item['kind'] not in ('korean_label','scene_label'):continue
        path=root/item['path'];label=read_json(path)
        for row in label['주차칸별결과'] if item['kind']=='scene_label' else [label]:mutate(row)
        atomic_json(path,label);item['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    atomic_json(bundlepath,bundle)


class SidecarEvidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.p,self.roi=fixture()
        self.record,_=persist_scene(self.root,self.p,self.roi,'batch','session',self.cfg)
    def tearDown(self):self.temp.cleanup()
    def invalid(self,key,value):
        rewrite_labels(self.root,self.record,lambda r:r.update({key:value}))
        with self.assertRaisesRegex(ValueError,key):read_collection(self.root,self.record)
    def test_numeric_number_cannot_disagree_with_unread_text(self):self.invalid('차량번호',23)
    def test_candidate_cannot_claim_human_approval(self):self.invalid('사람검수완료',True)
    def test_unknown_scene_cannot_claim_information_complete(self):self.invalid('자동정보완성',True)
    def test_vehicle_type_cannot_be_invented(self):self.invalid('차량종류','전기차')
    def test_ocr_status_cannot_claim_human_confirmation(self):self.invalid('번호판독상태','사람 확인')
    def test_numeric_confidence_must_match_prediction(self):self.invalid('점유측정값',.99)
    def test_geometry_warning_must_match_prediction(self):self.invalid('위치검사','정상 주차')
    def test_boolean_is_not_interchangeable_with_integer(self):self.invalid('사람검수완료',0)
    def test_missing_information_complete_field_is_rejected(self):
        rewrite_labels(self.root,self.record,lambda r:r.pop('자동정보완성'))
        with self.assertRaisesRegex(ValueError,'자동정보완성'):read_collection(self.root,self.record)
    def test_approved_integer_number_does_not_accept_float(self):
        rows=rows_for_edit(self.record);rows[0].update(occupancy='occupied',plate_text='23',color='green',review_status='approved',
            plate_bbox=[80,80,110,120],sticker_polygon=[[65,65],[125,65],[125,135],[65,135]])
        save_review(self.root,self.record,rows,'tester')
        self.assertEqual(read_collection(self.root,self.record)['status'],'complete')
        rewrite_labels(self.root,self.record,lambda r:r.update(차량번호=23.0) if r['차량번호']==23 else None)
        with self.assertRaisesRegex(ValueError,'차량번호'):read_collection(self.root,self.record)
    def test_ui_counts_do_not_trust_false_completion(self):
        rewrite_labels(self.root,self.record,lambda r:r.update(자동정보완성=True))
        index=DatasetIndex().scan(self.root,force=True);c=index['counts']
        self.assertEqual(c['partial'],1);self.assertEqual(c['information_complete'],0);self.assertEqual(c['collection_pairs'],0)
        self.assertEqual(c['quality_passed'],0);self.assertTrue(any('자동정보완성' in e for e in index['errors']))
    def test_bad_sidecar_skips_only_its_scene_in_export(self):
        rewrite_labels(self.root,self.record,lambda r:r.update(차량번호=23))
        q=replace(self.p,sequence=2,timestamp_utc='2026-11-04T09:00:02+00:00')
        good,_=persist_scene(self.root,q,self.roi,'batch','session',self.cfg)
        target,report=export_collection(self.root)
        self.assertEqual(report['counts']['scenes'],1);self.assertEqual(report['scenes'][0]['image_id'],good['image_id'])
        self.assertEqual(len(report['skipped']),1);self.assertEqual(read_json(Path(target)/'EXPORT_STATUS.json')['상태'],'완료')
    def test_repair_restores_values_without_touching_original(self):
        raw=self.root/self.record['raw_path'];before=raw.read_bytes()
        rewrite_labels(self.root,self.record,lambda r:r.update(차량번호=23,사람검수완료=True,자동정보완성=True))
        result,receipt=rebuild_scene_assets(self.root,self.record['image_id'],self.cfg)
        self.assertEqual(receipt['status'],'complete');self.assertEqual(raw.read_bytes(),before)
        bundle=read_collection(self.root,result);item=next(i for i in bundle['items'] if i['kind']=='korean_label')
        label=read_json(self.root/item['path']);self.assertIsNone(label['차량번호']);self.assertFalse(label['사람검수완료']);self.assertFalse(label['자동정보완성'])
    def test_legacy_bundle_without_selection_still_checks_source_values(self):
        path=self.root/'bundles'/(self.record['image_id']+'.json');value=read_json(path)
        value.pop('quality_selection_sha256');value.pop('quality_selection_status');atomic_json(path,value)
        self.assertEqual(read_collection(self.root,self.record)['status'],'complete')
        rewrite_labels(self.root,self.record,lambda r:r.update(차량번호=23))
        with self.assertRaises(ValueError):read_collection(self.root,self.record)
    def test_valid_legacy_optional_advisory_omission_remains_readable(self):
        rewrite_labels(self.root,self.record,lambda r:r.pop('점유판단근거',None))
        self.assertEqual(read_collection(self.root,self.record)['status'],'complete')
    def test_reviewed_scene_derives_values_from_current_human_truth(self):
        rows=rows_for_edit(self.record)
        for r in rows:r.update(occupancy='vacant',review_status='approved')
        save_review(self.root,self.record,rows,'tester')
        self.assertEqual(read_collection(self.root,self.record)['status'],'complete')
        self.assertEqual(export_collection(self.root)[1]['counts']['scenes'],1)
    def test_selected_training_and_photos_reject_false_approval_sidecar(self):
        cfg,p,roi=full_fixture();record,_=persist_scene(self.root,p,roi,'other','session',cfg)
        rows=rows_for_edit(record)
        for r in rows:r.update(occupancy='vacant',review_status='approved')
        save_review(self.root,record,rows,'tester');self.assertEqual(DatasetIndex().scan(self.root)['counts']['quality_passed'],1)
        rewrite_labels(self.root,record,lambda r:r.update(사람검수완료=False))
        self.assertEqual(export_collection(self.root,selected_only=True)[1]['counts']['scenes'],0)
        self.assertEqual(export_training(self.root,selected_only=True)[1],0)


class ActualCopyCounts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.p,self.roi=fixture()
    def tearDown(self):self.temp.cleanup()
    def scenes(self,shared):
        r1,_=persist_scene(self.root,self.p,self.roi,'b','s',self.cfg)
        p2=replace(self.p,sequence=2,timestamp_utc='2026-11-04T09:00:02+00:00');r2,_=persist_scene(self.root,p2,self.roi,'b','s',self.cfg)
        if shared:
            r2['raw_path']=r1['raw_path'];atomic_json(self.root/'manifests'/(r2['image_id']+'.json'),r2)
            render_collection(self.root,r2,p2.image)
        return r1,r2
    def verify_counts(self,target,report):
        root=Path(target);c=report['counts'];items=read_json(root/'EXPORT_STATUS.json')['파일목록']
        data=[r['파일'] for r in items if r['파일']!='내보내기_결과.json']
        self.assertEqual(c['images'],sum(p.endswith('.png') for p in data))
        self.assertEqual(c['json_files'],sum(p.endswith('.json') for p in data))
        self.assertEqual(c['originals'],len(list((root/'raw').rglob('*.png'))))
        self.assertEqual(c['scenes'],2);self.assertEqual(c['space_pairs'],4)
    def test_shared_original_counted_once(self):
        self.scenes(True);target,report=export_collection(self.root);self.verify_counts(target,report)
        self.assertEqual(report['counts']['originals'],1)
    def test_distinct_original_paths_with_same_pixels_counted_twice(self):
        self.scenes(False);target,report=export_collection(self.root);self.verify_counts(target,report)
        self.assertEqual(report['counts']['originals'],2)
    def test_shared_original_export_keeps_both_scene_references(self):
        a,b=self.scenes(True);target,report=export_collection(self.root)
        self.assertEqual({s['image_id'] for s in report['scenes']},{a['image_id'],b['image_id']})
        for s in report['scenes']:self.assertTrue((Path(target)/s['raw_path']).is_file())
    def test_storage_counter_separates_scenes_from_shared_original_files(self):
        self.scenes(True);counts=DatasetIndex().scan(self.root)['counts']
        self.assertEqual(counts['capture_scenes'],2);self.assertEqual(counts['originals'],1)
    def test_storage_cached_scan_preserves_unique_counts(self):
        self.scenes(True);index=DatasetIndex();first=index.scan(self.root)['counts'];second=index.scan(self.root)['counts']
        self.assertEqual(first,second);self.assertEqual(second['originals'],1)
    def test_storage_image_bytes_exclude_repeated_file_references(self):
        a,b=self.scenes(True);paths=set()
        for r in (a,b):
            paths.add(self.root/r['raw_path'])
            for i in read_json(self.root/'assets'/(r['image_id']+'.json'))['items']:paths.add(self.root/i['path'])
            for i in read_json(self.root/'bundles'/(r['image_id']+'.json'))['items']:
                if i['kind'] in ('annotated_scene','annotated_space'):paths.add(self.root/i['path'])
        self.assertEqual(DatasetIndex().scan(self.root)['counts']['bytes'],sum(p.stat().st_size for p in paths))
    def test_storage_distinct_files_are_not_deduplicated_by_image_content(self):
        self.scenes(False);counts=DatasetIndex().scan(self.root)['counts']
        self.assertEqual(counts['capture_scenes'],2);self.assertEqual(counts['originals'],2)


class MarkerPublication(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg,self.p,self.roi=board()
    def tearDown(self):self.temp.cleanup()
    def intruding(self):
        image=self.p.image.copy();marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),4,80)
        image[280:360,250:330]=cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR);return replace(self.p,image=image)
    def test_inside_marker_rejected_before_first_template_write(self):
        with self.assertRaisesRegex(ValueError,'주차칸 A-01 내부'):enroll(self.root,self.intruding(),self.roi)
        self.assertFalse(template_path(self.root,'A').exists());self.assertFalse((self.root/'marker_boards/references').exists())
    def test_inside_marker_cannot_replace_existing_good_reference(self):
        enroll(self.root,self.p,self.roi);path=template_path(self.root,'A');before=path.read_bytes()
        with self.assertRaises(ValueError):enroll(self.root,self.intruding(),self.roi)
        self.assertEqual(path.read_bytes(),before)
    def test_failed_final_geometry_check_does_not_publish_first_template(self):
        with patch('marker_setup.locate',side_effect=ValueError('final geometry failure')):
            with self.assertRaises(ValueError):enroll(self.root,self.p,self.roi)
        self.assertFalse(template_path(self.root,'A').exists());self.assertFalse((self.root/'marker_boards/references').exists())
    def test_failed_final_geometry_check_keeps_old_reference(self):
        enroll(self.root,self.p,self.roi);path=template_path(self.root,'A');before=path.read_bytes()
        refs={p.name for p in (self.root/'marker_boards/references').iterdir()}
        with patch('marker_setup.locate',side_effect=ValueError('final geometry failure')):
            with self.assertRaises(ValueError):enroll(self.root,replace(self.p,timestamp_utc='2026-11-05T12:00:00+00:00'),self.roi)
        self.assertEqual(path.read_bytes(),before);self.assertEqual({p.name for p in (self.root/'marker_boards/references').iterdir()},refs)
    def test_failed_reference_image_write_keeps_old_active_json(self):
        enroll(self.root,self.p,self.roi);path=template_path(self.root,'A');before=path.read_bytes()
        with patch('collection_output.atomic_bytes',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):enroll(self.root,self.p,self.roi)
        self.assertEqual(path.read_bytes(),before)
    def test_saved_template_with_inside_marker_is_held(self):
        enroll(self.root,self.p,self.roi);t=load_template(self.root,'A')
        t['markers'].append({'id':4,'corners':[[250.,280.],[329.,280.],[329.,359.],[250.,359.]]})
        t['sha256']=digest({k:v for k,v in t.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'내부'):validate_template(t)
    def test_tangent_marker_does_not_count_as_inside_bay(self):
        outside_bays([{'id':0,'corners':[[0,20],[20,20],[20,40],[0,40]]}],
                     [{'space_id':'A-01','polygon':[[20,20],[40,20],[40,40],[20,40]]}])
    def test_valid_registration_and_tracking_still_succeeds(self):
        result=enroll(self.root,self.p,self.roi);self.assertTrue(result['ready'])
        self.assertEqual(locate(load_template(self.root,'A'),self.p,self.roi['pose'])['marker_count'],4)
