PRAGMA journal_mode=WAL;

-- 1. 配置 KV
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 2. 观察名单
CREATE TABLE IF NOT EXISTS watchlist (
  symbol      TEXT PRIMARY KEY,
  added_at    TEXT NOT NULL,
  earnings_at TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1
);

-- 3. 扫描批次
CREATE TABLE IF NOT EXISTS scan_runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  provider        TEXT NOT NULL,
  symbol_count    INTEGER,
  candidate_count INTEGER,
  snapshot_path   TEXT,
  trigger         TEXT NOT NULL
);

-- 4. 候选合约（Short Put 快照）
CREATE TABLE IF NOT EXISTS candidates (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_run_id     INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
  symbol          TEXT NOT NULL,
  expiration      TEXT NOT NULL,
  strike          REAL NOT NULL,
  bid             REAL, ask REAL, mid REAL,
  spot            REAL,
  iv              REAL,
  iv_rank         REAL,
  delta           REAL, theta REAL, vega REAL, gamma REAL,
  dte             INTEGER,
  annualized_roi  REAL,
  pop             REAL,
  spread_pct      REAL,
  breakeven       REAL,
  margin_buffer   REAL,
  score           REAL,
  open_interest   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(scan_run_id, score DESC);

-- 5. 持仓
CREATE TABLE IF NOT EXISTS positions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol            TEXT NOT NULL,
  expiration        TEXT NOT NULL,
  strike            REAL NOT NULL,
  contracts         INTEGER NOT NULL,
  open_at           TEXT NOT NULL,
  open_premium      REAL NOT NULL,
  open_candidate_id INTEGER REFERENCES candidates(id),
  state             TEXT NOT NULL,
  close_at          TEXT,
  close_premium     REAL,
  close_reason      TEXT,
  realized_pnl      REAL,
  notes             TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_state ON positions(state);

-- 6. 雷达快照
CREATE TABLE IF NOT EXISTS radar_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id   INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  taken_at      TEXT NOT NULL,
  spot          REAL,
  current_mid   REAL,
  pnl_pct       REAL,
  delta         REAL,
  margin_buffer REAL,
  signals       TEXT
);
CREATE INDEX IF NOT EXISTS idx_radar_position ON radar_snapshots(position_id, taken_at);

-- 7. 事件中心
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  level      TEXT NOT NULL,
  category   TEXT NOT NULL,
  title      TEXT NOT NULL,
  payload    TEXT,
  ack_at     TEXT,
  acted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
