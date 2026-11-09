import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from backend.app.simulation.charging import telemetry
from backend.app.runtime import create_runtime_app


class VirtualChargingTests(unittest.TestCase):
    def test_cycle_is_separate_from_occupancy_and_resets_on_exit(self):
        start = datetime(2026, 11, 8, tzinfo=timezone.utc)
        row = dict(space_type='ev', status='occupied', vehicle_type='ev', session_id='s', entry_at=start)
        for seconds, state, power in ((0,'connected',0),(15,'charging',7),(134,'charging',7),(135,'completed',0)):
            with self.subTest(seconds=seconds):
                value = telemetry(row, start+timedelta(seconds=seconds), True)
                self.assertEqual((value['status'], value['power_kw']), (state,power))
                self.assertTrue(value['is_mock'])
                self.assertEqual(value['estimated_remaining_seconds'],max(0,135-seconds))
        self.assertEqual(telemetry({**row,'status':'empty'},start,True)['status'],'available')
        self.assertEqual(telemetry({**row,'vehicle_type':'general'},start,True)['status'],'not_connected')
        self.assertEqual(telemetry({**row,'status':'uncertain'},start,True)['status'],'unknown')
        self.assertEqual(telemetry(row,start,False)['source'],'not_connected')
        self.assertIsNone(telemetry(row,start,False)['estimated_remaining_seconds'])

    def test_both_virtual_lots_expose_charger_telemetry_in_spaces_api(self):
        with TemporaryDirectory() as folder:
            app = create_runtime_app(token='charger-local-test-token',database=Path(folder)/'db.sqlite',mode='demo')
            with TestClient(app) as client:
                for lot in ('C','D'):
                    entered=client.post('/api/v1/simulation/'+lot+'/entry',json={'vehicle_type':'ev'})
                    self.assertEqual(entered.status_code,200,entered.text)
                    rows=client.get('/api/v1/parking-lots/'+lot+'/spaces').json()['items']
                    parked=next(r for r in rows if r['space_type']=='ev' and r['status']=='occupied')
                    self.assertEqual(parked['charging']['status'],'connected')
                    self.assertTrue(parked['charging']['is_mock'])
                    self.assertIsNone(next(r for r in rows if r['space_type']=='general')['charging'])
                rows=client.get('/api/v1/parking-lots/A/spaces').json()['items']
                self.assertTrue(all(r['charging']['source']=='not_connected' for r in rows if r['space_type']=='ev'))

if __name__=='__main__':
    unittest.main()
