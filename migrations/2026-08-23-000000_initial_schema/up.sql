CREATE TYPE industry_type AS ENUM (
    'hollywood',
    'bollywood',
    'other'
);

CREATE TYPE credit_type AS ENUM ('actor', 'director');

CREATE TYPE ingestion_status AS ENUM ('running', 'completed', 'failed');

CREATE TABLE movies (
    movie_id BIGINT PRIMARY KEY,
    title TEXT NOT NULL,
    original_title TEXT,
    original_language VARCHAR(2),
    release_date DATE,
    overview TEXT NOT NULL,
    runtime_minutes INTEGER,
    vote_average NUMERIC(3,1),
    vote_count INTEGER NOT NULL DEFAULT 0,
    industry industry_type NOT NULL,
    CHECK (length(trim(title)) > 0),
    CHECK (length(trim(overview)) > 0),
    CHECK (runtime_minutes IS NULL OR runtime_minutes > 0),
    CHECK (vote_average IS NULL OR vote_average BETWEEN 0 AND 10),
    CHECK (vote_count >= 0)
);

CREATE TABLE genres (
    genre_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    CHECK (length(trim(name)) > 0)
);

CREATE TABLE movie_genres (
    movie_id BIGINT NOT NULL
        REFERENCES movies(movie_id) ON DELETE CASCADE,
    genre_id INTEGER NOT NULL
        REFERENCES genres(genre_id) ON DELETE RESTRICT,
    PRIMARY KEY (movie_id, genre_id)
);

CREATE TABLE people (
    person_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    CHECK (length(trim(name)) > 0)
);

CREATE TABLE movie_credits (
    credit_id TEXT PRIMARY KEY,
    movie_id BIGINT NOT NULL
        REFERENCES movies(movie_id) ON DELETE CASCADE,
    person_id BIGINT NOT NULL
        REFERENCES people(person_id) ON DELETE RESTRICT,
    credit_type credit_type NOT NULL,
    character_name TEXT,
    cast_order INTEGER,
    CHECK (cast_order IS NULL OR cast_order >= 0),
    CHECK (
        credit_type = 'actor'
        OR (character_name IS NULL AND cast_order IS NULL)
    )
);

CREATE TABLE countries (
    country_code VARCHAR(2) PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    CHECK (country_code = upper(country_code)),
    CHECK (length(country_code) = 2),
    CHECK (length(trim(name)) > 0)
);

CREATE TABLE movie_countries (
    movie_id BIGINT NOT NULL
        REFERENCES movies(movie_id) ON DELETE CASCADE,
    country_code VARCHAR(2) NOT NULL
        REFERENCES countries(country_code) ON DELETE RESTRICT,
    PRIMARY KEY (movie_id, country_code)
);

CREATE TABLE ingestion_runs (
    run_id UUID PRIMARY KEY,
    source_name TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    input_checksum TEXT NOT NULL,
    source_record_count INTEGER NOT NULL,
    shard_record_count INTEGER NOT NULL,
    layout_strategy TEXT NOT NULL,
    shard_id TEXT NOT NULL,
    status ingestion_status NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    details JSONB NOT NULL DEFAULT '{}',
    CHECK (source_record_count >= 0),
    CHECK (shard_record_count >= 0),
    CHECK (layout_strategy IN ('hash', 'industry')),
    CHECK (shard_id IN ('a', 'b')),
    CHECK (completed_at IS NULL OR completed_at >= started_at)
);

CREATE INDEX movies_release_date_idx ON movies (release_date);
CREATE INDEX movies_industry_idx ON movies (industry);
CREATE INDEX movies_title_idx ON movies (title);
CREATE INDEX people_name_idx ON people (name);
CREATE INDEX movie_genres_genre_id_idx ON movie_genres (genre_id);
CREATE INDEX movie_credits_movie_id_idx ON movie_credits (movie_id);
CREATE INDEX movie_credits_person_type_idx
    ON movie_credits (person_id, credit_type);
CREATE INDEX movie_countries_country_code_idx
    ON movie_countries (country_code);
