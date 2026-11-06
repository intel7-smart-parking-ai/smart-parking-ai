"""Run against either release to reproduce identity failures without a model."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np


from sticker_ocr import read_sticker
from round_sticker import identify

CFG={'number_mode':'physical_sticker','ocr_min_score':.7}
IMAGE=np.full((120,120,3),220,np.uint8)
POLY=[[20,20],[90,20],[90,90],[20,90]]

def hit(text):
    return ([[40,40],[65,40],[65,60],[40,60]],text,.98)

class Reader:
    def __init__(self, answers):self.answers=answers;self.turns={}
    def readtext(self, pixels, **kw):
        view=int(pixels[0,0,0])//10-1
        turn=self.turns.get(view,0);self.turns[view]=turn+1
        return self.answers[view*4+turn]

class IdentityAudit(unittest.TestCase):
    def read(self, answers):
        views=[(name,np.full((100,100,3),(i+1)*10,np.uint8)) for i,name in enumerate(
               ('sticker_color','sticker_value_normalized','sticker_value_binary'))]
        with patch('sticker_ocr.rectified_views',return_value=(views,np.eye(3))):
            return read_sticker(IMAGE,POLY,Reader(answers),CFG)

    def test_opposite_orientation_conflict_must_not_be_confirmed(self):
        r=self.read([[hit('66')],[],[hit('99')],[]]*3)
        self.assertIsNone(r['number_candidate'])
        self.assertEqual(r['status'],'conflicting_views')

    def test_fragmented_first_orientation_must_not_hide_readable_turn(self):
        r=self.read([[hit('1'),hit('6')],[hit('16')],[],[]]*3)
        self.assertEqual(r['number_candidate'],'16')

    def test_one_preprocessing_across_four_turns_is_not_four_votes(self):
        r=self.read([[hit('24')]]*4+[[]]*8)
        self.assertIsNone(r['number_candidate'])
        self.assertEqual(r['status'],'single_view_unconfirmed')

    def test_ordinary_number_keeps_original_three_view_agreement(self):
        r=self.read([[hit('24')]]*12)
        self.assertEqual(r['number_candidate'],'24')
        self.assertEqual(len(r['candidates']),3)
        self.assertEqual(sum(e['selected_in_view'] for e in r['candidates']),3)

    def test_template_estimate_preserves_disagreeing_current_ocr_for_review(self):
        disc={'radius':20,'polygon':POLY,'color':'green'}
        shape={'score':.94,'margin':.08,'number':'24','alternate':'21','box':[35,35,65,60],
               'angle':0,'views':[np.zeros((30,40),np.uint8)]*2}
        class Recognition:
            def recognize(self,*a,**kw):return [hit('24' if np.asarray(a[0])[0,0]==0 else '42')]
            def readtext(self,*a,**kw):return []
        # Independent OCR preprocessings disagree even though the template is strong.
        shape['views'][1]=np.ones((30,40),np.uint8)
        with patch('round_sticker.proposals',return_value=[disc]), \
             patch('print_estimator.examine',return_value=shape), \
             patch('sticker_reference.compare',return_value={'number':'24','score':.98,'margin':.1,'alternate':'21'}):
            d,r=identify(IMAGE,{'polygon':POLY},Recognition(),CFG)
        self.assertEqual(d['color'],'green')
        self.assertEqual(r['estimate']['number'],'24')
        self.assertIsNone(r['selected'])
        self.assertEqual(r['status'],'estimated')
        self.assertEqual({e['number_candidate'] for e in r['candidates']},{'24','42'})

    def test_physical_and_virtual_estimates_both_hold_opposite_66_99(self):
        disc={'radius':20,'polygon':POLY,'color':'white'}
        shape={'score':.94,'margin':.08,'number':'99','alternate':'66','box':[35,35,65,60]}
        for boundary in ('board_open_u','virtual_open_u'):
            with self.subTest(boundary=boundary),patch('round_sticker.proposals',return_value=[disc]), \
                 patch('print_estimator.examine',return_value=shape),patch('sticker_reference.compare',return_value=None):
                d,r=identify(IMAGE,{'polygon':POLY,'boundary_kind':boundary},None,CFG)
            self.assertEqual(d['color'],'white')
            self.assertEqual(r['status'],'sticker_number_unread')
            self.assertNotIn('estimate',r)


class ExposureDiagnostics(unittest.TestCase):
    def setUp(self):
        self.slot={'polygon':POLY}
        self.disc={'polygon':POLY}

    def test_fully_clipped_raw_reports_detail_loss_without_mutating_pixels(self):
        from sticker_exposure import diagnose,validate
        raw=np.full_like(IMAGE,255);before=raw.copy()
        r=diagnose(raw,raw,self.slot,self.disc)
        self.assertEqual(r['status'],'raw_saturated_without_detail')
        self.assertEqual(r['scope'],'sticker_polygon');validate(r)
        np.testing.assert_array_equal(raw,before)

    def test_white_paper_with_print_is_not_equated_to_lost_number(self):
        from sticker_exposure import diagnose
        raw=np.full_like(IMAGE,255);raw[30:80,40:60]=0
        r=diagnose(raw,raw,self.slot,self.disc)
        self.assertEqual(r['status'],'highlight_present')

    def test_processed_clipping_is_distinguished_from_raw_clipping(self):
        from sticker_exposure import diagnose
        raw=IMAGE.copy();raw[30:80,40:60]=0
        r=diagnose(raw,np.full_like(raw,255),self.slot,self.disc)
        self.assertEqual(r['status'],'correction_lost_detail')

    def test_no_sticker_only_claims_bay_scope(self):
        from sticker_exposure import diagnose
        r=diagnose(IMAGE,IMAGE,self.slot)
        self.assertEqual(r['scope'],'bay_polygon')
        self.assertEqual(r['status'],'no_clip_evidence')

    def test_malformed_diagnostic_numbers_are_rejected(self):
        from sticker_exposure import diagnose,validate
        import copy
        r=diagnose(IMAGE,IMAGE,self.slot)
        for value in (True,-.1,1.1,float('nan')):
            q=copy.deepcopy(r);q['raw']['white_clip_ratio']=value
            with self.assertRaises(ValueError):validate(q)

    def test_prediction_contract_rejects_invalid_diagnostics(self):
        from test_v19 import fixture
        from vision19 import VisionEngine
        from prediction_contract import validate_prediction
        cfg,p,roi=fixture();pred=VisionEngine().run(p,roi,cfg,None,'manual')
        pred['spaces'][0]['sticker_exposure']={'status':'anything'}
        context=dict(roi=roi,frame_id=p.sequence,captured_at_utc=p.timestamp_utc,lot_id=roi['lot_id'],stream_epoch=p.stream_epoch,image_size=roi['image_size'])
        with self.assertRaisesRegex(ValueError,'영상 진단'):validate_prediction(pred,context)


class SavedExposureEvidence(unittest.TestCase):
    def test_sidecar_roundtrip_and_rehashed_tampering(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from test_v19 import fixture
        from vision19 import VisionEngine
        from sticker_exposure import diagnose
        from dataset19 import persist_scene
        from collection_output import read_collection
        from parking_ai.storage import atomic_json
        from pipeline import read_json
        cfg,p,roi=fixture();pred=VisionEngine().run(p,roi,cfg,None,'manual')
        row=pred['spaces'][0]
        row['sticker_exposure']=diagnose(p.image,p.image,roi['spaces'][0])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record,assets=persist_scene(root,p,roi,'test','test',cfg,pred)
            bundle=read_collection(root,record)
            item=next(i for i in bundle['items'] if i['kind']=='korean_label' and i['space_id']==row['space_id'])
            path=root/item['path'];label=read_json(path)
            self.assertEqual(label['번호판독영상진단'],row['sticker_exposure'])
            label['번호판독영상진단']['raw']['white_clip_ratio']=.5
            atomic_json(path,label);item['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            atomic_json(root/'bundles'/(record['image_id']+'.json'),bundle)
            with self.assertRaises(ValueError):read_collection(root,record)

    def test_legacy_prediction_without_diagnostic_still_saves_and_reads(self):
        import tempfile
        from pathlib import Path
        from test_v19 import fixture
        from vision19 import VisionEngine
        from dataset19 import persist_scene
        from collection_output import read_collection
        cfg,p,roi=fixture();pred=VisionEngine().run(p,roi,cfg,None,'manual')
        with tempfile.TemporaryDirectory() as tmp:
            record,assets=persist_scene(Path(tmp),p,roi,'legacy','legacy',cfg,pred)
            self.assertEqual(read_collection(Path(tmp),record)['status'],'complete')

if __name__=='__main__':unittest.main(verbosity=2)
