"""Reproducible checks for direction evidence, field reports and collection integrity."""
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from collection_run import CollectionRun
from contracts import FIELD_SET
from dataset19 import export_training, persist_scene
from empty_pair import EmptyView, read_reference_source, store_empty_pair
from parking_ai.storage import atomic_json
from pipeline import read_json
from review import save_review
from round_sticker import identify
import test_v22
import test_v45
import test_v46
from test_v34 import data


class DirectionEvidence(unittest.TestCase):
    def read(self, secondary, ink=('99', '66', '99', '66'), radius=20, shape_number='99',detector='99',weak_views=()):
        f=test_v45
        disc={'radius':radius,'polygon':f.POLY,'color':'white'}
        shape={'score':.94,'margin':.08,'number':shape_number,'alternate':'98','box':[35,35,65,60],
               'angle':0,'views':[np.zeros((30,40),np.uint8)]*2}
        reader=Mock()
        reader.recognize.side_effect=[[f.hit(value)] for value in ink]
        selected={'number_candidate':detector,'status':'candidate','score':.98,'selected_in_view':True,
                  'rotation_degrees':180,'method':'sticker_value_binary'}
        weak=[{'number_candidate':shape_number,'status':'low_score','score':.55,'selected_in_view':False,
               'rotation_degrees':0,'method':method} for method in weak_views]
        primary={'selected':selected if secondary else None,'candidates':weak+([selected] if secondary else []),
                 'number_candidate':detector if secondary else None,'status':'candidate' if secondary else 'unread'}
        with patch('round_sticker.proposals',return_value=[disc]), patch('print_estimator.examine',return_value=shape), \
             patch('sticker_reference.compare',return_value=None), patch('sticker_ocr.read_sticker',return_value=primary):
            return identify(f.IMAGE,{'polygon':f.POLY},reader,f.CFG)

    def check_held(self, result):
        disc,read=result
        self.assertEqual(disc['color'],'white')
        self.assertEqual(read['status'],'sticker_number_unread')
        self.assertFalse(read.get('number_candidate') or read.get('estimate'))
        self.assertEqual({e['number_candidate'] for e in read['candidates']},{'66','99'})

    def test_second_reader_cannot_erase_conflicting_ink(self):
        self.check_held(self.read(True))

    def test_template_cannot_erase_conflicting_ink_on_small_disc(self):
        self.check_held(self.read(False,radius=10))

    def test_consistent_ink_can_still_confirm(self):
        self.assertEqual(self.read(True,('99',)*4)[1]['number_candidate'],'99')

    def test_shape_and_independent_ink_block_contradictory_detector_for_other_numbers(self):
        disc,r=self.read(True,('','37','',''),shape_number='37',detector='79')
        self.assertEqual((disc['color'],r['status']),('white','sticker_number_unread'))
        self.assertEqual({e['number_candidate'] for e in r['candidates']},{'37','79'})
        self.assertFalse(r.get('estimate') or r.get('selected'))

    def test_shape_alone_does_not_overrule_detector(self):
        self.assertEqual(self.read(True,('',)*4,shape_number='37',detector='79')[1]['number_candidate'],'79')

    def test_matching_ink_views_at_one_angle_outweigh_an_unsupported_template(self):
        _,r=self.read(True,('37','','37',''),shape_number='54',detector='79')
        self.assertEqual(r['status'],'sticker_number_unread')

    def test_ordinary_number_supported_by_shape_ink_and_detector_survives_other_turn(self):
        _,r=self.read(True,('24','52','24','52'),shape_number='24',detector='24')
        self.assertEqual(r['number_candidate'],'24')

    def test_three_weak_opposite_views_corroborating_shape_can_veto_but_not_promote(self):
        methods=('sticker_color','sticker_value_normalized','sticker_value_binary')
        _,r=self.read(True,('92',)*4,shape_number='35',detector='92',weak_views=methods)
        self.assertEqual(r['status'],'sticker_number_unread')
        self.assertFalse(r.get('selected') or r.get('estimate'))
        self.assertEqual({e['number_candidate'] for e in r['candidates']},{'35','92'})

    def test_repeated_weak_read_from_one_method_does_not_count_as_three(self):
        _,r=self.read(True,('92',)*4,shape_number='35',detector='92',weak_views=('sticker_color',)*3)
        self.assertEqual(r['number_candidate'],'92')

    def test_half_turn_pairs_held_even_when_font_template_favors_third_value(self):
        for left,right in [('86','98'),('68','89')]:
            with self.subTest(left=left):
                disc,r=self.read(True,(left,right,left,right),shape_number='56',detector=right)
                self.assertEqual((disc['color'],r['status']),('white','sticker_number_unread'))
                self.assertEqual({e['number_candidate'] for e in r['candidates']},{left,right})

    def test_local_detector_checks_both_turns_for_reversible_print(self):
        case=test_v45.IdentityAudit()
        r=case.read([[test_v45.hit('86')],[],[test_v45.hit('98')],[]]*3)
        self.assertEqual(r['status'],'conflicting_views')
        self.assertIsNone(r['selected'])


class FieldReports(unittest.TestCase):
    def test_optional_truth_angle_is_independent_of_guessed_orientation(self):
        f=test_v46.FieldMetrics();f.setUp()
        row=f.form['주차칸'][0]
        row.update(점유='차량',번호=23,스티커색='초록',스티커방향_도=90)
        f.predictions['A']['spaces'][0].update(number='24',color='green')
        self.assertEqual(f.compare()['방향별_번호대조']['실물칸']['90']['직접오답'],1)
        del row['스티커방향_도']
        self.assertEqual(f.compare()['방향별_번호대조']['실물칸']['미기록']['직접오답'],1)
        row['스티커방향_도']=360
        with self.assertRaisesRegex(ValueError,'스티커 방향'):f.compare()

    def test_number_and_color_have_independent_outcomes(self):
        f=test_v46.FieldMetrics();f.setUp()
        for i in range(3):
            f.form['주차칸'][i].update(점유='차량',번호=23,스티커색='초록')
        a,b,c=f.predictions['A']['spaces'][:3]
        a.update(number='23',color='blue')
        b.update(number=None,number_estimate={'number':'24'},color='green')
        c.update(number=None,color='unknown')
        metrics=f.compare()['번호와색_개별대조']['실물칸']
        self.assertEqual(metrics['번호'],dict(직접일치=1,추정일치=0,직접오답=0,추정오답=1,보류=1))
        self.assertEqual(metrics['스티커색'],dict(일치=1,오답=1,보류=1))

    def test_virtual_report_row_separates_observation_and_physical_result(self):
        f=test_v46.FieldMetrics();f.setUp()
        f.template['원본']['B']['위치근거']='가상 안내선 · 스티커 시험'
        f.form['원본']=f.template['원본']
        f.predictions['B']['spaces'][0]['occupancy']='occupied'
        report=f.compare();virtual=next(r for r in report['칸별결과'] if r['카메라']=='B')
        self.assertEqual(virtual['화면관측'],'occupied')
        self.assertIs(virtual['실물칸확인'],False)
        self.assertIsNone(virtual['자동점유'])
        self.assertIsNone(virtual['자동전용칸판정'])
        self.assertEqual(report['실물점유대조']['비교칸'],12)


class CollectionClock(unittest.TestCase):
    def test_measurement_results_count_reconnect_no_sticker_and_manual_stop(self):
        from concurrent.futures import Future
        from types import SimpleNamespace
        from dual_dashboard import DualDashboard
        from test_v19 import fixture
        _,packet,_=fixture()
        for kind,counter in [('reconnect','분석보류쌍'),('empty','스티커대기쌍'),('stop','정지로해제한분석쌍')]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                run=CollectionRun(tmp,'test',{})
                app=Mock();app.run_record=run;app.extra=app.write_job=app.hub=None
                app.running=kind!='stop';app.closing=app.one_shot=False;app.jobs={};app.frames={}
                app.busy.return_value=True;app.last_labels={k:Mock() for k in 'AB'};app.cfg={}
                for k in 'AB':
                    p=replace(packet,camera_id=k,serial=k)
                    app.frames[k]=replace(p,stream_epoch=p.stream_epoch+1) if kind=='reconnect' else p
                    outcome=SimpleNamespace(packet=p,roi={'lot_id':k,'registration_method':'virtual_sticker_guide_v1'},
                                            prediction={'spaces':[{'sticker_polygon':None}],'errors':[]})
                    future=Future();future.set_result(outcome);app.jobs[k]=future
                DualDashboard.step(app)
                self.assertEqual(run.data[counter],1)

    def test_packets_after_stop_do_not_change_run_reconnection_counts(self):
        from test_v19 import fixture
        _,p,_=fixture()
        with tempfile.TemporaryDirectory() as tmp:
            run=CollectionRun(tmp,'test',{});run.observe_channels({'A':p});run.stop_reason('정지')
            run.observe_channels({'A':replace(p,stream_epoch=p.stream_epoch+1)})
            self.assertEqual(run.data['연결회차변경수']['A'],0)

    def test_stop_and_drain_do_not_make_29_minutes_into_31(self):
        with tempfile.TemporaryDirectory() as tmp, patch('collection_run.time.monotonic',return_value=10.) as clock:
            run=CollectionRun(tmp,'test',{})
            clock.return_value=1750.;run.stop_reason('측정 정지')
            clock.return_value=1870.;run.saved({'id':'last','skew_ms':3.});run.checkpoint(True)
            log=read_json(run.path)
            self.assertEqual((log['측정활성_초'],log['정지후마무리_초'],log['실제경과_초']),(1740,120,1860))

    def test_close_retains_first_stop_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=CollectionRun(tmp,'test',{});run.stop_reason('장치 연결 변경');run.stop_reason('창 닫기')
            run.checkpoint(True)
            self.assertEqual(read_json(run.path)['종료사유'],'장치 연결 변경')


class EmptyEvidence(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        views={k:EmptyView(r.packet,r.roi,('A-01',)) for k in 'AB' for r in [data(k,k,100.)]}
        with patch('empty_pair.disc_evidence',return_value=([],('A-01',))):
            self.evidence=store_empty_pair(self.root,views,{'physical_sticker_set':FIELD_SET})['A'][1]['empty_board']
        self.path=next(self.root.rglob('reference.json'));self.doc=read_json(self.path)

    def test_reviewed_bay_substitution_is_detected(self):
        self.assertEqual(read_reference_source(self.root,self.evidence)[0]['촬영자_재확인칸'],['A-01'])
        self.doc['장면']['A']['촬영자_재확인칸']=['A-02']
        atomic_json(self.path,self.doc)
        with self.assertRaisesRegex(ValueError,'재확인'):read_reference_source(self.root,self.evidence)

    def test_v46_reference_without_optional_digest_loads(self):
        old=self.doc['장면']['A']['근거'];old.pop('재확인칸_SHA256',None);atomic_json(self.path,self.doc)
        self.assertTrue(read_reference_source(self.root,old)[1])


class TrainingLabels(unittest.TestCase):
    def images(self, number):
        case=test_v22.DurableCollection();case.setUp();self.addCleanup(case.tearDown)
        save_review(case.root,case.record,case.rows(),'test')
        packet=replace(case.p,sequence=case.p.sequence+1)
        record,_=persist_scene(case.root,packet,case.roi,'batch','session',case.cfg)
        save_review(case.root,record,case.rows(number),'test')
        return case.root

    def test_conflicting_labels_in_same_split_abort_delivery(self):
        root=self.images('42')
        with self.assertRaisesRegex(ValueError,'동일 학습 크롭 정답 충돌'):export_training(root)
        export=next((root/'exports').iterdir())
        self.assertEqual(read_json(export/'EXPORT_STATUS.json')['상태'],'실패')
        self.assertFalse((export/'manifest.json').exists())

    def test_matching_repeated_labels_in_same_split_still_export(self):
        root=self.images('23')
        path,count=export_training(root)
        self.assertGreater(count,0)
        numbers=[s for s in read_json(Path(path)/'manifest.json')['samples'] if s['task']=='number']
        self.assertEqual([s['label'] for s in numbers],['23','23'])
