"""Round 37: OCR paths in assets.local.json resolve against the camera folder (classroom PC)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web_live import assets


class OcrPathTests(unittest.TestCase):
    def settings_with(self, data, env=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'assets.local.json').write_text(json.dumps(data), encoding='utf-8')
            clean = {k: v for k, v in os.environ.items() if k not in ('V53_OCR_PYTHON', 'V53_OCR_MODELS')}
            with patch.object(assets, 'ROOT', root), patch.dict(os.environ, {**clean, **(env or {})}, clear=True):
                return root.absolute(), assets.settings()

    def test_relative_forward_slash_paths_become_absolute(self):
        root, cfg = self.settings_with({'ocr_python': '.venv-ocr/Scripts/python.exe', 'ocr_models': 'ocr_models'})
        self.assertEqual(Path(cfg['ocr_python']), root / '.venv-ocr' / 'Scripts' / 'python.exe')
        self.assertTrue(Path(cfg['ocr_python']).is_absolute())
        self.assertEqual(Path(cfg['ocr_models']), root / 'ocr_models')

    def test_empty_and_absolute_values_are_unchanged(self):
        absolute = str(Path(tempfile.gettempdir()).resolve() / 'ocr' / 'python.exe')
        _, cfg = self.settings_with({'ocr_python': absolute, 'ocr_models': ''})
        self.assertEqual(cfg['ocr_python'], absolute)
        self.assertEqual(cfg['ocr_models'], '')

    def test_environment_override_still_wins(self):
        _, cfg = self.settings_with({'ocr_python': '.venv-ocr/Scripts/python.exe'},
                                    env={'V53_OCR_PYTHON': 'C:/custom/python.exe', 'V53_OCR_MODELS': 'D:/m'})
        self.assertEqual(cfg['ocr_python'], 'C:/custom/python.exe')
        self.assertEqual(cfg['ocr_models'], 'D:/m')

    def test_missing_file_keeps_ocr_off(self):
        with tempfile.TemporaryDirectory() as folder:
            clean = {k: v for k, v in os.environ.items() if k not in ('V53_OCR_PYTHON', 'V53_OCR_MODELS')}
            with patch.object(assets, 'ROOT', Path(folder)), patch.dict(os.environ, clean, clear=True):
                cfg = assets.settings()
        self.assertEqual((cfg['ocr_python'], cfg['ocr_models']), ('', ''))


if __name__ == '__main__':
    unittest.main()
