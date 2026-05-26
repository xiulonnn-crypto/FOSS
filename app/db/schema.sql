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
  enabled     INTEGER NOT NULL DEFAULT 1,
  pool_status TEXT,
  tags        TEXT,
  notes       TEXT,
  last_scanned_at      TEXT,
  last_candidate_count INTEGER,
  last_pool_summary    TEXT
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
  trigger         TEXT NOT NULL,
  diagnostics     TEXT
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
  open_interest   INTEGER,
  quality_grade   TEXT,
  quality_score   INTEGER,
  quality_flags   TEXT,
  quote_age_seconds INTEGER,
  greeks_source   TEXT,
  iv_rank_source  TEXT,
  state_features  TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(scan_run_id, score DESC);

-- 5. 合约池（Short Put 最新状态）
CREATE TABLE IF NOT EXISTS option_pool (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol              TEXT NOT NULL,
  expiration          TEXT NOT NULL,
  strike              REAL NOT NULL,
  right               TEXT NOT NULL DEFAULT 'P',
  bid                 REAL,
  ask                 REAL,
  mid                 REAL,
  spot                REAL,
  iv                  REAL,
  iv_rank             REAL,
  delta               REAL,
  theta               REAL,
  vega                REAL,
  gamma               REAL,
  dte                 INTEGER,
  annualized_roi      REAL,
  pop                 REAL,
  spread_pct          REAL,
  breakeven           REAL,
  margin_buffer       REAL,
  score               REAL,
  open_interest       INTEGER,
  quality_grade       TEXT,
  quality_score       INTEGER,
  quality_flags       TEXT,
  quote_age_seconds   INTEGER,
  greeks_source       TEXT,
  iv_rank_source      TEXT,
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  last_scan_run_id    INTEGER REFERENCES scan_runs(id) ON DELETE SET NULL,
  latest_candidate_id INTEGER REFERENCES candidates(id) ON DELETE SET NULL,
  missed_scan_count   INTEGER NOT NULL DEFAULT 0,
  status              TEXT NOT NULL DEFAULT 'NEW',
  latest_entry_signal_id INTEGER,
  entry_signal_status TEXT,
  entry_signal_score  INTEGER,
  entry_signal_summary TEXT,
  entry_signal_generated_at TEXT,
  entry_signal_payload TEXT,
  state_features      TEXT,
  UNIQUE(symbol, expiration, strike, right)
);
CREATE INDEX IF NOT EXISTS idx_option_pool_symbol_status ON option_pool(symbol, status);
CREATE INDEX IF NOT EXISTS idx_option_pool_status_dte ON option_pool(status, dte);
CREATE INDEX IF NOT EXISTS idx_option_pool_quality ON option_pool(quality_grade, score DESC);
CREATE INDEX IF NOT EXISTS idx_option_pool_entry_signal ON option_pool(entry_signal_status, entry_signal_score DESC);

-- 6. 开仓信号快照（智能开仓决策卡）
CREATE TABLE IF NOT EXISTS entry_signals (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  option_pool_id  INTEGER REFERENCES option_pool(id) ON DELETE CASCADE,
  scan_run_id     INTEGER REFERENCES scan_runs(id) ON DELETE SET NULL,
  candidate_id    INTEGER REFERENCES candidates(id) ON DELETE SET NULL,
  symbol          TEXT,
  expiration      TEXT,
  strike          REAL,
  right           TEXT,
  status          TEXT NOT NULL,
  decision_score  INTEGER,
  summary         TEXT,
  metrics_json    TEXT,
  reasons_json    TEXT,
  blockers_json   TEXT,
  signal_json     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  is_latest       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_entry_signals_pool_latest ON entry_signals(option_pool_id, is_latest, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_entry_signals_status ON entry_signals(status, decision_score DESC);

-- 7. 期权观察池（用户意图与跟踪状态）
CREATE TABLE IF NOT EXISTS option_watchlist (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  option_pool_id       INTEGER NOT NULL REFERENCES option_pool(id) ON DELETE CASCADE,
  status               TEXT NOT NULL DEFAULT 'WATCHING',
  watch_reason         TEXT,
  ignore_reason        TEXT,
  target_premium       REAL,
  target_score         REAL,
  target_margin_buffer REAL,
  notes                TEXT,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  last_evaluated_at    TEXT,
  last_signal          TEXT
);
CREATE INDEX IF NOT EXISTS idx_option_watchlist_status ON option_watchlist(status);
CREATE INDEX IF NOT EXISTS idx_option_watchlist_pool ON option_watchlist(option_pool_id);

-- 8. 持仓
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
  notes             TEXT,
  latest_exit_signal_id INTEGER,
  exit_signal_action TEXT,
  exit_signal_severity TEXT,
  exit_signal_score  REAL,
  exit_signal_summary TEXT,
  exit_signal_generated_at TEXT,
  exit_signal_payload TEXT,
  close_signal_id   INTEGER,
  close_snapshot    TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_state ON positions(state);

-- 9. 雷达快照
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

-- 10. 持仓动作建议（智能平仓决策卡）
CREATE TABLE IF NOT EXISTS exit_signals (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id     INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  radar_snapshot_id INTEGER REFERENCES radar_snapshots(id) ON DELETE SET NULL,
  action          TEXT NOT NULL,
  severity        TEXT NOT NULL,
  urgency_score   REAL,
  suggested_close_reason TEXT,
  summary         TEXT,
  signal_json     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  is_latest       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_exit_signals_position_latest ON exit_signals(position_id, is_latest, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_exit_signals_event_dedup ON exit_signals(position_id, action, suggested_close_reason, severity);

-- 11. 市场 IV / Skew 快照（阶段一特征工程）
CREATE TABLE IF NOT EXISTS market_iv_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  as_of_date TEXT NOT NULL,
  iv30 REAL,
  atm_strike REAL,
  skew REAL,
  vix REAL,
  source TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(symbol, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_market_iv_snapshots_symbol_date ON market_iv_snapshots(symbol, as_of_date DESC);

-- 12. 候选/持仓统一特征快照
CREATE TABLE IF NOT EXISTS feature_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  as_of TEXT NOT NULL,
  features_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_feature_snapshots_entity ON feature_snapshots(entity_type, entity_id, as_of DESC);

-- 13. 持仓动作日志
CREATE TABLE IF NOT EXISTS position_action_logs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id     INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  exit_signal_id  INTEGER REFERENCES exit_signals(id) ON DELETE SET NULL,
  action_type     TEXT NOT NULL,
  reason          TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_action_logs_position ON position_action_logs(position_id, created_at DESC);

-- 14. 事件中心
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
