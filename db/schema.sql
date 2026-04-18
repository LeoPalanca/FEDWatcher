CREATE TABLE IF NOT EXISTS documents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    central_bank VARCHAR(10),
    doc_type VARCHAR(50),
    release_date DATETIME,
    url TEXT,
    raw_text LONGTEXT,
    processed BOOLEAN DEFAULT FALSE,
    UNIQUE KEY unique_url (url(255))
);

CREATE TABLE IF NOT EXISTS sentiment (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT,
    overall_tone VARCHAR(10),
    tone_score FLOAT,
    inflation_assessment TEXT,
    labor_market_assessment TEXT,
    forward_guidance TEXT,
    key_phrases JSON,
    confidence FLOAT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS market_data (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    sofr_rate FLOAT,
    ff_futures_implied JSON,
    ois_1m FLOAT,
    ois_3m FLOAT,
    ois_6m FLOAT,
    ois_1y FLOAT,
    ois_2y FLOAT,
    us2y_yield FLOAT
);

CREATE TABLE IF NOT EXISTS signals (
    id INT AUTO_INCREMENT PRIMARY KEY,
    document_id INT,
    market_snapshot_id INT,
    tone_implied_next_rate FLOAT,
    market_implied_next_rate FLOAT,
    divergence FLOAT,
    signal_direction VARCHAR(20),
    narrative TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id),
    FOREIGN KEY (market_snapshot_id) REFERENCES market_data(id)
);