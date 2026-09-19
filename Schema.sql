-- schema.sql
--
-- Three tables and nothing clever.
--
--   analyses     every reading the system has produced, model or corrected
--   corrections  what an expert changed, and why
--   preferences  small key/value store, currently just the learned/default switch
--
-- A correction points at the analysis it corrected, so the pair (what the model
-- said, what the expert said instead) is always recoverable. That pair is the
-- training signal; the corrected output on its own is not.

CREATE TABLE IF NOT EXISTS analyses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT    NOT NULL,

    -- normalised hash of input_text, for exact-repeat lookup
    fingerprint  TEXT    NOT NULL,

    -- first line or so of the lyrics, for listing screens
    excerpt      TEXT    NOT NULL,

    -- the composed text actually sent to the analyser, metadata included
    input_text   TEXT    NOT NULL,

    -- the analyser's JSON verdict, serialised
    output       TEXT    NOT NULL,

    -- 'model'      fresh model call
    -- 'correction' served from a stored expert correction
    source       TEXT    NOT NULL DEFAULT 'model',

    -- when learned guidance shaped this reading, the corrections it leaned on
    learned_from TEXT
);

CREATE INDEX IF NOT EXISTS analyses_fingerprint
    ON analyses (fingerprint);

CREATE INDEX IF NOT EXISTS analyses_created
    ON analyses (created_at DESC);


CREATE TABLE IF NOT EXISTS corrections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,

    analysis_id   INTEGER NOT NULL
                  REFERENCES analyses (id) ON DELETE CASCADE,

    -- copied from the analysis so matching never needs a join
    fingerprint   TEXT    NOT NULL,
    excerpt       TEXT    NOT NULL,

    -- what the model said, what the expert said instead, and the fields
    -- that actually differ between the two
    original      TEXT    NOT NULL,
    corrected     TEXT    NOT NULL,
    changed       TEXT    NOT NULL,

    editor        TEXT,

    -- the reviewer's reasoning. This is the most valuable column in the
    -- database: a changed number teaches far less than the sentence saying why.
    note          TEXT,

    -- embedding of the corrected song's input text, JSON array of floats,
    -- null when the embedding call was unavailable at the time
    embedding     TEXT,

    -- retired corrections stay on the record but stop teaching
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS corrections_active
    ON corrections (active, created_at DESC);

CREATE INDEX IF NOT EXISTS corrections_fingerprint
    ON corrections (fingerprint, active);


CREATE TABLE IF NOT EXISTS preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);