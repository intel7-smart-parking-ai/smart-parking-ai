import sqlite3
import unittest
from datetime import datetime,timedelta,timezone
from backend.app.forecasting import record,backtest,predict_day,SCHEMA


class ForecastTests(unittest.TestCase):
    def test_future_values_never_change_predictions_at_origin(self):
        start=datetime(2026,8,1,tzinfo=timezone(timedelta(hours=9)))
        series={start+timedelta(hours=h):h%24 for h in range(56*24)}
        origin=start+timedelta(days=42)
        expected=predict_day(series,origin)
        changed={t:(999 if t>=origin else v) for t,v in series.items()}
        self.assertEqual(expected,predict_day(changed,origin))
        self.assertEqual(len(expected),24)

    def test_no_history_and_source_mixing_are_not_validated(self):
        self.assertEqual(backtest([])['status'],'insufficient_data')
        rows=[dict(lot_id=lot,bucket='2026-11-08T00:00:00+09:00',capacity=64,empty=2,reliable=1,source='vision',is_mock=0) for lot in ('C','D')]
        self.assertEqual(backtest(rows)['status'],'incompatible_data')

    def test_unobserved_spaces_never_become_training_evidence(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory=sqlite3.Row
            conn.executescript(SCHEMA)
            at=datetime.now(timezone.utc)
            record(conn,'A',[{'status':'empty','observed_at':None}],at)
            row=dict(conn.execute('SELECT * FROM vacancy_history').fetchone())
            self.assertEqual(row['reliable'],0)
            self.assertEqual(backtest([row])['status'],'insufficient_data')
            record(conn,'A',[{'status':'occupied','observed_at':at}],at)
            self.assertEqual(conn.execute('SELECT count(*) FROM vacancy_history').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT reliable,empty FROM vacancy_history').fetchone()[:],(1,0))

if __name__=='__main__':
    unittest.main()
