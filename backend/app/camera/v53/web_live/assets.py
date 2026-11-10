"""Private assets stay outside the source distribution."""
from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parents[1]


def settings():
    path = ROOT / 'assets.local.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    defaults = {
        'detector': '../geti_model_test_server/models/58d2c49a/model.xml',
        'team_directory': '../verification_v53/team_sources/parking_combo_align_feedback',
        'legacy_board_directory': '../smart_parking_v52/configs/board_print',
        'landmark_directory': 'runtime/board_landmarks',
        'printed_numbers': 'models/d54_print.npz',
        'fire_led_model': 'models/red_led.npz',
        'glow_model': 'models/red_glow.npz',
        'empty_bays': 'models/empty_bays.npz',
        'empty_bays_lit': 'models/empty_bays_lit.npz',
        'reviewed_empty_bays': 'runtime/reviewed_empty_bays.npz',
    }
    defaults.update(data)
    for key in ('detector', 'team_directory', 'legacy_board_directory', 'landmark_directory', 'printed_numbers', 'fire_led_model','glow_model','empty_bays','empty_bays_lit','reviewed_empty_bays'):
        path = Path(defaults[key])
        defaults[key] = (path if path.is_absolute() else ROOT/path).resolve()
    # A relative OCR path in assets.local.json is relative to this folder, like the
    # other assets. Windows cannot start '.venv-ocr/Scripts/python.exe' as written
    # (FileNotFoundError, OCR silently off), so pass an absolute path instead.
    for key in ('ocr_python', 'ocr_models'):
        value = data.get(key, '')
        if isinstance(value, str) and value and not Path(value).is_absolute():
            # abspath, not resolve(): a Linux venv python is a symlink to the base
            # interpreter, and following it would start Python outside the venv.
            value = os.path.abspath(ROOT / value)
        defaults[key] = os.environ.get('V53_' + key.upper(), value)
    return defaults
