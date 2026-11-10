"""Do not confuse repeated observations, model classes, and physical vehicles."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from web_live.evaluate import evaluate
from web_live.tests.test_contract import fixture


class CohortTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        result,_=fixture();truth={s['space_id']:{'occupancy':'empty'} for s in result['spaces']}
        for row,number,kind in zip(result['spaces'],('11','42','87'),('ev','ev','general')):
            truth[row['space_id']]={'occupancy':'occupied','car_id':number,'vehicle_type':kind}
            row.update(observation='occupied',car_id=number,vehicle_type=kind)
        result['spaces'][1]['car_id']=None
        (self.base/'prediction.json').write_text(json.dumps(result),encoding='utf-8')
        for name,color in [('first','white'),('second','blue'),('enrollment','green')]:
            Image.new('RGB',(40,35),color).save(self.base/name,format='PNG')
        (self.base/'evidence.txt').write_text('Synthetic roster evidence for tests only',encoding='utf-8')
        self.sample=dict(split='test',session_id='test-session',sample_id='one',image='first',prediction='prediction.json',
                         lot_id='A',truth=truth,truth_reviewed_independently=True)
        self.roster={'11':{'cohort':'trained','evidence':'evidence.txt'},'42':{'cohort':'untrained','evidence':'evidence.txt'}}
    def tearDown(self):self.temp.cleanup()

    def test_repeated_observations_do_not_increase_unique_vehicle_count(self):
        other=dict(self.sample,sample_id='two',image='second')
        report=evaluate({'samples':[self.sample,other],'vehicle_cohorts':self.roster},self.base)
        for name in ('trained','untrained','unknown'):
            self.assertEqual(report['cohorts'][name]['vehicle_observations'],2)
            self.assertEqual(report['cohorts'][name]['unique_vehicle_count'],1)
        self.assertEqual(report['cohorts']['untrained']['number'],{'correct':0,'wrong':0,'null':2})
        self.assertEqual(report['cohorts']['trained']['number']['correct'],2)
        self.assertEqual(report['spaces'],24)

    def test_missing_roster_is_unknown_not_inferred_from_model_classes(self):
        report=evaluate({'samples':[self.sample]},self.base)
        self.assertEqual(report['cohorts']['unknown']['vehicle_ids'],['11','42','87'])
        self.assertEqual(report['cohorts']['trained']['unique_vehicle_count'],0)

    def test_numberless_truth_counts_observation_without_inventing_an_identity(self):
        self.sample['truth']['A-01']['car_id']=None
        report=evaluate({'samples':[self.sample]},self.base)
        self.assertEqual(report['cohorts']['unknown']['vehicle_observations'],3)
        self.assertEqual(report['cohorts']['unknown']['unique_vehicle_count'],2)

    def test_invalid_or_unsubstantiated_cohort_is_rejected(self):
        for entry in ({'cohort':'trained'}, {'cohort':'untrained','evidence':'missing.txt'}, {'cohort':'inferred','evidence':'evidence.txt'}):
            with self.subTest(entry=entry):
                self.assertRaises(ValueError,evaluate,{'samples':[self.sample],'vehicle_cohorts':{'11':entry}},self.base)


if __name__=='__main__':unittest.main()
