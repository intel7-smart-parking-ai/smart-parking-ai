import copy
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock,patch

import cv2
import numpy as np

from board_profile import apply_lighting,match_aisle_light,matches_registered_bays
from camera_hub import CameraHub
from dataset19 import export_training
from dual_capture import PairHold,continuous_pair,save_pair,synchronized
from empty_pair import numbered_discs
from field_check import compare_layout,field_check,form_for
from parking_ai.storage import atomic_json
from pipeline import read_json
from review import save_review
from round_sticker import identify,recover_exposure
from test_v19 import fixture
import test_v22
from test_v34 import data,profile
from dual_capture import profile_specs
from vision19 import geometry_candidates


class CameraEvidence(unittest.TestCase):
    def test_virtual_rescan_keeps_independent_color_when_66_and_99_are_ambiguous(self):
        from round_sticker import identify_guide
        _,p,roi=fixture();s=roi['spaces'][0];d={'color':'green','center':[70,120],'radius':20}
        image=p.image.copy();cv2.circle(image,(70,120),20,(15,150,15),-1)
        with patch('round_sticker.identify',side_effect=[(d,{'number_candidate':'66','status':'candidate'}),(d,{'estimate':{'number':'99'},'status':'estimated'})]):
            found,r=identify_guide(image,s,None,{})
        self.assertIs(found,d);self.assertEqual(r['status'],'sticker_number_unread')

    def test_virtual_rescan_needs_the_same_disc_and_number(self):
        from round_sticker import identify_guide
        _,p,roi=fixture();s=roi['spaces'][0];d={'color':'blue','center':[70,120],'radius':18}
        image=p.image.copy();cv2.circle(image,(70,120),20,(110,10,15),-1)
        initial=(None,{'status':'round_sticker_unread','guide_ink':True})
        for x,number,expected in [(70,'31','31'),(104,'31',None),(70,'13',None)]:
            with patch('round_sticker.identify',side_effect=[initial,(d,{'number_candidate':'31'}),({**d,'center':[x,120]},{'number_candidate':number})]):
                _,r=identify_guide(image,s,None,{})
                self.assertEqual(r.get('number_candidate'),expected)

    def test_photo_guides_are_reproducible_and_never_confirm_a_physical_bay(self):
        from sticker_guide import guide_spaces,virtual_roi
        from layouts import validate_snapshot,validate_spaces
        from dual_capture import label_fields
        _,packet,_=fixture()
        for lot,n in [('A',12),('B',16)]:
            for size in ((640,480),(1920,1080),(1080,1920)):
                slots=guide_spaces(lot,*size)
                self.assertEqual(len(validate_spaces(lot,slots,size)),n)
            roi=virtual_roi(packet,lot,'test')
            validate_snapshot(roi)
            self.assertFalse(label_fields({'analysis_config':{},'roi':roi})['실물칸확인'])
            roi['spaces'][0]['polygon'][0][0]+=4
            with self.assertRaisesRegex(ValueError,'가상 안내선'):validate_snapshot(roi)

    def test_missing_markers_fall_back_to_current_board_or_guide(self):
        from sticker_guide import physical_or_virtual,is_guide
        _,p,_=fixture()
        with patch('marker_setup.load_template',return_value={1:1}),patch('marker_setup.locate',side_effect=ValueError('가림')),patch('board_profile.locate',side_effect=[None,{'roi':{'registration_method':'fixed_printed_board_v1'}}]):
            self.assertTrue(is_guide(physical_or_virtual('.',p,'A','fixed')))
            self.assertFalse(is_guide(physical_or_virtual('.',p,'A','fixed')))

    def test_reversed_66_99_keeps_sticker_color_and_holds_number(self):
        _,packet,roi=fixture();slot=roi['spaces'][0]
        disc={'radius':15,'polygon':slot['polygon'],'color':'white'}
        shape={'number':'99','alternate':'66','score':.95,'margin':.008,'box':[10,10,20,20]}
        reader=Mock(spec=['readtext'])
        with patch('round_sticker.proposals',return_value=[disc]),patch('print_estimator.examine',return_value=shape), \
                patch('sticker_ocr.overlap_fraction',return_value=.99),patch('sticker_ocr.read_sticker',return_value={'selected':{'number_candidate':'66'}}):
            found,result=identify(packet.image,slot,reader,{})
        self.assertEqual(found['color'],'white')
        self.assertEqual(result['status'],'sticker_number_unread')
        self.assertNotIn('estimate',result)

    def test_per_bay_training_requires_quality_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'품질 선별'):
                export_training(Path(tmp),per_bay=True)
            self.assertFalse((Path(tmp)/'exports').exists())

    def test_empty_aisle_compensates_manual_bays_without_sampling_sticker_or_car(self):
        slots=[{'polygon':q} for q in ([[10,10],[90,10],[90,52],[10,52]],
                                      [[10,140],[90,140],[90,185],[10,185]])]
        values=np.tile(np.arange(170,dtype=np.uint8)+35,(200,1))
        base=cv2.cvtColor(values,cv2.COLOR_GRAY2BGR)
        dark=np.rint(base*.75+2).astype(np.uint8)
        dark[15:50,12:83]=(38,192,40)
        before=dark.copy();corrected,tone=match_aisle_light(dark,{'spaces':slots},base)
        self.assertEqual(tone['source'],'empty_aisle')
        self.assertTrue(np.array_equal(dark,before))
        self.assertTrue(np.array_equal(corrected,apply_lighting(dark,tone)))
        self.assertLess(np.abs(corrected[60:130,25:80].astype(float)-base[60:130,25:80]).mean(),2)

    def test_recent_synchronized_pair_preserves_epoch_and_never_reuses_frames(self):
        specs={s.camera_id:s for s in profile_specs(profile())}
        hub=CameraHub(list(specs.values()))
        _,p,_=fixture()
        def frame(k,i,t):return replace(p,camera_id=k,serial=specs[k].serial,sequence=i,received_monotonic=t)
        a,b=frame('A',15,42.32),frame('B',10,42.15)
        current={'A':a,'B':b}
        peer=hub._states['B'];peer.status='RUNNING';peer.stream_epoch=b.stream_epoch;peer.recent.append(b)
        state=hub._states['A'];state.status='RUNNING';state.stream_epoch=a.stream_epoch
        for i,t in enumerate((42.14,42.20,42.26,42.32),12):state.recent.append(frame('A',i,t))
        with self.assertRaises(PairHold):synchronized(current,specs,30,now=42.4)
        pair,keys,delta=continuous_pair(hub,current,specs,30,{},42.1,now=42.4)
        self.assertEqual((pair['A'].sequence,delta),(12,10))
        self.assertFalse(np.shares_memory(pair['A'].image,state.recent[0].image))
        with self.assertRaises(PairHold):continuous_pair(hub,current,specs,30,keys,42.1,now=42.4)
        with self.assertRaises(PairHold):continuous_pair(hub,current,specs,30,{},42.19,now=42.4)
        state.stream_epoch+=1
        with self.assertRaises(PairHold):continuous_pair(hub,current,specs,30,{},42.1,now=42.4)

    def test_fixed_bays_must_all_agree_including_last_corner(self):
        _,p,roi=fixture();roi['registration_method']='fixed_printed_board_v1'
        proposed=copy.deepcopy(roi)
        with patch('board_profile.locate',return_value={'roi':proposed}):
            self.assertTrue(matches_registered_bays(p,roi))
            proposed['spaces'][-1]['polygon'][-1][0]+=12
            self.assertFalse(matches_registered_bays(p,roi))
        roi.pop('registration_method')
        self.assertFalse(matches_registered_bays(p,roi))

    def test_clear_disc_with_unread_digits_holds_empty_reference(self):
        _,p,roi=fixture()
        with patch('round_sticker.identify',side_effect=[({'color':'green'},{'status':'sticker_number_unread'}),(None,{'status':'round_sticker_unread'})]):
            self.assertEqual(numbered_discs(p.image,roi),[roi['spaces'][0]['space_id']])

    def test_rescue_requires_same_number_color_and_disc(self):
        _,p,roi=fixture();slot=roi['spaces'][0]
        disc={'color':'green','center':[60,90],'radius':12}
        a={'estimate':{'number':'41'}}
        for d,num,accepted in [(disc,'41',True),(disc,'14',False),({**disc,'color':'blue'},'41',False),
                               ({**disc,'center':[80,100]},'41',False)]:
            with self.subTest(num=num,d=d),patch('round_sticker.identify',side_effect=[(disc,copy.deepcopy(a)),(d,{'estimate':{'number':num}})]):
                match=recover_exposure(p.image,p.image-15,slot,{},None)
                self.assertEqual(bool(match),accepted)
                if match:self.assertEqual(match[1]['estimate']['exposure_agreement'],'raw_and_partial_correction')

    def test_slanted_painted_line_and_open_entrance_are_distinguished(self):
        q=[[45,30],[125,30],[95,125],[15,125]]
        slot={'space_id':'A-01','polygon':q,'painted_edges':[[q[3],q[0]],[q[0],q[1]],[q[1],q[2]]],'entrance_edge':[q[2],q[3]]}
        for a,b,reason in [((50,56),(85,93),None),((38,75),(72,98),None),
                           ((52,15),(83,56),'주차선 침범 변화 후보'),((36,99),(68,146),'주차칸 입구 돌출 변화 후보'),
                           ((145,30),(173,90),None)]:
            mask=np.zeros((160,190),np.uint8);cv2.rectangle(mask,a,b,255,-1)
            warnings,_=geometry_candidates(mask,[slot])
            self.assertEqual([r['reason'] for r in warnings],[reason] if reason else [])


class FieldWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames={k:data(k,k,100+.01*i) for i,k in enumerate('AB')}
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        info=save_pair(cls.root,cls.frames,dict(A='a',B='b'),'qa',100)
        cls.receipt=cls.root/'dual_measurements'/(info['id']+'.json')
        cls.form_path,_=field_check(cls.root,cls.receipt)
        cls.template=read_json(cls.form_path)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def filled(self):
        form=copy.deepcopy(self.template);form['조명조건']='기본'
        for r in form['주차칸']:r['점유']='빈칸'
        form['주차칸'][0].update(점유='차량',번호=91,스티커색='초록',주차선='정상')
        return form

    def test_blank_template_has_no_machine_labels_and_incomplete_forms_are_rejected(self):
        self.assertEqual(len(self.template['주차칸']),28)
        self.assertTrue(all(r['번호'] is None and r['점유']=='미입력' for r in self.template['주차칸']))
        for mut in ('missing','duplicate','unentered','other_image'):
            f=self.filled()
            if mut=='missing':f['주차칸'].pop()
            if mut=='duplicate':f['주차칸'][-1]=f['주차칸'][-2]
            if mut=='unentered':f['주차칸'][-1]['점유']='미입력'
            if mut=='other_image':f['원본']['A']['원본_SHA256']='x'
            path=self.root/(mut+'.json');atomic_json(path,f)
            with self.subTest(mut=mut),self.assertRaises(ValueError):field_check(self.root,path)

    def test_field_result_preserves_existing_truth_and_counts_hold_in_denominator(self):
        f=self.filled();path=self.root/'truth.json';atomic_json(path,f)
        target,_=field_check(self.root,path);r=read_json(target)
        self.assertEqual((r['결과']['차량칸'],r['결과']['판독보류'],r['결과']['빈칸점유보류']),(1,1,27))
        self.assertEqual(r['차량칸_번호색점유_일치율'],0)
        self.assertEqual(r['주차선대조']['판정보류'],1)
        self.assertIsNone(r['학습번호전달검사'])
        self.assertFalse(list((self.root/'reviews').glob('*.json')))
        self.assertNotEqual(target,field_check(self.root,path)[0])

    def test_empty_training_export_reports_zero_crops_not_untested(self):
        target,n=export_training(self.root,paired_only=True,selected_only=True,correct_lighting=True)
        self.assertEqual(n,0)
        f=self.filled();f['학습_manifest_상대경로']=(Path(target)/'manifest.json').relative_to(self.root).as_posix()
        path=self.root/'check-training.json';atomic_json(path,f)
        report,_=field_check(self.root,path)
        self.assertEqual(read_json(report)['학습번호전달검사']['번호사진'],0)


class TrainingIntegrity(unittest.TestCase):
    def setUp(self):
        self.case=test_v22.DurableCollection();self.case.setUp();self.addCleanup(self.case.tearDown)

    def test_review_hash_and_approved_number_are_bound_to_delivered_crop(self):
        c=self.case
        self.assertEqual(export_training(c.root)[1],0)
        save_review(c.root,c.record,c.rows('42'),'operator')
        path,_=export_training(c.root,correct_lighting=True)
        rows=read_json(Path(path)/'manifest.json')['samples']
        number=next(r for r in rows if r['task']=='number')
        self.assertEqual(number['label'],'42');self.assertTrue(number['사람검수완료'])
        review=c.root/'reviews'/(c.record['image_id']+'.json')
        self.assertEqual(hashlib.sha256(review.read_bytes()).hexdigest(),number['검수파일_SHA256'])

    def test_training_png_with_wrong_pixels_cannot_be_delivered(self):
        c=self.case;save_review(c.root,c.record,c.rows(),'operator')
        import dataset19
        write=dataset19.write_png
        with patch('dataset19.write_png',side_effect=lambda p,im:write(p,np.full_like(im,12))),self.assertRaises(ValueError):
            export_training(c.root)
        statuses=[read_json(p)['상태'] for p in (c.root/'exports').rglob('EXPORT_STATUS.json')]
        self.assertIn('실패',statuses)

    def test_wrong_unapproved_estimate_is_excluded_even_from_unfiltered_training(self):
        from dataset19 import persist_scene
        c=self.case;p=copy.deepcopy(c.pred)
        row=p['spaces'][0];box=row['plate_bbox']
        row.update(number=None,plate_bbox=None,selected_ocr=None,ocr_status='estimated',
                   number_estimate={'number':'42','alternate':'23','method':'open_print','score':.87,'margin':.04,'text_bbox':box})
        record,_=persist_scene(c.root,c.p,c.roi,'batch','session',c.cfg,p,c.record['image_id'])
        form=c.rows('42');form[0]['review_status']='pending'
        form[1].update(occupancy='vacant',review_status='approved')
        save_review(c.root,record,form,'operator')
        target,count=export_training(c.root)
        samples=read_json(Path(target)/'manifest.json')['samples']
        self.assertEqual(count,1);self.assertEqual(samples[0]['task'],'occupancy')
