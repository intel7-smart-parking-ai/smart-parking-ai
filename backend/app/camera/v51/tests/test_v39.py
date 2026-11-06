import copy
import unittest
from unittest.mock import Mock
from unittest.mock import patch
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from automatic_setup import missing_stages
from bay_name_retry import localized_read, validate_local_reads
from dual_capture import RegistrationHold
from test_v38 import hit
from layouts import layout,validate_spaces
from partial_bays import partial_end_bays,partial_label
from dual_dashboard import DualDashboard


class PrintedStrokeRetry(unittest.TestCase):
    def setUp(self):
        self.image=np.full((100,160,3),45,np.uint8)
        self.image[38:63,50:110]=181
        self.center=np.array([80,50])
        self.size=np.array([46,21])

    def run_reads(self,sequence,turns=0):
        reader=Mock()
        pending=iter(sequence)
        def recognize(im,**kw):
            text,confidence=next(pending)
            return [hit(im,text,confidence)]
        reader.readtext.side_effect=recognize
        row=localized_read(self.image,reader,'B-01',self.center,self.size,turns,.70)
        return reader.readtext.call_count,row

    def test_two_full_reads_after_numeric_prefix_in_all_camera_rotations(self):
        seq=[('6-0',.92),('8-01',.81),('8-01',.88),('B-01',.95),('B-01',.82)]
        for turns in range(4):
            count,row=self.run_reads(seq,turns)
            self.assertEqual(count,5)
            self.assertEqual(row['numeric_prefix_read']['text'],'8-01')
            self.assertEqual(row['score'],.82)
            self.assertEqual([r['text'] for r in row['local_reads']],['B-01','B-01'])
            validate_local_reads(row,[160,100],.7)

    def test_literal_numeric_prefix_or_one_valid_read_is_held(self):
        for tail in [[('8-01',.99)]*4,[('B-01',.9)]+[('8-01',.99)]*3]:
            self.assertIsNone(self.run_reads([('8-01',.99)]*3+tail)[1])

    def test_wrong_suffix_partial_and_weak_numeric_prefix_do_not_trigger(self):
        for label,score in [('8-02',.99),('8-0',.99),('8-01',.69),('801',.99)]:
            self.assertEqual(self.run_reads([(label,score)]*3),(3,None))

    def test_conflicting_complete_name_still_vetoes_retry(self):
        self.assertIsNone(self.run_reads([('8-01',.99)]*3+[('B-01',.94),('A-01',.95)])[1])

    def test_corrupt_numeric_evidence_or_changed_image_location_rejected(self):
        seq=[('8-01',.90)]*3+[('B-01',.94)]*2
        row=self.run_reads(seq)[1]
        for key,value in [('mode','stroke_plain'),('score',.3),('bbox',[2,4,16,18]),('text','8-02')]:
            changed=copy.deepcopy(row)
            changed['numeric_prefix_read'][key]=value
            with self.assertRaises(ValueError):
                validate_local_reads(changed,[160,100],.7)
        changed=copy.deepcopy(row)
        del changed['numeric_prefix_read']
        with self.assertRaises(ValueError):
            validate_local_reads(changed,[160,100],.7)

    def test_hold_separates_confirmed_print_from_missing_or_conflicting_name(self):
        setup={'missing':['A-08','B-01','B-07'],'duplicate_names':['B-07'],
               'text_evidence':[{'space_id':'A-08'},{'space_id':'B-07'}],
               'expected_count':16,'detected_count':13}
        self.assertEqual(missing_stages(setup),{'A-08':'경계 미확인','B-01':'칸명 미판독','B-07':'칸명 충돌'})
        self.assertIn('A-08(경계 미확인)',str(RegistrationHold(Mock(camera_id='B'),'B',setup)))
        self.assertIsNone(missing_stages({'missing':['A-08']}))


class ClippedEndBay(unittest.TestCase):
    def setUp(self):
        self.im=np.full((260,762,3),45,np.uint8)
        self.catalog={s['space_id']:s for s in layout('B')['spaces']}
        self.complete=[]
        self.names={}
        for number in range(1,9):
            x=50+(number-1)*90
            pts=[[x,55],[x+90,55],[x+52,165],[x-38,165]]
            sid=f'A-{number:02d}'
            cv2.polylines(self.im,[np.int32(pts)[[3,0,1,2]]],False,(245,245,245),3)
            self.names[sid]={'bbox':[x+10,95,x+40,115],'score':.95}
            if number<8:
                self.complete.append({**self.catalog[sid],'polygon':pts,'boundary_kind':'open_u'})
    def find(self):
        return partial_end_bays(self.im,'B',self.catalog,self.complete,self.names,set())
    def test_visible_end_is_registered_with_three_measured_lines_and_one_clipped_corner(self):
        slot,=self.find()
        self.assertEqual((slot['space_id'],slot['neighbours']),('A-08',['A-07','A-06']))
        self.assertTrue(.9 <= slot['visible_fraction'] < 1)
        self.assertTrue(all(v>=.8 for v in slot['line_support']))
        self.assertTrue(all(0<=x<762 and 0<=y<260 for x,y in slot['polygon']))
        self.assertGreater(max(x for x,y in slot['estimated_full_polygon']),761)
        self.assertEqual(partial_label(slot)['칸명'],'A-08')
        with self.assertRaisesRegex(ValueError,'일부 촬영'):
            validate_spaces('B',[slot],[762,260])
    def test_missing_outer_stroke_or_named_neighbour_is_not_filled(self):
        slot=self.complete.pop()
        self.assertFalse(self.find())
        self.complete.append(slot)
        cv2.line(self.im,(770,55),(732,165),(45,45,45),14)
        self.assertFalse(self.find())
    def test_large_cut_or_unread_name_is_not_registered(self):
        full=self.im
        self.im=full[:,:702]
        self.assertFalse(self.find())
        self.im=full
        del self.names['A-08']
        self.assertFalse(self.find())
    def test_hold_table_and_counter_keep_partial_separate(self):
        partial=self.find()
        app=Mock()
        app.registration_views={}
        app.last_labels={k:Mock() for k in 'AB'}
        frames={k:SimpleNamespace(camera_id=k,sequence=2) for k in 'AB'}
        setup={'partial_spaces':partial,'registered_count':8,'detected_count':7,'expected_count':16,
               'missing':['A-08'],'spaces':self.complete}
        hold=RegistrationHold(frames['B'],'B',setup)
        cfg={'channels':{k:{'lot':k} for k in 'AB'}}
        DualDashboard.show_partial_registration(app,frames,{'A':ValueError(),'B':hold},cfg,True)
        self.assertIn('위치 8/16',app.last_labels['B'].set.call_args.args[0])
        self.assertIn('일부 촬영',app.fill_rows.call_args.args[3]['A-08'][0])
        self.assertIs(app.registration_views['B'].packet,frames['B'])
        app.show_result.set.assert_called_once_with(True)


class ImageFileRetry(unittest.TestCase):
    def test_brief_image_publish_lock_retries_and_persistent_lock_preserves_original(self):
        import os
        from dataset19 import write_png
        im=np.full((24,24,3),50,np.uint8)
        with tempfile.TemporaryDirectory() as folder:
            dst=Path(folder)/'A-08.png'
            replace=os.replace
            calls=[]
            def locked_once(a,b):
                calls.append(a)
                if len(calls)==1:
                    raise PermissionError('busy')
                replace(a,b)
            with patch('parking_ai.storage.os.replace',side_effect=locked_once):
                write_png(dst,im)
            np.testing.assert_array_equal(cv2.imdecode(np.frombuffer(dst.read_bytes(),np.uint8),1),im)
            old=dst.read_bytes()
            with patch('parking_ai.storage.os.replace',side_effect=PermissionError('busy')),patch('parking_ai.storage.time.sleep'):
                with self.assertRaises(PermissionError):
                    write_png(dst,im+1)
            self.assertEqual(dst.read_bytes(),old)
            self.assertEqual(list(dst.parent.iterdir()),[dst])
