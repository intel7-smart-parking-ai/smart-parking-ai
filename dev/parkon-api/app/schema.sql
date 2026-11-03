CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS parking_lots(lot_id text PRIMARY KEY, name text NOT NULL, type text NOT NULL CHECK(type IN ('real','virtual')));
CREATE TABLE IF NOT EXISTS rate_policies(lot_id text PRIMARY KEY REFERENCES parking_lots, policy jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS parking_spaces(space_id text PRIMARY KEY, lot_id text NOT NULL REFERENCES parking_lots, zone text NOT NULL, space_type text NOT NULL, status text NOT NULL, car_id text);
CREATE TABLE IF NOT EXISTS parking_sessions(session_id text PRIMARY KEY, lot_id text NOT NULL REFERENCES parking_lots, space_id text NOT NULL REFERENCES parking_spaces, car_id text NOT NULL, entry_at timestamptz NOT NULL, status text NOT NULL DEFAULT 'parked');
CREATE TABLE IF NOT EXISTS mock_payments(payment_id text PRIMARY KEY, session_id text NOT NULL, lot_id text NOT NULL REFERENCES parking_lots, car_id text NOT NULL, amount_krw integer NOT NULL CHECK(amount_krw >= 0), status text NOT NULL CHECK(status IN ('completed','canceled')), idempotency_key text UNIQUE NOT NULL, created_at timestamptz NOT NULL, canceled_at timestamptz);
CREATE UNIQUE INDEX IF NOT EXISTS one_completed_payment_per_session ON mock_payments(session_id) WHERE status='completed';
CREATE TABLE IF NOT EXISTS alerts(alert_id text PRIMARY KEY, lot_id text NOT NULL REFERENCES parking_lots, space_id text NOT NULL REFERENCES parking_spaces, type text NOT NULL, status text NOT NULL CHECK(status IN ('active','acknowledged','resolved')), detected_at timestamptz NOT NULL, acknowledged_at timestamptz, resolved_at timestamptz);
INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS demo_vehicles(car_id text PRIMARY KEY, vehicle_number text UNIQUE NOT NULL CHECK(vehicle_number ~ '^[0-9]{4}$'));
INSERT INTO schema_migrations(version) VALUES (2) ON CONFLICT DO NOTHING;
ALTER TABLE demo_vehicles ADD COLUMN IF NOT EXISTS plate_number text;
CREATE UNIQUE INDEX IF NOT EXISTS demo_vehicle_full_plate ON demo_vehicles(plate_number);
INSERT INTO schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING;
ALTER TABLE mock_payments ADD COLUMN IF NOT EXISTS payment_method text NOT NULL DEFAULT 'credit_card' CHECK(payment_method IN ('credit_card','naver_pay','kakao_pay'));
INSERT INTO schema_migrations(version) VALUES (4) ON CONFLICT DO NOTHING;
ALTER TABLE parking_lots ADD COLUMN IF NOT EXISTS layout_type text;
ALTER TABLE parking_spaces ADD COLUMN IF NOT EXISTS bay_label text;
INSERT INTO schema_migrations(version) VALUES (5) ON CONFLICT DO NOTHING;

-- Upgrade existing demo rows without deleting payments or changing their amounts.
ALTER TABLE mock_payments DROP CONSTRAINT IF EXISTS mock_payments_status_check;
UPDATE mock_payments SET status='canceled' WHERE status='cancelled';
ALTER TABLE mock_payments ADD CONSTRAINT mock_payments_status_check CHECK(status IN ('completed','canceled'));
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='mock_payments' AND column_name='cancelled_at') THEN
  ALTER TABLE mock_payments RENAME COLUMN cancelled_at TO canceled_at;
 END IF;
END $$;
ALTER TABLE mock_payments DROP CONSTRAINT IF EXISTS mock_payments_payment_method_check;
UPDATE mock_payments SET payment_method=CASE payment_method WHEN 'credit_card' THEN 'MOCK_CARD' WHEN 'naver_pay' THEN 'MOCK_EASY_PAY' WHEN 'kakao_pay' THEN 'MOCK_EASY_PAY' ELSE payment_method END;
ALTER TABLE mock_payments ALTER COLUMN payment_method SET DEFAULT 'MOCK_CARD';
ALTER TABLE mock_payments ADD CONSTRAINT mock_payments_payment_method_check CHECK(payment_method IN ('MOCK_CARD','MOCK_EASY_PAY','MOCK_ACCOUNT'));
INSERT INTO schema_migrations(version) VALUES (6) ON CONFLICT DO NOTHING;

-- Unknown LED observation locations must not be assigned to an arbitrary bay.
ALTER TABLE alerts ALTER COLUMN space_id DROP NOT NULL;
UPDATE alerts SET space_id=NULL,type='mock_fire_led_observed' WHERE type='mock_fire_led';
