CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS parking_lots(lot_id text PRIMARY KEY, name text NOT NULL, type text NOT NULL CHECK(type IN ('real','virtual')));
CREATE TABLE IF NOT EXISTS rate_policies(lot_id text PRIMARY KEY REFERENCES parking_lots, policy jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS parking_spaces(space_id text PRIMARY KEY, lot_id text NOT NULL REFERENCES parking_lots, zone text NOT NULL, space_type text NOT NULL, status text NOT NULL, car_id text);
CREATE TABLE IF NOT EXISTS parking_sessions(session_id text PRIMARY KEY, lot_id text NOT NULL REFERENCES parking_lots, space_id text NOT NULL REFERENCES parking_spaces, car_id text NOT NULL, entry_at timestamptz NOT NULL, status text NOT NULL DEFAULT 'parked');
CREATE TABLE IF NOT EXISTS mock_payments(payment_id text PRIMARY KEY, session_id text NOT NULL, lot_id text NOT NULL REFERENCES parking_lots, car_id text NOT NULL, amount_krw integer NOT NULL CHECK(amount_krw >= 0), status text NOT NULL CHECK(status IN ('completed','cancelled')), idempotency_key text UNIQUE NOT NULL, created_at timestamptz NOT NULL, cancelled_at timestamptz);
CREATE UNIQUE INDEX IF NOT EXISTS one_completed_payment_per_session ON mock_payments(session_id) WHERE status='completed';
CREATE TABLE IF NOT EXISTS alerts(alert_id text PRIMARY KEY, lot_id text NOT NULL REFERENCES parking_lots, space_id text NOT NULL REFERENCES parking_spaces, type text NOT NULL, status text NOT NULL CHECK(status IN ('active','acknowledged','resolved')), detected_at timestamptz NOT NULL, acknowledged_at timestamptz, resolved_at timestamptz);
INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING;
