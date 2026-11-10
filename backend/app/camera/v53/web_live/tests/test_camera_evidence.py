import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from web_live.number_reader import NumberReader
from web_live.pipeline import ParkingPipeline, frame_quality


class CameraEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = ParkingPipeline.__new__(ParkingPipeline)
        self.pipeline.numbers = NumberReader()
        self.pipeline.registry = Mock()
        self.pipeline.registry.locate.return_value = None
        self.pipeline.detector = Mock()
        self.pipeline.confirmed = {'1': object(), '2': object()}
        self.pipeline.baselines = {'1': object(), '2': object()}
        self.pipeline.search_order = {'1': {'lot_id': 'B'}}
        for channel in ('1', '2'):
            self.pipeline.numbers.cache[(channel, 'old')] = object()

    def analyze(self, image):
        with patch('web_live.pipeline.annotate', return_value='preview'):
            return self.pipeline._analyze(image, '1', 1, 2, '2026-11-07T02:00:00+00:00', 'auto', .85, time.perf_counter())

    def test_extreme_frames_do_not_run_models_or_create_observations(self):
        for level, state in ((14, 'dark'), (250, 'overexposed')):
            with self.subTest(level=level):
                result = self.analyze(np.full((96, 160, 3), level, np.uint8))
                self.assertEqual(result['frame_quality']['state'], state)
                self.assertFalse(result['registration_ok'])
                self.assertFalse(result['roi_confirmed'])
                self.assertFalse(result['spaces'])
                self.assertFalse(result['detections'])
                self.assertEqual(result['detector_ms'], 0)
        self.pipeline.detector.infer_bgr.assert_not_called()
        self.pipeline.registry.locate.assert_not_called()

    def test_sticker_without_board_clears_only_lost_channels_number_evidence(self):
        detector = self.pipeline.detector.infer_bgr
        detector.return_value = {'detections': [{'confidence': .91, 'sticker_color': 'blue'}], 'inference_ms': 1.0}
        image = np.full((96, 160, 3), 90, np.uint8)
        first = self.analyze(image)
        self.assertIn('스티커 1개 검출', first['note'])
        self.assertFalse(first['spaces'])
        self.assertEqual(set(self.pipeline.confirmed), {'2'})
        self.assertEqual(set(self.pipeline.baselines), {'2'})
        self.assertEqual(set(self.pipeline.numbers.cache), {('2', 'old')})
        generation = self.pipeline.numbers.camera_generations['1']
        self.analyze(image)
        self.assertEqual(self.pipeline.numbers.camera_generations['1'], generation)

    def test_mixed_bright_and_dark_scene_is_still_analyzed(self):
        image = np.zeros((180, 320, 3), np.uint8)
        image[75:105, 140:180] = 250
        self.assertEqual(frame_quality(image)['state'], 'inspect')

    def test_clipped_slot_on_approved_board_never_reads_number_or_emits_empty(self):
        image = np.full((96, 160, 3), 100, np.uint8)
        poly = np.float32([[10,10], [70,10], [70,85], [10,85]])
        self.pipeline.registry.locate.return_value = dict(lot_id='A', outline=poly, matrix=np.eye(3),
            inliers=30, residual_px=.5, line_support=.9, polygons={'B-01':poly}, clipped_spaces=['B-01'])
        self.pipeline.registry.boards = {'A':{'spaces':[{'space_id':'B-01'}]}}
        self.pipeline.confirmed['1'] = (1,'A',poly)
        self.pipeline.baselines['1'] = dict(epoch=1,lot='A',outline=poly,crops={})
        self.pipeline.detector.infer_bgr.return_value = dict(inference_ms=1, detections=[
            dict(confidence=.95,sticker_color='green',vehicle_type_from_sticker='ev',bbox_xyxy=[25,25,45,45])])
        self.pipeline.occupancy = Mock()
        self.pipeline.number_model = Mock()
        result = self.analyze(image)
        self.assertTrue(result['roi_confirmed'])
        row, = result['spaces']
        self.assertEqual(row['quality_reason'], '칸 일부가 화면 밖')
        self.assertIsNone(row['car_id'])
        self.assertFalse(row['sendable'])
        self.assertRaises(ValueError, self.pipeline.validate_empty, result)
        self.pipeline.occupancy.scores.assert_not_called()
        self.pipeline.number_model.scores.assert_not_called()
