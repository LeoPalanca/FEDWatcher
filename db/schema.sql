CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    central_bank VARCHAR(10),
    doc_type VARCHAR(50),
    release_date TIMESTAMP,
    url TEXT UNIQUE,
    raw_text TEXT,
    processed BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS sentiment (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id),
    overall_tone VARCHAR(10),
    tone_score FLOAT,
    inflation_assessment TEXT,
    labor_market_assessment TEXT,
    forward_guidance TEXT,
    key_phrases TEXT[],
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_data (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sofr_rate FLOAT,
    ff_futures_implied FLOAT[],
    ois_1m FLOAT,
    ois_3m FLOAT,
    ois_6m FLOAT,
    ois_1y FLOAT,
    ois_2y FLOAT,
    us2y_yield FLOAT
);

CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id),
    market_snapshot_id INTEGER REFERENCES market_data(id),
    tone_implied_next_rate FLOAT,
    market_implied_next_rate FLOAT,
    divergence FLOAT,
    signal_direction VARCHAR(20),
    narrative TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);