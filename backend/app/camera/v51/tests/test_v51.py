import copy
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from board_profile import METHOD,align_printed_reference
from empty_pair import reference_for,verify_empty_evidence
from vision19 import context_key,reference_difference


class ConfirmedBoardMovement(unittest.TestCase):
    def setUp(self):
        self.empty=np.full((260,420,3),45,np.uint8)
        slots=[]
        for j,row in enumerate('AB'):
            for i in range(3):
                x=28+110*i;y=30+105*j
                points=np.int32([[x,y],[x+86,y],[x+86,y+72],[x,y+72]])
                cv2.polylines(self.empty,[points[[3,0,1,2]]],False,(190,190,190),3)
                slots.append({'space_id':f'{row}-{i+1:02d}','polygon':points.tolist()})
        self.old=dict(registration_method=METHOD,lot_id='A',camera_id='A',serial='sim',
                      image_size=[420,260],pose='same',calibration_key='same',spaces=slots)
        self.now=copy.deepcopy(self.old)
        for s in self.now['spaces']:s['polygon']=(np.int32(s['polygon'])+[12,9]).tolist()
        self.current=cv2.warpAffine(self.empty,np.float32([[1,0,12],[0,1,9]]),(420,260),borderMode=cv2.BORDER_REPLICATE)
        self.ref=dict(image=self.empty,key=context_key(self.old),epoch=2,frame=11,empty_board={'빈기준ID':'test'})
        self.packet=SimpleNamespace(image=self.current,sequence=12,stream_epoch=2)

    def test_changed_printed_board_binds_transform_without_altering_originals(self):
        old=self.empty.copy()
        ref=reference_for((self.old,self.ref),self.packet,self.now)
        self.assertEqual(ref['key'],context_key(self.now))
        self.assertEqual(set(ref['printed_board_warp']['일치한주차선']),{'A','B'})
        self.assertTrue(np.array_equal(self.empty,old))
        self.assertTrue(np.array_equal(ref['image'],self.current))
        mask,err=reference_difference(self.current,self.now,ref,2)
        self.assertIsNone(err)
        self.assertEqual(cv2.countNonZero(mask),0)

    def test_real_object_in_mapped_bay_remains_visible(self):
        self.current[58:92,55:98]=(12,160,190)
        ref=reference_for((self.old,self.ref),self.packet,self.now)
        mask,err=reference_difference(self.current,self.now,ref,2)
        self.assertIsNone(err)
        self.assertGreater(cv2.countNonZero(mask[60:90,58:94]),900)

    def test_one_visible_row_is_insufficient_for_two_rows(self):
        self.current[135:]=45
        with self.assertRaisesRegex(ValueError,'주차선'):
            align_printed_reference(self.old,self.now,self.empty,self.current)

    def test_large_shift_or_distorted_corner_is_refused(self):
        for mode in ('shift','corner'):
            roi=copy.deepcopy(self.now)
            if mode=='shift':
                for s in roi['spaces']:s['polygon']=(np.int32(s['polygon'])+[100,0]).tolist()
            else:roi['spaces'][-1]['polygon'][1][0]+=13
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                align_printed_reference(self.old,roi,self.empty,self.current)

    def test_bad_epoch_stale_frame_or_different_calibration_cannot_use_warp(self):
        for epoch,seq,key in [(3,12,'same'),(2,11,'same'),(2,12,'other')]:
            self.now['calibration_key']=key
            frame=SimpleNamespace(image=self.current,sequence=seq,stream_epoch=epoch)
            with self.subTest(key=key,seq=seq),self.assertRaises(ValueError):
                reference_for((self.old,self.ref),frame,self.now)

    def test_manual_roi_motion_requires_registration(self):
        self.old.pop('registration_method');self.now.pop('registration_method')
        with self.assertRaisesRegex(ValueError,'좌표 변경'):
            reference_for((self.old,self.ref),self.packet,self.now)

    def test_matching_pixels_outside_board_do_not_replace_missing_paint(self):
        with self.assertRaisesRegex(ValueError,'주차선'):
            align_printed_reference(self.old,self.now,self.empty,np.zeros_like(self.current))

    def test_warp_cannot_be_attached_to_an_unreferenced_scene(self):
        with self.assertRaisesRegex(ValueError,'빈 기준'):
            verify_empty_evidence(None,{'analysis_config':{}},{'reference':{'printed_board_warp':{}}})


class SimpleCaptureControls(unittest.TestCase):
    def setUp(self):
        from dual_dashboard import DualDashboard
        from unittest.mock import Mock
        self.app=DualDashboard.__new__(DualDashboard)
        app=self.app
        app.closing=False;app.running=False;app.pending_capture=None
        app.extra=None;app.run_record=None
        app.busy=Mock(return_value=False);app.start=Mock()
        app.note=Mock();app.show_result=Mock();app.lock_controls=Mock()

    def test_first_key_connects_without_prior_preview(self):
        self.app.quick_capture()
        self.app.start.assert_called_once()
        self.assertEqual(self.app.pending_capture,'continuous')
        self.app.show_result.set.assert_called_once_with(False)

    def test_space_queues_only_one_capture_while_key_repeats(self):
        self.app.quick_capture(once=True)
        self.app.quick_capture(once=True)
        self.app.start.assert_called_once()
        self.assertEqual(self.app.pending_capture,'once')

    def test_startup_recovery_finishes_before_queued_capture(self):
        self.app.extra=('recover',object());self.app.busy.return_value=True
        self.app.quick_capture()
        self.app.start.assert_not_called()
        self.app.extra=None;self.app.busy.return_value=False
        self.app.advance_quick_capture()
        self.app.start.assert_called_once()

    def test_stop_cancels_pending_connection_and_future_autostart(self):
        future=object()
        self.app.pending_capture='once';self.app.extra=('connect',future)
        self.app.stop()
        self.assertIsNone(self.app.pending_capture)
        self.assertEqual(self.app.extra,('discover',future))
        self.app.advance_quick_capture()
        self.app.start.assert_not_called()

    def test_connection_error_drops_pending_action(self):
        self.app.start.side_effect=ValueError('no cameras')
        with self.assertRaisesRegex(ValueError,'no cameras'):
            self.app.quick_capture()
        self.assertIsNone(self.app.pending_capture)

    def test_typing_space_and_child_dialogs_do_not_start_collection(self):
        from unittest.mock import Mock
        app=self.app;app.window=Mock()
        app.window.grab_current.return_value=None
        widget=Mock();widget.winfo_toplevel.return_value=app.window
        widget.winfo_class.return_value='TEntry'
        event=SimpleNamespace(widget=widget,keysym='space',state=0)
        self.assertIsNone(app.shortcut(event))
        widget.winfo_class.return_value='Canvas';widget.winfo_toplevel.return_value=object()
        self.assertIsNone(app.shortcut(event))
        widget.winfo_toplevel.return_value=app.window;app.window.grab_current.return_value=object()
        self.assertIsNone(app.shortcut(event))
        app.start.assert_not_called()

    def test_f5_event_from_camera_canvas_starts_collection(self):
        from unittest.mock import Mock
        app=self.app;app.window=Mock();app.window.grab_current.return_value=None
        widget=Mock();widget.winfo_toplevel.return_value=app.window
        event=SimpleNamespace(widget=widget,keysym='F5',state=0)
        self.assertEqual(app.shortcut(event),'break')
        app.start.assert_called_once()


class RectangularLabels(unittest.TestCase):
    def scene(self,color='green',radius=24,circular=False,printed=True):
        image=np.full((180,260,3),42,np.uint8)
        colors={'green':(145,205,160),'blue':(225,175,100),'white':(235,235,235)}
        if circular:cv2.circle(image,(130,90),radius,colors[color],-1)
        else:cv2.rectangle(image,(130-radius,90-radius),(130+radius,90+radius),colors[color],-1)
        if printed:
            scale=radius/38
            (w,h),_=cv2.getTextSize('23',cv2.FONT_HERSHEY_SIMPLEX,scale,2)
            cv2.putText(image,'23',(130-w//2,90+h//2),cv2.FONT_HERSHEY_SIMPLEX,scale,(12,12,12),2,cv2.LINE_AA)
        slot={'polygon':[[10,10],[249,10],[249,169],[10,169]]}
        return image,slot

    def test_four_and_five_area_scale_labels_retain_color(self):
        from rectangular_sticker import proposals
        for color in ('white','green','blue'):
            for radius in (16,32,36):
                image,slot=self.scene(color,radius)
                before=image.copy()
                candidates=proposals(image,slot['polygon'])
                with self.subTest(color=color,radius=radius):
                    self.assertEqual([d['color'] for d in candidates],[color])
                    self.assertTrue(np.array_equal(image,before))

    def test_circle_and_blank_paper_do_not_become_rectangle_number_labels(self):
        from rectangular_sticker import proposals
        for circular,printed in ((True,True),(False,False)):
            image,slot=self.scene(circular=circular,printed=printed)
            self.assertFalse(proposals(image,slot['polygon']))
        image,slot=self.scene(radius=6)
        self.assertFalse(proposals(image,slot['polygon']))

    def test_multiple_rectangles_are_held_and_empty_registration_sees_print(self):
        from rectangular_sticker import identify
        image,slot=self.scene()
        label,result=identify(image,slot,None,{})
        self.assertEqual(result['status'],'sticker_number_unread')
        self.assertEqual(label['color'],'green')
        both=image.copy()
        both[:,10:70]=image[:,100:160]
        label,result=identify(both,slot,None,{})
        self.assertIsNone(label)
        self.assertEqual(result['status'],'multiple_sticker_candidates')

    def reader(self,values):
        from unittest.mock import Mock
        hits=[[[[0,0],[1,0],[1,1],[0,1]],text,score] for text,score in values]
        return SimpleNamespace(recognize=Mock(side_effect=[[hit] for hit in hits]))

    def read(self,values):
        from rectangular_sticker import proposals,read_rectangle
        image,slot=self.scene()
        label=proposals(image,slot['polygon'])[0]
        return read_rectangle(image,label['polygon'],self.reader(values),
                              {'number_mode':'physical_sticker','number_min':10,'number_max':99,'ocr_min_score':.7})

    def test_two_matching_views_and_one_orientation_confirm(self):
        values=[('23',.98) if n in (0,4) else ('1',.99) for n in range(12)]
        read=self.read(values)
        self.assertEqual((read['status'],read['number_candidate']),('candidate','23'))
        self.assertEqual(sum(e['selected_in_view'] for e in read['candidates']),2)

    def test_conflicting_rotation_cannot_confirm_or_estimate(self):
        values=[('87' if n%4==0 else '28',.99) for n in range(12)]
        read=self.read(values)
        self.assertEqual(read['status'],'sticker_number_unread')
        self.assertIsNone(read['selected'])
        self.assertNotIn('estimate',read)
        self.assertEqual(len(read['candidates']),12)

    def test_one_strong_view_stays_unconfirmed(self):
        read=self.read([('23',.98)]+[('1',.99)]*11)
        self.assertEqual(read['status'],'sticker_number_unread')

    def test_weak_windshield_read_does_not_override_round_sticker(self):
        from unittest.mock import patch,Mock
        from rectangular_sticker import identify
        image,slot=self.scene()
        read={'status':'sticker_number_unread','selected':None,'candidates':[
            {'number_candidate':'11','score':.85,'status':'low_score','method':'sticker_value_normalized','rotation_degrees':0}]}
        with patch('rectangular_sticker.read_rectangle',return_value=read):
            self.assertIsNone(identify(image,slot,Mock(),{'ocr_min_score':.7}))
