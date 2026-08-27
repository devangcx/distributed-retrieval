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

### Manifest checksums

Every manifest contains an `input_checksum` identifying the exact
`movies.json` file from which its movie IDs were generated. This prevents a
manifest created for one corpus version from being used accidentally with a
different version. Without this check, added, removed, or reordered movies
could make the manifest and canonical records disagree while still producing
apparently valid shard IDs.

`generate_partitions.py` reads `movies.json` as raw bytes and calculates a
SHA-256 digest:

```python
source_bytes = input_path.read_bytes()
input_checksum = hashlib.sha256(source_bytes).hexdigest()
```

The same checksum is stored in all four manifests produced from that input. The
PostgreSQL loader recalculates SHA-256 from the supplied canonical file and
refuses to continue if it differs from the manifest value. The checksum is
also recorded in `ingestion_runs`, linking a database load to its exact source
artifact.

The checksum covers the file bytes rather than only the set of movie IDs. A
change to metadata or JSON formatting therefore creates a new checksum and
requires regenerating the manifests. This is intentional: the checksum is a
reproducibility and consistency guard, not merely a shard-membership check or a
security signature.

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

### Preparing persistent PostgreSQL layouts

Both partition strategies use the same two PostgreSQL containers but remain
available at the same time in separate schemas:

```text
postgres-shard-a: hash_layout and industry_layout
postgres-shard-b: hash_layout and industry_layout
```

### Why we don't have separate containers/shards for each layout

Separate containers could be given the same CPU, memory, and PostgreSQL
settings. However, we keep both strategies in the same containers because:

- Using the exact same PostgreSQL instances reduces the chance of configuration
  differences between the strategies.
- Separate containers and volumes can have different cache and disk states.
- Running eight containers together would create more competition for the
  host's CPU, memory, and disk.
- The dataset is small enough for both layouts to share the existing
  containers.
- Separate schemas keep the layouts isolated while allowing both to remain
  available.
- The Rust orchestrator can switch schemas instead of rebuilding the database.
- Four containers are simpler to run and monitor than eight.

Both layouts are stored at the same time, but they will be benchmarked one at a
time so they do not compete for the same resources during measurements.

### Setting up PostgreSQL schemas for both layouts

After both PostgreSQL containers are running, create both schemas on each shard
and run the existing Diesel migration once per schema:

```bash
python scripts/setup_postgres_layouts.py
```

This is a later database operation and is deliberately separate from offline
loader validation. The wrapper uses Diesel for migration history and applies
the same finalized migration independently within each schema.

### Validating and loading PostgreSQL shards

Validate a corpus/manifest pair without connecting to PostgreSQL:

```bash
python scripts/load_postgres.py \
  --manifest data/processed/partitions/hash/shard_a.json \
  --dry-run
```

Omit `--dry-run` and name the destination URL environment variable only when a
database load is intended:

```bash
python scripts/load_postgres.py \
  --manifest data/processed/partitions/hash/shard_a.json \
  --database-url-env POSTGRES_SHARD_A_URL
```

Run the loader once for each of the four manifests. The manifest strategy
selects `hash_layout` or `industry_layout`, while the URL selects physical
shard A or B. The loader verifies the source checksum and counts before it
connects, then clears and rebuilds that one schema inside a transaction. A
failure rolls back the transaction, and loading one schema does not change the
other layout.

This project uses a fixed experimental corpus and plans one final ingestion,
so the loader deliberately performs full replacement instead of maintaining
incremental reconciliation logic. This keeps the ingestion path smaller and
easier to reproduce before development moves to the Rust orchestrator.

The canonical industry label `other_or_ambiguous` is explicitly mapped to the
finalized PostgreSQL enum label `other` at the loader boundary. The source JSON
is not rewritten.

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

- The layouts are not mixed within a schema or Qdrant collection.
- Both layouts remain available simultaneously on the same four containers.
  Each PostgreSQL shard contains separate `hash_layout` and `industry_layout`
  schemas. Each Qdrant shard will use separate collections for the two layouts.
  This keeps the datasets persistent while giving both strategies the same
  container resources.
- A movie's PostgreSQL rows and Qdrant point always use the
  same shard assignment.
