from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from PIL import Image
from collection_output import export_collection,png_bytes,verify_display_png,validate_collection_label
from dual_capture import PairHold,recover_pairs,save_pair
from dual_dashboard import DualDashboard,has_registration_view
from empty_pair import EmptyView,store_empty_pair
from pipeline import read_json
from test_v34 import data
import hashlib


class CollectionIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames={k:data(k,k,10+i*.01) for i,k in enumerate('AB')}

    def test_invalid_encoded_photo_never_publishes_a_pair_and_can_be_rebuilt(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('collection_output.png_bytes',return_value=b'not-an-image'),self.assertRaises((ValueError,OSError)):
                save_pair(root,self.frames,{'A':'a','B':'b'},'test',100)
            self.assertEqual(len(list((root/'raw').rglob('*.png'))),2)
            self.assertEqual(export_collection(root,paired_only=True)[1]['counts']['space_pairs'],0)
            self.assertEqual(recover_pairs(root,{})['complete'],1)
            self.assertEqual(export_collection(root,paired_only=True)[1]['counts']['space_pairs'],28)

    def test_valid_png_with_different_pixels_is_rejected(self):
        original=Image.new('RGB',(60,80),'green')
        payload=png_bytes(Image.new('RGB',original.size,'blue'))
        with self.assertRaisesRegex(ValueError,'픽셀'):
            verify_display_png(payload,list(original.size),hashlib.sha256(original.tobytes()).hexdigest())

    def test_legacy_png_with_decodable_pixels_and_missing_end_checksum_is_held(self):
        payload=png_bytes(Image.new('RGB',(60,80),'green'))
        verify_display_png(payload)
        with self.assertRaisesRegex(ValueError,'PNG'):
            verify_display_png(payload[:-1])

    def test_estimate_must_carry_its_status_and_integer_in_the_korean_label(self):
        from collection_output import expected_scene_labels
        from records import load_prediction
        import copy
        with TemporaryDirectory() as folder:
            root=Path(folder)
            info=save_pair(root,self.frames,{'A':'a','B':'b'},'test',100)
            identifier=info['members']['A']['image_id']
            record=read_json(root/'manifests'/(identifier+'.json'))
            expected=expected_scene_labels(root,record,load_prediction(root,record))
            items=read_json(root/'bundles'/(identifier+'.json'))['items']
            item=next(i for i in items if i['kind']=='korean_label')
            source=expected[item['space_id']]
            source.update(번호판독상태='추정 · 검토 필요',추정차량번호=11)
            sample=read_json(root/item['path'])
            sample.update(번호판독상태=source['번호판독상태'],추정차량번호=11)
            validate_collection_label(sample,record,item,items,expected)
            for key in ('번호판독상태','추정차량번호'):
                with self.subTest(key=key),self.assertRaises(ValueError):
                    changed=copy.deepcopy(sample)
                    if key=='번호판독상태':del changed[key]
                    else:changed[key]=11.0
                    validate_collection_label(changed,record,item,items,expected)

    def test_sticker_on_second_camera_prevents_publish_of_both_empty_references(self):
        with TemporaryDirectory() as folder:
            views={k:EmptyView(r.packet,r.roi) for k,r in self.frames.items()}
            with patch('empty_pair.disc_evidence',side_effect=[([],()),(['A-08'],())]),self.assertRaisesRegex(ValueError,'카메라 B A-08'):
                store_empty_pair(Path(folder),views,{'physical_sticker_set':'round-10-99-v1'})
            self.assertFalse(list(Path(folder).rglob('reference.json')))


class ConsoleState(unittest.TestCase):
    def test_alignment_hold_shows_current_frame_and_failure_reason(self):
        app=Mock();app.registration_views={};app.last_labels={k:Mock() for k in 'AB'}
        frames={k:SimpleNamespace(sequence=314) for k in 'AB'}
        outcomes={k:PairHold('구도 이동') for k in 'AB'}
        self.assertTrue(has_registration_view(outcomes))
        DualDashboard.show_partial_registration(app,frames,outcomes,{'channels':{k:{'lot':k} for k in 'AB'}},True)
        self.assertIs(app.registration_views['A'].packet,frames['A'])
        self.assertEqual(app.registration_views['A'].roi['spaces'],[])
        self.assertIn('구도 이동',app.fill_rows.call_args_list[0].args[3]['A-01'][0])

    def test_stopping_reference_write_keeps_reference_without_restarting_capture(self):
        app=Mock();app.empty_start_cancelled=False;app.extra=('empty_saved',Mock());app.running=False
        app.frames={k:SimpleNamespace(serial=k,stream_epoch=1) for k in 'AB'}
        app.hub.get_latest_frame.side_effect=app.frames.get
        app.last_labels={k:Mock() for k in 'AB'}
        app.auto_after_registration.get.return_value=True
        views={k:({'spaces':[],'lot_id':k},{'epoch':1,'empty_board':{'시리얼':k}}) for k in 'AB'}
        DualDashboard.stop(app)
        DualDashboard.handle_extra(app,'empty_saved',views)
        self.assertIs(app.empty_references,views)
        app.activate.assert_not_called()
