from datetime import datetime, timedelta, timezone
import json
import threading
import time
import unittest
from unittest.mock import patch
from web_live.evaluate import evaluate
from web_live.policy import CATALOG
from web_live.tests import test_evaluation_cohorts as cohorts
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import eventually
from web_live.transport import EventTransport


def observation(n,number='31',epoch=1):
    stamp=(datetime.now(timezone.utc)+timedelta(seconds=n)).isoformat()
    return dict(source='vision',camera_id='camera_a',lot_id='A',space_id='B-06',event_id=str(n),
                frame_id=f'camera_a:{epoch}:{n}:{stamp}',observed_at=stamp,event_type='vehicle_observed',
                car_id=number,vehicle_type='general',confidence=.90,bbox_xyxy=None)


class StableTransportTests(unittest.TestCase):
    def test_only_same_state_stream_and_fresh_distinct_samples_can_overlap(self):
        previous=observation(0);new=observation(1)
        args=lambda e,g=1:(e,None,'http://127.0.0.1','test',g)
        test=lambda e,g=1:EventTransport._allow_overlap(args(previous),args(e,g),time.monotonic()-1)
        self.assertTrue(test(new))
        for diff in (dict(event_type='space_empty_observed'),dict(car_id=None),dict(vehicle_type='ev'),
                     dict(confidence=.79),dict(frame_id=previous['frame_id']),dict(event_id=previous['event_id']),
                     dict(observed_at=previous['observed_at']),dict(frame_id='camera_a:2:1:time')):
            with self.subTest(diff=diff):self.assertFalse(test(dict(new,**diff)))
        self.assertFalse(test(new,2))
        for item in (dict(car_id=None),dict(vehicle_type='accessible'),dict(car_id='00')):
            self.assertFalse(EventTransport._allow_overlap(args(dict(previous,**item)),args(dict(new,**item)),time.monotonic()-1))

    def test_changed_identity_waits_for_both_outstanding_requests(self):
        gate=[threading.Event(),threading.Event()];seen=[]
        def deliver(e,*args):
            seq=int(e['event_id']);seen.append(seq)
            if seq<2:gate[seq].wait(4)
            return 'accepted'
        with patch('web_live.transport.destination',return_value=('http://127.0.0.1','test')),patch.object(EventTransport,'_deliver',side_effect=deliver):
            t=EventTransport()
            try:
                t.offer([observation(0)],lambda _:True)
                self.assertTrue(eventually(lambda:seen==[0]))
                t.offer([observation(1)],lambda _:True)
                self.assertTrue(eventually(lambda:seen==[0,1]))
                t.offer([observation(2,'87')],lambda _:True);gate[1].set()
                self.assertFalse(eventually(lambda:2 in seen,.15))
                gate[0].set();self.assertTrue(eventually(lambda:seen==[0,1,2]))
            finally:
                for g in gate:g.set()
                t.close()


class FullCohortTests(unittest.TestCase):
    def test_full_roster_gap_requires_physical_images_complete_split_and_all_ids(self):
        t=cohorts.CohortTests();t.setUp();self.addCleanup(t.tearDown)
        numbers=sorted(CATALOG)[:24]
        roster={n:dict(cohort='trained' if i<6 else 'untrained',evidence='evidence.txt') for i,n in enumerate(numbers)}
        training=dict(t.sample,split='train',session_id='enrollment',sample_id='training',image='enrollment')
        samples=[training]
        for batch,first in enumerate((0,12)):
            r,_=fixture();truth={}
            for i,row in enumerate(r['spaces']):
                n=numbers[first+i];sid=row['space_id']
                truth[sid]=dict(occupancy='occupied',car_id=n,vehicle_type=CATALOG[n])
                row.update(observation='occupied',car_id=n if first+i<6 else None,vehicle_type=CATALOG[n])
            pred='pred'+str(batch)+'.json';(t.base/pred).write_text(json.dumps(r),'utf8')
            samples.append(dict(t.sample,sample_type='physical_capture',truth=truth,prediction=pred,
                                image=('first','second')[batch],sample_id='test'+str(batch)))
        manifest=dict(samples=samples,vehicle_cohorts=roster,training_inventory_complete=True)
        result=lambda m:evaluate(m,t.base)['full_24_comparison']
        self.assertEqual(result(manifest)['trained_minus_untrained_pp'],100)
        for change in (dict(samples=samples[:2]),dict(training_inventory_complete=False),dict(vehicle_cohorts={}),
                       dict(samples=[training,dict(samples[1],sample_type='composite'),samples[2]])):
            with self.subTest(keys=list(change)):
                report=result(dict(manifest,**change))
                self.assertFalse(report['available'])
                self.assertIsNone(report['trained_minus_untrained_pp'])
