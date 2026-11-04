"""설비 설정값(facility) 규칙: null=미확인, true=있음, false=없음, 높이 제한 false=해당 없음."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from chatbot import config, engine as e, incidents
from chatbot.adapters import CachedAdapter, MockAdapter
from chatbot.catalog import Catalog


class FacilitySettingTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, 'data', CachedAdapter(MockAdapter())); p.start(); self.addCleanup(p.stop); e.reset_all()
        self.addCleanup(config.catalog.configure, deepcopy(config.catalog.settings))

    def briefing_with(self, facility):
        doc = deepcopy(config.catalog.settings); doc['lots'][0]['facility'] = facility
        config.catalog.configure(doc)
        e.data.set_alert(2, 'A', 6); e.current_alert()
        return incidents.list_public()[0]['briefing']['facility']

    def test_default_values_are_unconfirmed(self):
        self.assertEqual(self.briefing_with({'underground': None, 'height_limit_m': None, 'sprinkler': None}),
                         '지하: 미확인 / 높이 제한: 미확인 / 스프링클러: 미확인')

    def test_model_values_show_none_and_not_applicable(self):
        self.assertEqual(self.briefing_with({'underground': False, 'height_limit_m': False, 'sprinkler': False}),
                         '지하: 없음 / 높이 제한: 해당 없음 / 스프링클러: 없음')

    def test_real_building_values(self):
        self.assertEqual(self.briefing_with({'underground': True, 'height_limit_m': 2.1, 'sprinkler': True}),
                         '지하: 있음 / 높이 제한: 2.1m / 스프링클러: 있음')

    def test_invalid_values_are_rejected_with_lot_and_key(self):
        for facility in ({'sprinker': False}, {'underground': 'no'}, {'height_limit_m': True},
                         {'height_limit_m': 0}, {'height_limit_m': '2m'}):
            doc = deepcopy(config.catalog.settings); doc['lots'][0]['facility'] = facility
            with self.subTest(facility=facility), self.assertRaises(ValueError) as ctx:
                Catalog(doc)
            self.assertIn('A 주차장 facility', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
