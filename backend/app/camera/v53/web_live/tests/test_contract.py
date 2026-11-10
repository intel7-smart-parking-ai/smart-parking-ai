import copy
from datetime import datetime,timedelta,timezone
import unittest
from unittest.mock import patch
import numpy as np
from web_live.policy import CATALOG,confirmed_number,to_events,valid_score
from web_live.registration import expected_ids,dedicated,overlap_slot
from web_live.pipeline import empty_similarity,same_pose,crop_quality,public_result
from web_live.models import unique_choice
from web_live.ocr_worker import read_number
from web_live.transport import destination,EventTransport


def fixture(lot='A'):
    stamp=datetime.now(timezone.utc).isoformat()
    packet={'source':'live_camera','epoch':2,'sequence':8,'observed_at':stamp}
    rows=[{'space_id':sid,'observation':'empty','confidence':.9,'sendable':True,'empty_evidence_ok':True,
           'car_id':None,'vehicle_type':None} for sid in expected_ids(lot)]
    result={'registration_ok':True,'roi_confirmed':True,'lot_id':lot,'stream_epoch':2,'frame_sequence':8,
            'observed_at':stamp,'spaces':rows}
    return result,packet


class ContractTests(unittest.TestCase):
    def test_d54_exact_30_strings(self):
        self.assertEqual(len(CATALOG),30);self.assertNotIn('28',CATALOG);self.assertEqual(CATALOG['23'],'ev')
        self.assertTrue(all(isinstance(x,str) and len(x)==2 for x in CATALOG))

    def test_a_b_counts_and_zones(self):
        self.assertEqual((len(expected_ids('A')),len(expected_ids('B'))),(12,16))
        self.assertEqual([dedicated('A',s) for s in ['A-05','A-06','B-06']],['ev','ev','accessible'])
        self.assertEqual([dedicated('B',s) for s in ['A-07','A-08','B-07','B-08']],['ev','ev','accessible','accessible'])

    def test_good_observations_schema_and_pixels(self):
        for lot,camera in [('A','camera_a'),('B','camera_b')]:
            r,p=fixture(lot);events=to_events(r,p,camera)
            self.assertEqual(len(events),len(expected_ids(lot)))
            self.assertTrue(all(e['bbox_xyxy'] is None and e['confidence']==.9 for e in events))
            self.assertEqual(len({e['event_id'] for e in events}),len(events))
            self.assertEqual(set(events[0]),{'event_id','event_type','source','lot_id','camera_id','space_id','car_id','vehicle_type','confidence','bbox_xyxy','observed_at','frame_id'})

    def test_bad_confidence_never_sent(self):
        for score in [None,True,'0.9',float('nan'),float('inf'),-.1,1.1]:
            with self.subTest(score=score):
                r,p=fixture();r['spaces'][0]['confidence']=score
                self.assertEqual(len(to_events(r,p,'camera_a')),11)
                self.assertFalse(valid_score(score))

    def test_079_080_090_boundary(self):
        for score,n in [(.79,11),(.8,12),(.9,12)]:
            r,p=fixture();r['spaces'][0]['confidence']=score
            self.assertEqual(len(to_events(r,p,'camera_a')),n)

    def test_stale_future_naive_rejected(self):
        for delta in [-3,1]:
            r,p=fixture();p['observed_at']=r['observed_at']=(datetime.now(timezone.utc)+timedelta(seconds=delta)).isoformat()
            self.assertEqual(to_events(r,p,'camera_a'),[])
        r,p=fixture();p['observed_at']=r['observed_at']='2026-11-06T01:00:00'
        self.assertEqual(to_events(r,p,'camera_a'),[])

    def test_photo_does_not_send(self):
        r,p=fixture();p['source']='saved_image';self.assertEqual(to_events(r,p,'camera_a'),[])

    def test_pose_and_epoch_frame_guard(self):
        for field,value in [('registration_ok',False),('roi_confirmed',False),('stream_epoch',3),('frame_sequence',7),('observed_at','x')]:
            r,p=fixture();r[field]=value;self.assertEqual(to_events(r,p,'camera_a'),[])

    def test_wrong_camera_or_unknown_lot(self):
        r,p=fixture();self.assertEqual(to_events(r,p,'camera_b'),[])
        r['lot_id']='C';self.assertEqual(to_events(r,p,'camera_a'),[])

    def test_missing_duplicate_and_fake_bay(self):
        for change in ('missing','duplicate','fake'):
            r,p=fixture()
            if change=='missing':r['spaces'].pop()
            elif change=='duplicate':r['spaces'][-1]['space_id']='A-01'
            else:r['spaces'][-1]['space_id']='aisle'
            self.assertEqual(to_events(r,p,'camera_a'),[])

    def test_occlusion_and_absent_baseline(self):
        r,p=fixture();r['spaces'][0]['empty_evidence_ok']=False;r['spaces'][1]['sendable']=False
        r['spaces'][2]['observation']='unknown';self.assertEqual(len(to_events(r,p,'camera_a')),9)

    def test_unknown_number_keeps_occupancy(self):
        for number in ['28','99',23,None]:
            r,p=fixture();r['spaces'][0].update(observation='occupied',car_id=number,vehicle_type='ev')
            e=to_events(r,p,'camera_a')[0];self.assertEqual(e['event_type'],'vehicle_observed');self.assertIsNone(e['car_id'])

    def test_known_number_kept_as_string(self):
        r,p=fixture();r['spaces'][0].update(observation='occupied',car_id='23',vehicle_type='ev')
        self.assertEqual(to_events(r,p,'camera_a')[0]['car_id'],'23')

    def test_duplicate_car_identity_withheld(self):
        r,p=fixture()
        for row in r['spaces'][:2]:row.update(observation='occupied',car_id='11',vehicle_type='ev')
        self.assertTrue(all(x['car_id'] is None for x in to_events(r,p,'camera_a')))

    def test_number_color_conflict(self):
        r,p=fixture();r['spaces'][0].update(observation='occupied',car_id='11',vehicle_type='general')
        self.assertIsNone(to_events(r,p,'camera_a')[0]['car_id'])

    def test_ocr_required_and_unseen_23_supported(self):
        self.assertIsNone(confirmed_number(None,'11','ev'))
        self.assertEqual(confirmed_number({'number':'23','confidence':.95},'28','ev'),'23')
        self.assertIsNone(confirmed_number({'number':'28','confidence':.99},'28','ev'))
        self.assertIsNone(confirmed_number({'number':'87','confidence':.95},'34','general'))
        self.assertIsNone(confirmed_number({'number':'77','confidence':.79},'77','accessible'))

    def test_multi_label_ambiguity(self):
        self.assertIsNone(unique_choice({'empty':.7,'occupied':.71},.65,.45))
        self.assertEqual(unique_choice({'empty':.72,'occupied':.38},.65,.45),'empty')

    def test_bbox_is_sticker_overlap(self):
        poly=[[0,0],[100,0],[100,100],[0,100]]
        self.assertAlmostEqual(overlap_slot([50,50,150,150],poly),.25)
        self.assertEqual(overlap_slot([5,5,5,10],poly),0)

    def test_light_and_occlusion_are_not_empty_evidence(self):
        rng=np.random.default_rng(4);ref=rng.integers(35,160,(224,160,3),dtype=np.uint8)
        self.assertTrue(empty_similarity(ref,ref))
        light=np.clip(ref.astype(float)+40,0,255).astype('uint8');self.assertFalse(empty_similarity(light,ref))
        hand=ref.copy();hand[30:130,30:130]=[130,170,220];self.assertFalse(empty_similarity(hand,ref))

    def test_quality_holds_flat_dark_overexposed(self):
        for value in (0,10,128,255):self.assertIsNotNone(crop_quality(np.full((160,224,3),value,np.uint8)))

    def test_pose_move_invalidates(self):
        p=np.float32([[0,0],[100,0],[100,100],[0,100]])
        self.assertTrue(same_pose(p,p+1,(1080,1920,3)))
        self.assertFalse(same_pose(p,p+30,(1080,1920,3)))

    def test_private_arrays_excluded(self):
        self.assertEqual(public_result({'a':1,'_crops':np.zeros(1),'_outline':[]}),{'a':1})

    def test_ocr_rotation_conflict_preserved(self):
        class Reader:
            def __init__(self):self.i=0
            def recognize(self,*a,**kw):
                n=['87']*3+['28']*3+['87']*3+['28']*3;n=n[self.i];self.i+=1
                return [([[0,0],[20,0],[20,20],[0,20]],n,.95)]
        r=read_number(Reader(),np.full((40,50,3),180,np.uint8));self.assertIsNone(r['number'])

    def test_ocr_valid_two_digits_not_concatenated(self):
        class Reader:
            def recognize(self,*a,**kw):return [([], '1',.99),([], '1',.99)]
        self.assertIsNone(read_number(Reader(),np.full((40,50,3),180,np.uint8))['number'])

    def test_endpoint_credentials_and_path_guard(self):
        for url in ['','http://u:p@localhost:8000','http://localhost:8000/x','file:///tmp/a','http://localhost:8000?token=x']:
            with patch.dict('os.environ',{'API_BASE_URL':url,'VISION_TOKEN':'test'},clear=False):
                self.assertRaises(ValueError,destination)

    def test_transport_cancel_prevents_network(self):
        t=EventTransport()
        try:
            r,p=fixture();e=to_events(r,p,'camera_a')[0]
            generation=t.generation;t.cancel()
            with patch('urllib.request.build_opener') as opener:
                self.assertEqual(t.send_one(e,lambda e:True,'http://127.0.0.1:8000','test',generation),'withheld_or_expired')
                opener.return_value.open.assert_not_called()
        finally:t.close()


if __name__=='__main__':unittest.main()
