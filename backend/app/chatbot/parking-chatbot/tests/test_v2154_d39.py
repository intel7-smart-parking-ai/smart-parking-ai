"""Approved D-39 data migration keeps layout, age and API identity semantics."""
from copy import deepcopy
import unittest

from chatbot import config, parser
from chatbot.adapters import MockAdapter
from chatbot.catalog import Catalog


EV = {'23', '42', '57', '85'}
ACCESSIBLE = {'14', '32', '55', '75'}
GENERAL = {'10','11','12','20','21','22','24','30','33','34','40','41','43','44','58','88'}


class D39Tests(unittest.TestCase):
    def test_complete_sticker_assignment_and_unrestricted_parser(self):
        types = config.catalog.settings['mock_vehicle_types']
        self.assertEqual(set(types), EV | ACCESSIBLE | GENERAL)
        for kind, numbers in [('ev', EV), ('accessible', ACCESSIBLE), ('general', GENERAL)]:
            self.assertEqual({n for n,t in types.items() if t == kind}, numbers)
        self.assertEqual(set(config.catalog.settings['ev_vehicles']), EV)
        self.assertTrue(all(not {'6','9'} & set(n) for n in types))
        self.assertIsNone(config.catalog.settings['assigned_car_ids'])
        self.assertEqual(parser.normalize_car_id('17'), '17')
        self.assertEqual(parser.normalize_car_id('99'), '99')

    def test_initial_placement_and_elapsed_minutes_are_preserved(self):
        expected = {
            'A': [('11',1,95),('12',2,40),('23',3,12),('40',5,150),('41',6,33),('33',7,58),('43',8,20)],
            'B': [('44',1,70),('20',2,25),('42',3,110),('22',4,15),('34',5,45),('24',6,80),('58',7,5),('88',8,63)],
            'C': [('21',7,28),('57',11,140)], 'D': [('32',2,48),('30',9,18)]}
        actual = {lot: [tuple(row) for row in data['mock_cars']] for lot,data in config.catalog.lots().items()}
        self.assertEqual(actual, expected)
        mock = MockAdapter()
        self.assertEqual(len(mock.cars), 19)
        self.assertEqual(len(mock._full_occupied['B']), 8)
        self.assertEqual(len(mock._occupied_spots('B')), 16)
        self.assertTrue(set(mock.cars) <= EV | ACCESSIBLE | GENERAL)
        mock.clear_demo()
        self.assertEqual(len(mock._occupied_spots('B')), 16)
        self.assertEqual(mock.demo_state()['demo_occupied'], {})

    def test_mock_types_are_consistent_in_location_and_list(self):
        mock = MockAdapter()
        rows = {r['car_id']:r for r in mock.list_cars()}
        for car, kind in [('23','ev'),('32','accessible'),('11','general')]:
            self.assertEqual(mock.find_car(car)['vehicle_type'], kind)
            self.assertEqual(rows[car]['vehicle_type'], kind)
        self.assertIsNone(mock.find_car('17'))
        self.assertIsNone(mock.find_car('85'))  # Assigned, not initially parked.

    def test_invalid_type_and_inconsistent_ev_metadata_are_rejected(self):
        for change in ('type','ev'):
            data = deepcopy(config.catalog.settings)
            if change == 'type': data['mock_vehicle_types']['32'] = 'invented'
            else: data['mock_vehicle_types']['32'] = 'ev'
            with self.subTest(change=change), self.assertRaises(ValueError):
                Catalog(data)
