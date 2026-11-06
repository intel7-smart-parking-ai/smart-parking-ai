import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import test_integration as fixture
from review import default_review, save_review, export_reviewed


class ImageExport(unittest.TestCase):
    def setup_record(self, root, approved=True):
        _, record = fixture.Contracts().fixture(root)
        rows = default_review(record)
        rows[0].update(occupancy='occupied', plate_text='49', review_status='approved' if approved else 'pending')
        save_review(root, record, rows, 'tester')
        return record

    def test_original_exact_and_preview_has_roi_and_separate_label_panel(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); record=self.setup_record(root)
            before=(root/record['raw_path']).read_bytes()
            target,count=export_reviewed(root)
            folder=target.with_suffix('.images')
            self.assertEqual(count,1)
            self.assertEqual((folder/'originals'/f"{record['image_id']}.png").read_bytes(),before)
            self.assertEqual((root/record['raw_path']).read_bytes(),before)
            with Image.open(folder/'previews'/f"{record['image_id']}.png") as im:
                self.assertGreater(im.height,100)
                self.assertEqual(im.getpixel((90,80)),(0,255,64))
            report=json.loads(target.with_suffix('.report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['preview_scope'],'human_reviewed_labels_not_automatic_ocr')
            self.assertEqual(len(report['exported_images']),1)

    def test_unapproved_not_exported_as_image(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.setup_record(root,False)
            target,count=export_reviewed(root)
            self.assertEqual(count,0)
            self.assertFalse(target.with_suffix('.images').exists())

    def test_image_write_failure_removes_incomplete_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.setup_record(root)
            with patch('PIL.Image.Image.save',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):export_reviewed(root)
            self.assertEqual(list((root/'exports').iterdir()),[])

    def test_repeated_exports_keep_previous_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.setup_record(root)
            first,_=export_reviewed(root); second,_=export_reviewed(root)
            self.assertNotEqual(first,second)
            self.assertTrue(first.with_suffix('.images').is_dir())
            self.assertTrue(second.with_suffix('.images').is_dir())
