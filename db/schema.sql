CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    central_bank TEXT,
    doc_type TEXT,
    release_date TEXT,
    url TEXT UNIQUE,
    raw_text TEXT,
    processed INTEGER DEFAULT 0,
    processed2 INTEGER DEFAULT 0,
    processed3 INTEGER DEFAULT 0,
    processed_w INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sentiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    overall_tone TEXT,
    tone_score REAL,
    inflation_assessment TEXT,
    labor_market_assessment TEXT,
    forward_guidance TEXT,
    key_phrases TEXT,
    confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS sentiment2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    overall_tone TEXT,
    tone_score REAL,
    inflation_assessment TEXT,
    labor_market_assessment TEXT,
    forward_guidance TEXT,
    key_phrases TEXT,
    confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS sentiment3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    overall_tone TEXT,
    tone_score REAL,
    inflation_assessment TEXT,
    labor_market_assessment TEXT,
    forward_guidance TEXT,
    key_phrases TEXT,
    confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS sentiment_w (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    forward_guidance_score REAL,
    inflation_score REAL,
    labor_market_score REAL,
    general_score REAL,
    policy_discussion_score REAL,
    tone_score REAL,
    overall_tone TEXT,
    inflation_assessment TEXT,
    labor_market_assessment TEXT,
    forward_guidance TEXT,
    key_phrases TEXT,
    confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS weights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type TEXT NOT NULL,
    dimension TEXT NOT NULL,
    weight REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(doc_type, dimension)
);

CREATE TABLE IF NOT EXISTS macro_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_month TEXT UNIQUE NOT NULL,
    core_cpi_index REAL,
    core_cpi_mom REAL,
    core_cpi_yoy REAL,
    unemployment_rate REAL,
    us2y_yield REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    policy_rate REAL
);

CREATE TABLE IF NOT EXISTS market_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    sofr_rate REAL,
    ff_futures_implied TEXT,
    ois_1m REAL,
    ois_3m REAL,
    ois_6m REAL,
    ois_1y REAL,
    ois_2y REAL,
    us2y_yield REAL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    market_snapshot_id INTEGER,
    smoothed_tone REAL,
    tone_implied_next_rate REAL,
    market_implied_next_rate REAL,
    divergence REAL,
    signal_direction TEXT,
    market_verdict TEXT,
    narrative TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id),
    FOREIGN KEY (market_snapshot_id) REFERENCES market_data(id)
);
