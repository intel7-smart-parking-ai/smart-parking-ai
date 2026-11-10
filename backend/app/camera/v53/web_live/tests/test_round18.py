from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock
import numpy as np
from web_live.fire_led import FireWatch, LensModel, project_box
from web_live.glow import localize
from web_live.policy import to_events
from web_live.registration import expected_ids


class FireTests(unittest.TestCase):
    def setUp(self):
        self.key=('1',1,'A','A-02')
        self.box=(30,30,41,41)
        self.off=np.full((224,160,3),40,np.uint8)
        self.on=self.off.copy()
        self.on[30:41,30:41]=(10,120,255)
        self.watch=FireWatch(Mock())
        self.watch.model.score.return_value=.94
        self.start=datetime.now(timezone.utc)

    def see(self,frame,n,*,key=None,eligible=True,roof=None,lamp=True):
        self.watch.model.candidates.return_value=[(.94,self.box)] if lamp else []
        return self.watch.observe(key or self.key,frame,eligible=eligible,sequence=n,
                                  observed_at=(self.start+timedelta(seconds=n*.3)).isoformat(),roof_region=roof)['state']

    def test_off_then_on_and_off(self):
        self.assertEqual(self.see(self.off,1,lamp=False),'off')
        self.assertEqual([self.see(self.on,i) for i in (2,3,4)],['candidate','candidate','confirmed'])
        self.assertEqual(self.see(self.off,5,lamp=False),'off')
        self.assertEqual(self.see(self.on,6),'candidate')

    def test_static_lit_first_or_red_object_never_confirms(self):
        self.assertEqual([self.see(self.on,i) for i in range(6)],['candidate']*6)

    def test_duplicate_time_does_not_advance_and_outage_rearms(self):
        self.see(self.off,0,lamp=False)
        self.see(self.on,1)
        self.assertEqual(self.see(self.on,1),'unverified')
        self.assertEqual(self.see(self.on,2),'candidate')
        self.assertEqual(self.see(self.on,12),'candidate')
        self.assertEqual(self.see(self.on,13),'candidate')

    def test_other_lot_same_space_is_independent(self):
        other=('2',1,'B','A-02')
        for i in range(4):
            a=self.see(self.on if i else self.off,i)
            b=self.see(self.on,i,key=other)
        self.assertEqual((a,b),('confirmed','candidate'))
        self.watch.clear('1')
        self.assertEqual(set(self.watch.bays),{other})

    def test_occlusion_global_light_and_outside_roof(self):
        self.see(self.off,0)
        self.see(self.on,1)
        self.assertEqual(self.see(self.on,2,eligible=False),'unverified')
        self.assertEqual(self.see(self.on,3),'candidate')
        self.see(self.off,4,lamp=False)
        self.assertEqual(self.see(self.on,5,roof=(60,60,120,180)),'off')
        glare=np.full_like(self.on,160)
        glare[30:41,30:41]=self.on[30:41,30:41]
        self.assertTrue(all(self.see(glare,i)!='confirmed' for i in range(6,10)))

    def test_proposals_and_projection(self):
        model=LensModel(np.zeros(7),np.zeros(7),np.ones(7),8.)
        self.assertTrue(model.candidates(self.on))
        for color,ys,xs in [((20,110,255),slice(0,7),slice(31,39)),
                            ((20,110,255),slice(30,99),slice(30,99)),
                            ((180,180,180),slice(30,40),slice(30,40))]:
            image=self.off.copy();image[ys,xs]=color
            self.assertEqual(model.candidates(image),[])
        self.assertEqual(project_box((10,12,18,20),np.diag([.5,.5,1.]),self.off.shape),[20,24,36,40])

    def test_tilted_lens_with_saturated_red_channel(self):
        off=self.off.copy()
        off[30:35,30:41]=(10,50,245)
        on=off.copy()
        on[30:31,30:39,1]=100
        self.watch.model.score.return_value=.15
        self.see(off,0,lamp=False)
        self.assertEqual([self.see(on,i) for i in (1,2,3)],['candidate','candidate','confirmed'])

    def test_weak_score_needs_both_previous_lens_and_optical_change(self):
        dark=self.off.copy()
        dark[30:41,30:41]=(20,50,230)
        bright=dark.copy()
        bright[30:35,30:41,1]=125
        self.watch.model.candidates.return_value=[(.73,self.box)]
        def states(ref):
            self.watch.clear()
            self.watch.model.score.return_value=ref
            out=[]
            for i,image in enumerate((dark,bright,bright,bright)):
                out.append(self.watch.observe(self.key,image,eligible=True,sequence=i,
                           observed_at=(self.start+timedelta(seconds=i*.3)).isoformat()))
            return out[-1]
        self.assertEqual(states(.70)['state'],'candidate')
        confirmed=states(.20)
        self.assertEqual(confirmed['state'],'confirmed')
        self.assertEqual(confirmed['appearance_score'],.73)
        self.assertGreater(confirmed['confidence'],.80)

    def test_fire_location_without_vehicle_or_known_number(self):
        stamp=self.start.isoformat()
        poly=np.float32([[0,0],[20,0],[20,20],[0,20]])
        lights=[dict(bbox_xyxy=box,confidence=.96) for box in
                ([4,4,10,10],[30,25,40,35],[55,4,65,14],[18,4,24,12])]
        localize(lights,poly*2.5,{'A-01':poly})
        self.assertEqual([h['location'] for h in lights],['space','aisle','outside','boundary'])
        rows=[dict(space_id=sid,observation='unknown') for sid in expected_ids('A')]
        result=dict(stream_epoch=2,frame_sequence=1,observed_at=stamp,registration_ok=True,roi_confirmed=True,
                    lot_id='A',spaces=rows,fire_lights=lights)
        packet=dict(source='live_camera',epoch=2,sequence=1,observed_at=stamp)
        events=to_events(result,packet,'camera_a',now=self.start)
        self.assertEqual([e['space_id'] for e in events],['A-01',None])
        self.assertTrue(all(e['event_type']=='mock_fire_led_observed' and e['car_id'] is None for e in events))
        self.assertEqual(to_events(result,dict(packet,source='saved_image'),'camera_a',now=self.start),[])
        self.assertEqual(to_events(dict(result,roi_confirmed=False),packet,'camera_a',now=self.start),[])
