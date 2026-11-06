"""Confirmed empty boards in the two-camera UI, storage and quality-selected export."""
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
from dataset19 import export_training
from dual_capture import CHANNELS, ChannelAnalyzer, alignment_root, recover_pairs, save_pair
from empty_pair import EmptyView, reference_for, store_empty_pair
from marker_setup import enroll, locate
from pipeline import read_json
from review import rows_for_edit, save_review
from test_v34 import data


class EmptyPairCollection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng=np.random.default_rng(35)
        cls.base={k:data(k,k,100+i*.01) for i,k in enumerate(CHANNELS)}
        # Textured gray parking surface and real printed-marker geometry; no cars.
        for item in cls.base.values():
            img=item.packet.image.copy()
            texture=rng.integers(90,141,img.shape[:2],np.uint8)
            for slot in item.roi['spaces']:
                mask=np.zeros(img.shape[:2],np.uint8)
                cv2.fillPoly(mask,[np.int32(slot['polygon'])],255)
                img[mask>0]=texture[mask>0,None]
            item.packet=replace(item.packet,image=img)

    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.workers={}
        self.views={}
        for k,item in self.base.items():
            reader=Mock();reader.readtext.return_value=[]
            self.workers[k]=ChannelAnalyzer(reader)
            result=enroll(alignment_root(self.root,k),item.packet,item.roi)
            self.views[k]=EmptyView(item.packet,result['roi'])

    def analyze(self,k,reference=None,image=None):
        item=self.base[k]
        p=replace(item.packet,sequence=item.packet.sequence+1,
                  image=item.packet.image if image is None else image)
        return self.workers[k].analyze(self.root,p,{'lot':k,'view':'ceiling'},item.cfg,reference)

    def test_complete_capture_through_selected_photos_and_reviewed_training(self):
        refs=store_empty_pair(self.root,self.views)
        results={k:self.analyze(k,refs[k]) for k in CHANNELS}
        for k in CHANNELS:
            self.assertTrue(all(r['occupancy']=='vacant' for r in results[k].prediction['spaces']))
        info=save_pair(self.root,results,{'A':'ba','B':'bb'},'test',100)
        self.assertEqual(info['quality'],{'A':'통과','B':'통과'})
        target,report=export_collection(self.root,selected_only=True,paired_only=True)
        self.assertEqual(report['counts']['space_pairs'],28)
        self.assertEqual(report['빈기준파일수'],{'images':2,'json_files':1})
        self.assertEqual((report['counts']['images'],report['counts']['json_files']),(34,31))
        target=Path(target).rename(self.root/'relocated')
        labels=[read_json(p) for p in (Path(target)/'collected').rglob('*.json')]
        self.assertEqual(len(labels),28)
        for label in labels:
            evidence=label['빈주차장기준']
            self.assertEqual(evidence,refs[label['카메라ID']][1]['empty_board'])
            member_dir=target/'references'/'dual'/evidence['빈기준ID']
            member=read_json(member_dir/'reference.json')['장면'][label['카메라ID']]
            self.assertEqual(hashlib.sha256((member_dir/member['원본파일']).read_bytes()).hexdigest(),evidence['기준원본_SHA256'])
            self.assertFalse(label['사람검수완료'])
            self.assertEqual(label['점유상태'],'빈칸')
        self.assertEqual(export_training(self.root,selected_only=True)[1],0)
        filtered,report=export_collection(self.root,lot='A',selected_only=True)
        self.assertEqual(report['빈기준파일수'],{'images':1,'json_files':1})
        self.assertFalse(list((Path(filtered)/'references').rglob('B.png')))
        record=read_json(self.root/'manifests'/(info['members']['A']['image_id']+'.json'))
        rows=rows_for_edit(record);rows[0].update(occupancy='vacant',review_status='approved')
        save_review(self.root,record,rows,'fixture')
        path,count=export_training(self.root,selected_only=True)
        self.assertEqual(count,1)
        sample=read_json(Path(path)/'manifest.json')['samples'][0]
        self.assertEqual((sample['동시측정ID'],sample['카메라ID'],sample['카메라시리얼']),(info['id'],'A','sim-A'))
        proof=self.root/'references'/'dual'/refs['B'][1]['empty_board']['빈기준ID']/'reference.json'
        before=proof.read_bytes();metadata=read_json(proof)
        metadata['장면']['B']['주차칸등록']['marker_evidence']['statistics']['maximum_error_px']=99.
        from parking_ai.storage import atomic_json
        atomic_json(proof,metadata)
        self.assertEqual(recover_pairs(self.root,{})['held'],1)
        proof.write_bytes(before)
        (self.root/'references'/'dual'/refs['B'][1]['empty_board']['빈기준ID']/'B.png').write_bytes(b'corrupt')
        self.assertEqual(recover_pairs(self.root,{})['held'],1)
        self.assertEqual(export_collection(self.root,selected_only=True,paired_only=True)[1]['counts']['scenes'],0)
        self.assertEqual(export_training(self.root,selected_only=True)[1],0)

    def test_no_reference_and_local_changes_do_not_silently_become_vacant(self):
        self.assertTrue(all(r['occupancy']=='unknown' for r in self.analyze('A').prediction['spaces']))
        refs=store_empty_pair(self.root,self.views)
        image=self.base['A'].packet.image.copy()
        slot=self.views['A'].roi['spaces'][0]
        x,y=np.int32(np.mean(slot['polygon'],axis=0))
        image[y:y+15,x:x+15]=12
        result=self.analyze('A',refs['A'],image)
        row=next(r for r in result.prediction['spaces'] if r['space_id']==slot['space_id'])
        self.assertEqual(row['occupancy'],'unknown')
        self.assertIn('국소 변화',row['occupancy_reason'])

    def test_changed_camera_view_epoch_frame_or_marker_alignment_refused(self):
        refs=store_empty_pair(self.root,self.views)
        old=self.views['A']
        p=replace(old.packet,sequence=old.packet.sequence+1)
        for ref,packet,roi in ((refs['B'],p,old.roi),(refs['A'],old.packet,old.roi),
                             (refs['A'],replace(p,stream_epoch=p.stream_epoch+1),old.roi)):
            with self.assertRaises(ValueError):reference_for(ref,packet,roi)
        template=old.roi['marker_evidence']['template']
        def shifted(dx):
            mat=np.float32([[1,0,dx],[0,1,0]])
            image=cv2.warpAffine(p.image,mat,p.image.shape[1::-1])
            frame=replace(p,image=image)
            return frame,locate(template,frame,old.roi['pose'])['roi']
        p1,roi=shifted(1)
        aligned=reference_for(refs['A'],p1,roi)
        self.assertLess(np.mean(cv2.absdiff(aligned['image'][200:-200,200:-200],p1.image[200:-200,200:-200])),1.)
        p9,roi=shifted(9)
        with self.assertRaisesRegex(ValueError,'구도'):reference_for(refs['A'],p9,roi)
        other=copy.deepcopy(old.roi);other['calibration_key']='other-view'
        with self.assertRaises(ValueError):reference_for(refs['A'],p,other)

    def test_reference_write_interruption_does_not_commit_half_a_pair(self):
        from dataset19 import write_png
        def interrupted(path,image):
            if path.name=='B.png':raise OSError('simulated disk full')
            return write_png(path,image)
        with patch('dataset19.write_png',side_effect=interrupted),self.assertRaises(OSError):
            store_empty_pair(self.root,self.views)
        self.assertFalse(list((self.root/'references').rglob('reference.json')))
        refs=store_empty_pair(self.root,self.views)
        self.assertEqual(set(refs),set(CHANNELS))
        self.assertEqual(len(list((self.root/'references').rglob('reference.json'))),1)

    def test_two_measurements_share_one_exported_reference_pair(self):
        refs=store_empty_pair(self.root,self.views)
        for session in ('first','second'):
            results={k:self.analyze(k,refs[k]) for k in CHANNELS}
            save_pair(self.root,results,{'A':'ba','B':'bb'},session,100)
        _,report=export_collection(self.root,selected_only=True,paired_only=True)
        self.assertEqual(report['counts']['measurements'],2)
        self.assertEqual(report['빈기준파일수'],{'images':2,'json_files':1})

    def test_clipped_bay_blocks_reference_registration_before_writing(self):
        view=self.views['A'];image=view.packet.image.copy()
        slot=view.roi['spaces'][0]
        cv2.fillPoly(image,[np.int32(slot['polygon'])],(255,255,255))
        frame=replace(view.packet,image=image)
        roi=locate(view.roi['marker_evidence']['template'],frame,view.roi['pose'])['roi']
        with self.assertRaisesRegex(ValueError,'빈 기준 보류'):
            store_empty_pair(self.root,{**self.views,'A':EmptyView(frame,roi)})
        self.assertFalse((self.root/'references').exists())

    def test_one_d39_vehicle_and_twenty_seven_empty_bays(self):
        from test_v21 import Reader
        refs=store_empty_pair(self.root,self.views)
        image=self.base['A'].packet.image.copy()
        slot=next(s for s in self.views['A'].roi['spaces'] if s['space_type']=='accessible')
        x,y=map(int,np.mean(slot['polygon'],axis=0))
        cv2.rectangle(image,(x-65,y-78),(x+65,y+78),(45,45,45),-1)
        cv2.rectangle(image,(x-47,y-42),(x+47,y+42),(0,200,0),-1)
        cv2.putText(image,'23',(x-24,y+13),cv2.FONT_HERSHEY_SIMPLEX,1.1,(0,0,0),3)
        self.workers['A'].engine.reader=Reader()
        results={'A':self.analyze('A',refs['A'],image),'B':self.analyze('B',refs['B'])}
        info=save_pair(self.root,results,{'A':'ba','B':'bb'},'test',100)
        self.assertEqual(info['quality'],{'A':'통과','B':'통과'})
        dst,report=export_collection(self.root,selected_only=True,paired_only=True)
        self.assertEqual(report['counts']['space_pairs'],28)
        labels=[read_json(p) for p in (Path(dst)/'collected').rglob('*.json')]
        self.assertEqual(sum(r['점유상태']=='빈칸' for r in labels),27)
        car,=[r for r in labels if r['점유상태']=='차량 있음']
        self.assertEqual((car['주차칸명'],car['차량번호'],car['스티커색'],car['전용칸종류비교']),
                         (slot['space_id'],23,'초록','종류 불일치 의심'))
        self.assertFalse(car['사람검수완료'])
