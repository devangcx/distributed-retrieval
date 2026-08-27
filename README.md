# Local Setup

Get a TMDB API key and read access token. from https://developer.themoviedb.org/docs/getting-started

## Environment Variables

Have the following environment variables.

```
TMDB_API_READ_ACCESS_TOKEN="your_read_access_token"
TMDB_API_KEY="your_api_key"
POSTGRES_SHARD_A_URL="postgres://retrieval_user:retrieval_password@localhost:5433/retrieval"
POSTGRES_SHARD_B_URL="postgres://retrieval_user:retrieval_password@localhost:5434/retrieval"
```

## Python Environment

Create a local Python environment and install the dependencies.

```bash
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
pip install -r scripts/requirements.txt
pip install -r scripts/dev-requirements.txt
```

## Docker

Start all containers and network using Docker Compose:

```bash
docker-compose up -d
```

Inspect status of all container and the network

```bash
docker-compose ps -a
```

Stop all containers while retaining data volumes

```bash
docker-compose down --remove-orphans
```

# Testing

Run the complete Python test suite from the repository root:

```bash
python -m pytest
```

To run one test module or one specific test, pass its path.

```bash
python -m pytest tests/python/scripts/test_fetch_data.py
```

Run the Rust tests and compile the Rust test targets with:

```bash
cargo test
```

The Python tests use temporary data and mocked TMDB responses, so they do not
require API credentials or make network requests.

# Data

## Fetching Data

Use `data/processed/movies.json` to fetch the movie corpus.

```bash
python scripts/fetch_data.py
```

- The target is 20,000 usable movies.
- However, usage of the following filters determine the actual number of
  movies we are able to fetch.

Filters

- Countries:
  - US
  - IN
- Languages:
  - English
  - Hindi
- Release Dates:
  - 2020-01-01 to 2023-12-31
- Minimum Vote Count:
  - 20
- No adult and video content

## Building from local cache

To rebuild the processed dataset using only the existing detail files, run:

```bash
python scripts/fetch_data.py --from-cache --target-size 20000
```

- The cache-only mode makes no TMDB requests and does not require credentials.
- Both modes normalize records in ascending TMDB movie-ID order, making repeated
  runs against the same raw cache deterministic.
- Movies with blank titles or
  overviews are excluded, and the output can therefore contain fewer records than
  the requested target.

Run `python scripts/fetch_data.py --help` for the complete command help.

## Generating Partition Manifests

After producing `movies.json`, generate the hash and industry shard manifests:

```bash
python scripts/generate_partitions.py
```

The manifests are written beneath `data/processed/partitions`. They contain
movie IDs and audit metadata; running this command does not load PostgreSQL or
Qdrant. Generate only one strategy when needed:

```bash
python scripts/generate_partitions.py --strategy hash
python scripts/generate_partitions.py --strategy industry
```

Custom paths are also supported:

```bash
python scripts/generate_partitions.py --input path/to/movies.json --output-dir path/to/partitions
```

Run `python scripts/generate_partitions.py --help` for the complete command
help.

## Data Model

- PostgreSQL is the authoritative store for movie metadata and relationships.
- Qdrant is a derived, rebuildable vector store.
- TMDB `movie_id` identifies the same movie in both stores.
- Diesel is the Rust persistence layer. SQL migrations define the database, and
  Diesel generates `src/schema.rs` from the applied schema.

### PostgreSQL schema

```sql
CREATE TYPE industry_type AS ENUM (
    'hollywood',
    'bollywood',
    'other_or_ambiguous'
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
    partition_strategy TEXT NOT NULL,
    shard_id TEXT NOT NULL,
    status ingestion_status NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    details JSONB NOT NULL DEFAULT '{}',
    CHECK (source_record_count >= 0),
    CHECK (shard_record_count >= 0),
    CHECK (partition_strategy IN ('hash', 'industry')),
    CHECK (shard_id IN ('a', 'b')),
    CHECK (completed_at IS NULL OR completed_at >= started_at)
);
```

- `movie_genres` and `movie_countries` represent many-to-many relationships.
- `movie_credits` represents actors and directors using stable TMDB person and
  credit IDs.
- Deleting a movie cascades to these association records.
- Deleting a
  referenced genre, person, or country is restricted.

`ingestion_runs` is an audit log, not searchable movie data. It identifies the
input version and checksum, records shard counts and status, and supports
reproducible experiments across both shards.

`partition_strategy` identifies
the hash or industry layout, while `shard_id` identifies shard A or B without
assuming that the strategy uses a numeric remainder.

#### Relational indexes

```sql
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
```

Primary keys and unique constraints already supply their own indexes.

### Some data decisions

- `release_date` is authoritative; release year and month are derived.
- `vote_average` uses `NUMERIC(3,1)` and permits values from `0.0` to `10.0`.
- `original_title` and `original_language` are retained.
- Movies without a non-blank overview are excluded.
- The first ten cast credits are retained in TMDB billing order.
- TMDB credit IDs are retained to make credit imports idempotent.
- Production countries are normalized through `countries` and
  `movie_countries`.
- Production companies and TMDB popularity are omitted.
- Only actor and director credits are modeled.

### Qdrant retrieval model

Each point uses `movie_id` as its point ID and contains three named representations:

```text
overview_dense
  Dense embedding of the overview only.

metadata_sparse
  Sparse lexical representation of title, original title, genres, directors,
  the first ten cast members and character names, industry, and countries.

full_document_dense
  Dense embedding of a deterministic document containing title, original
  title, release year, genres, industry, countries, directors, the first ten
  cast members and characters, and overview.
```

- Dense vectors use cosine distance.
- Reciprocal Rank Fusion is the candidate for fusion between dense and sparse results.

#### Qdrant payload

```text
movie_id       integer
release_year   integer
genre_ids      integer array
industry       keyword
country_codes  keyword array
```

- These fields receive payload indexes and provide hard filters during vector
  search.
- Payload is duplicated search metadata, not the source of truth.
- Director data remains PostgreSQL-only initially and will be duplicated into
  payload only if benchmarks justify it.

Changing an embedding model, sparse model, source fields, text template,
preprocessing rule, or cast limit creates a new embedding version. Different
versions use separate Qdrant collections while preserving `movie_id` as the
point ID.

## Migration

To run Diesel migration to a specific shard, use the following command:

```bash
diesel migration run --database-url $DATABASEURL
```

Confirm Diesel run status on a specific shard

```bash
diesel migration list --database-url $URL
```

Inspect actual tables

```bash
docker compose exec postgres-shard-a psql -U retrieval_user -d retrieval -c "\dt"
docker compose exec postgres-shard-b psql -U retrieval_user -d retrieval -c "\dt"
```

Use `down.sql` to revert migrations if needed.

```bash
diesel migration revert --database-url $DATABASEURL
```

# Sharding

Two strategies will be evaluated while holding the shard count at two:

```text
Hash layout
  shard A: even movie IDs
  shard B: odd movie IDs

Industry layout
  shard A: Hollywood
  shard B: Bollywood and other_or_ambiguous
```

- The layouts are not mixed.
- They are loaded and benchmarked sequentially using
  the same two PostgreSQL and two Qdrant services so that both strategies receive
  the same resources.
- A movie's PostgreSQL rows and Qdrant point always use the
  same shard assignment.
