import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock,patch
import cv2
import numpy as np
from dual_dashboard import has_registration_view,DualDashboard
from dual_capture import RegistrationHold,ChannelAnalyzer,PairHold
from test_v19 import fixture


class FailedRegistrationDisplay(unittest.TestCase):
    def test_regular_missing_names_replaces_previous_dark_table_and_shows_measured_frames(self):
        frames={k:SimpleNamespace(camera_id=k,sequence=13234) for k in 'AB'}
        setup={'detected_count':8,'expected_count':12,'spaces':[{'space_id':'A-04'}],
               'missing':['A-01'],'text_evidence':[],'duplicate_names':[]}
        held=RegistrationHold(frames['A'],'A',setup)
        outcomes={'A':held,'B':ValueError('test')}
        self.assertTrue(has_registration_view(outcomes))
        app=Mock()
        app.registration_views={}
        app.last_labels={k:Mock() for k in 'AB'}
        DualDashboard.show_partial_registration(app,frames,outcomes,{'channels':{k:{'lot':k} for k in 'AB'}},True)
        notes=app.fill_rows.call_args_list[0].args[3]
        self.assertEqual(notes['A-01'],['등록 보류 · 칸명 미판독'])
        self.assertIs(app.registration_views['A'].packet,frames['A'])
        self.assertNotIn('저조도',str(notes))
        app.show_result.set.assert_called_once_with(True)
        self.assertFalse(has_registration_view({'A':ValueError('not a registration result')}))


class RegisteredCapture(unittest.TestCase):
    def setUp(self):
        from layouts import snapshot
        from vision19 import make_reference
        cfg,p,roi=fixture()
        cfg['physical_sticker_set']='round-10-99-v1'
        self.cfg=cfg
        self.setting={'lot':'A','view':'ceiling'}
        self.worker=ChannelAnalyzer(Mock())
        self.worker.engine.reader.readtext.return_value=[]
        self.worker.register_roi=Mock(side_effect=AssertionError('차량 배치 후 칸 이름 OCR 재호출'))
        rng=np.random.default_rng(41)
        gray=rng.integers(80,180,p.image.shape[:2],dtype=np.uint8)
        base=replace(p,image=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR))
        self.roi=snapshot('A','A',base.serial,gray.shape[::-1],cfg['camera_pose']['A']+'__view-ceiling',roi['spaces'])
        self.empty=(self.roi,{**make_reference(base,self.roi),'empty_board':{'빈기준ID':'simulation'}})
        self.packet=replace(base,sequence=base.sequence+1,image=base.image.copy())

    def test_registered_regions_survive_car_covering_print_without_bay_ocr(self):
        self.packet.image[88:168,50:112]=[15,25,210]
        result=self.worker.analyze('.',self.packet,self.setting,self.cfg,self.empty)
        row=result.prediction['spaces'][0]
        self.assertEqual(row['occupancy'],'occupied')
        self.assertEqual(result.roi['spaces'],self.roi['spaces'])
        self.worker.register_roi.assert_not_called()

    def test_registration_refuses_camera_motion_instead_of_reusing_old_pixels(self):
        shifted=np.roll(self.packet.image,22,axis=1)
        with self.assertRaisesRegex(PairHold,'구도'):
            self.worker.analyze('.',replace(self.packet,image=shifted),self.setting,self.cfg,self.empty)
        self.worker.register_roi.assert_not_called()

    def test_observed_sticker_set_is_bound_to_new_capture(self):
        from contracts import sticker_colors,number_config_for_lot
        from analysis import number
        legacy={}
        self.assertEqual(sticker_colors(legacy)['11'],'white')
        self.assertIsNone(number('77',number_config_for_lot('A',legacy)))
        self.assertEqual(sticker_colors(self.cfg),{})
        self.assertEqual(number('77',number_config_for_lot('A',self.cfg)),'77')
        self.assertEqual(number('49',number_config_for_lot('A',self.cfg)),'49')
        self.assertEqual(number('99',number_config_for_lot('A',self.cfg)),'99')

    def test_blank_board_and_repeated_feature_indices_are_rejected(self):
        from board_profile import fit,locate
        blank=replace(self.packet,image=np.full_like(self.packet.image,128))
        self.assertIsNone(locate(blank,'B','ceiling'))
        with self.assertRaises(ValueError):
            fit('A',[0]*16,[[20.,20.]]*16,[640,480])

    def test_reference_light_uses_static_aisle_and_retains_parked_object(self):
        from board_profile import match_aisle_light
        gray=np.tile(np.arange(160,dtype=np.uint8)+35,(120,1))
        empty=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
        slots=[{'boundary_kind':'board_open_u','entrance_edge':[[x,10],[x,105]]} for x in (30,130)]
        dark=np.rint(empty*.75+2).astype('uint8');dark[:,:20]=[90,10,50]
        out,fit=match_aisle_light(dark,{'spaces':slots},empty)
        self.assertEqual(fit['source'],'empty_aisle')
        self.assertLess(np.abs(out[:,35:125].astype(float)-empty[:,35:125]).mean(),1.6)
        self.assertGreater(np.abs(out[:,:20].astype(float)-empty[:,:20]).mean(),15)

    def test_model_load_failure_is_shared_by_both_ocr_entry_points(self):
        from dual_capture import SharedReader
        reader=SharedReader('.',self.cfg)
        with patch('analysis.make_reader',side_effect=OSError('offline')) as factory:
            with self.assertRaisesRegex(RuntimeError,'offline'):reader.recognize(self.packet.image)
            with self.assertRaisesRegex(RuntimeError,'offline'):reader.readtext(self.packet.image)
            self.assertEqual(factory.call_count,1)

    def test_irresolvable_print_on_a_single_clear_disc_retains_only_paper_color(self):
        from round_sticker import identify
        from contracts import number_config_for_lot
        poly=[[30,30],[60,30],[60,60],[30,60]]
        disc={'radius':20,'polygon':poly,'color':'blue'}
        read={'score':.93,'margin':.001,'number':'69','alternate':'96','box':[34,36,55,51]}
        with patch('round_sticker.proposals',return_value=[disc]),patch('print_estimator.examine',return_value=read),patch('sticker_reference.compare',return_value=None):
            chosen,answer=identify(self.packet.image,{'polygon':poly},None,number_config_for_lot('A',self.cfg))
        self.assertEqual(chosen['color'],'blue')
        self.assertEqual(answer['status'],'sticker_number_unread')
        self.assertNotIn('estimate',answer)
