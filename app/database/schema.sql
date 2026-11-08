CREATE TABLE IF NOT EXISTS metrics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,   -- UTC ISO 8601
    cpu_usage        REAL,
    memory_usage     REAL,
    disk_usage       REAL,
    network_sent     INTEGER,
    network_received INTEGER
);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    metric      TEXT NOT NULL,           -- cpu / memory / disk / service
    severity    TEXT NOT NULL CHECK (severity IN ('WARNING', 'CRITICAL')),
    message     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'ACKNOWLEDGED', 'RESOLVED')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_status    ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp);

CREATE TABLE IF NOT EXISTS services (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name  TEXT NOT NULL UNIQUE,
    enabled       INTEGER NOT NULL DEFAULT 1,
    auto_recovery INTEGER NOT NULL DEFAULT 0,   -- disabled by default
    last_status   TEXT,
    last_checked  TEXT,
    last_status_change TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recovery_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    action       TEXT NOT NULL,
    result       TEXT NOT NULL CHECK (result IN ('SUCCESS', 'FAILURE')),
    message      TEXT
);
CREATE INDEX IF NOT EXISTS idx_recovery_timestamp ON recovery_logs(timestamp);

-- At most ONE open incident per metric. The database itself prevents duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_one_open_per_metric
    ON alerts(metric) WHERE status IN ('ACTIVE', 'ACKNOWLEDGED');

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL            -- JSON-encoded
);
