"""Schema for named benchmark runs."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmarks (
    name TEXT PRIMARY KEY NOT NULL CHECK(length(trim(name)) > 0),
    created_at TEXT NOT NULL,
    schedule TEXT
);
CREATE TABLE IF NOT EXISTS benchmark_config (
    name TEXT NOT NULL REFERENCES benchmarks(name),
    suite TEXT NOT NULL,
    option TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (name, suite, option)
);

CREATE TABLE IF NOT EXISTS results (
    name TEXT NOT NULL REFERENCES benchmarks(name),
    suite TEXT NOT NULL,
    key TEXT NOT NULL,
    config_key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (name, suite, key)
);

CREATE TABLE IF NOT EXISTS failures (
    name TEXT NOT NULL REFERENCES benchmarks(name),
    id INTEGER PRIMARY KEY,
    suite TEXT NOT NULL,
    key TEXT,
    config_key TEXT,
    configuration TEXT,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS failures_suite_configuration ON failures(name, suite, configuration);
CREATE INDEX IF NOT EXISTS failures_suite_key ON failures(name, suite, key);

CREATE TABLE IF NOT EXISTS skipped (
    name TEXT NOT NULL REFERENCES benchmarks(name),
    id INTEGER PRIMARY KEY,
    suite TEXT NOT NULL,
    key TEXT,
    config_key TEXT,
    configuration TEXT,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS skipped_suite_configuration ON skipped(name, suite, configuration);

CREATE TABLE IF NOT EXISTS metadata (
    name TEXT NOT NULL REFERENCES benchmarks(name),
    suite TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (name, suite)
);

CREATE TABLE IF NOT EXISTS profiles (
    name TEXT NOT NULL REFERENCES benchmarks(name),
    suite TEXT NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    content BLOB NOT NULL,
    PRIMARY KEY (name, suite, key, kind)
);

CREATE TABLE IF NOT EXISTS reports (
    name TEXT NOT NULL REFERENCES benchmarks(name),
    suite TEXT NOT NULL,
    content BLOB NOT NULL,
    PRIMARY KEY (name, suite)
);

CREATE TABLE IF NOT EXISTS config (
    suite TEXT NOT NULL,
    option TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (suite, option)
);

CREATE TABLE IF NOT EXISTS schedule_config (
    suite TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    samples INTEGER NOT NULL,
    amend INTEGER NOT NULL,
    skip_failed INTEGER NOT NULL
);
"""
