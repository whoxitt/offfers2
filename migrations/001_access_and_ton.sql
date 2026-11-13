-- Safe additive migration for the temporary access / TON payment subsystem.
-- The bot also applies these statements idempotently during Database.init_schema().
CREATE TABLE IF NOT EXISTS access_subscriptions (
  user_id BIGINT PRIMARY KEY, username TEXT, granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ, is_permanent BOOLEAN NOT NULL DEFAULT FALSE,
  source TEXT NOT NULL DEFAULT 'payment', notified_for_expiry TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS access_links (
  secondary_user_id BIGINT PRIMARY KEY, primary_user_id BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS access_link_requests (
  id BIGSERIAL PRIMARY KEY, primary_user_id BIGINT NOT NULL, secondary_user_id BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), status TEXT NOT NULL DEFAULT 'pending',
  UNIQUE(primary_user_id, secondary_user_id)
);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS tx_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_tx_hash_unique
  ON payments(tx_hash) WHERE tx_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_access_expiry
  ON access_subscriptions(expires_at) WHERE is_permanent=FALSE;
CREATE INDEX IF NOT EXISTS idx_access_links_primary
  ON access_links(primary_user_id) WHERE is_active=TRUE;
