#!/bin/bash

cd /home/programming/FEDWatcher || exit 1

sqlite3 fedwatcher.db <<'SQL'
.headers on
.mode column

SELECT
    date(substr(d.release_date, 1, 10)) AS release_date,
    d.id AS document_id,
    d.doc_type,
    s.tone_score AS sentiment_tone_score,
    s2.tone_score AS sentiment2_tone_score,
    s3.tone_score AS sentiment3_tone_score,
    md.us2y_yield AS macro_us2y_yield
FROM documents d
LEFT JOIN sentiment s
    ON s.document_id = d.id
LEFT JOIN sentiment2 s2
    ON s2.document_id = d.id
LEFT JOIN sentiment3 s3
    ON s3.document_id = d.id
LEFT JOIN macro_data md
    ON md.observation_month = substr(d.release_date, 1, 7)
WHERE s.tone_score IS NOT NULL
   OR s2.tone_score IS NOT NULL
   OR s3.tone_score IS NOT NULL
ORDER BY date(substr(d.release_date, 1, 10)) ASC, d.id ASC;
SQL
