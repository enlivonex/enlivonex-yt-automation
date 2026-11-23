CREATE TABLE IF NOT EXISTS access_requests (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL COLLATE NOCASE UNIQUE,
  pc_type TEXT NOT NULL CHECK (
    pc_type IN ('dedicated-gpu', 'integrated-gpu', 'unsure')
  ),
  status TEXT NOT NULL CHECK (
    status IN ('requested', 'approved', 'redeemed')
  ),
  created_at TEXT NOT NULL,
  payment_verified_at TEXT,
  activation_hash TEXT,
  activation_expires_at TEXT,
  redeemed_at TEXT,
  download_token_hash TEXT,
  download_token_expires_at TEXT
);

CREATE INDEX IF NOT EXISTS access_requests_status_created
ON access_requests (status, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS access_requests_activation_hash
ON access_requests (activation_hash)
WHERE activation_hash IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS access_requests_download_token_hash
ON access_requests (download_token_hash)
WHERE download_token_hash IS NOT NULL;
