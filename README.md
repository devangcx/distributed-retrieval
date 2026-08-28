# Local Setup

Run the following steps from the repository root in the order shown.
Install Docker Desktop, Python 3, and the Rust toolchain before starting. This
sequence builds the canonical corpus, layout manifests, and PostgreSQL stores.
Qdrant ingestion will be added in its own milestone.

## 1. Remove Existing Containers and Volumes

For a clean rebuild, remove the existing containers and their PostgreSQL and
Qdrant data volumes. This permanently deletes the locally stored databases:

```bash
docker compose down --volumes --remove-orphans
```

## 2. Configure Environment Variables

Get a TMDB API key and read access token from
https://developer.themoviedb.org/docs/getting-started. Add these values and the
local PostgreSQL connection URLs to `.env`:

```
TMDB_API_READ_ACCESS_TOKEN="your_read_access_token"
TMDB_API_KEY="your_api_key"
POSTGRES_SHARD_A_URL="postgres://retrieval_user:retrieval_password@localhost:5433/retrieval"
POSTGRES_SHARD_B_URL="postgres://retrieval_user:retrieval_password@localhost:5434/retrieval"
```

## 3. Create the Development Environment

Create a local Python environment and install the Python dependencies:

```bash
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
pip install -r scripts/requirements.txt
pip install -r scripts/dev-requirements.txt
```

Install the Diesel CLI used by the relational-store build script:

```bash
cargo install diesel_cli --version 2.3.12 --no-default-features --features postgres
diesel --version
```

## 4. Build the Canonical Movie Corpus

> [!WARNING]
> This step will fetch data from the TMDB API and overwrite any existing
> canonical corpus. You only need to do this if you want fresh data. Use the
> cached data at `data/raw` instead if you do not need fresh data.

Fetch and normalize the TMDB records into the canonical
`data/processed/movies.json` file:

```bash
python scripts/fetch_data.py
```

If the raw TMDB detail cache is already present, the following command can be
used instead to rebuild the same canonical artifact without network requests:

```bash
python scripts/fetch_data.py --from-cache
```

## 5. Generate the Layout Manifests

Generate both hash and industry manifests from the canonical corpus.

> [!NOTE]
> Read more about the hash and industry layout in the
> data model below.

```bash
python scripts/generate_layout_manifests.py
```

This writes the four manifests beneath `data/processed/layouts`. Each
manifest records its source checksum, strategy, shard, and assigned movie IDs.

## 6. Start the Database Containers

> [!NOTE]
> Make sure the Docker daemon is installed (using Docker Desktop) and running.

Start all containers and network using Docker Compose:

```bash
docker compose up -d
```

Inspect status of all container and the network

```bash
docker compose ps -a
```

## 7. Build the PostgreSQL Relational Stores

After Docker reports both PostgreSQL containers as healthy, create and migrate
the `hash_layout` and `industry_layout` schemas on both shards:

```bash
python scripts/build_relational_store.py
```

This command creates both schemas on both PostgreSQL shards and runs
`diesel migration run` once against each schema. No separate migration command
is required.

Load each manifest into its matching physical shard:

```bash
python scripts/load_postgres.py --manifest data/processed/layouts/hash/shard_a.json --database-url-env POSTGRES_SHARD_A_URL
python scripts/load_postgres.py --manifest data/processed/layouts/hash/shard_b.json --database-url-env POSTGRES_SHARD_B_URL
python scripts/load_postgres.py --manifest data/processed/layouts/industry/shard_a.json --database-url-env POSTGRES_SHARD_A_URL
python scripts/load_postgres.py --manifest data/processed/layouts/industry/shard_b.json --database-url-env POSTGRES_SHARD_B_URL
```

Each load validates its manifest and corpus checksum before connecting, then
transactionally replaces the selected layout schema. The expected movie counts
are:

> [!NOTE]
> Read more about checksum below in layout manifests.

| PostgreSQL shard | Layout            | Movies |
| ---------------- | ----------------- | -----: |
| A                | `hash_layout`     |  5,485 |
| B                | `hash_layout`     |  5,608 |
| A                | `industry_layout` |  9,559 |
| B                | `industry_layout` |  1,534 |

## 8. Verify the Build

Run the Python tests:

```bash
python -m pytest
```

Compile the Rust application and run its tests:

```bash
cargo test
```

Confirm the loaded movie counts on PostgreSQL shard A:

```bash
docker compose exec -T postgres-shard-a psql -U retrieval_user -d retrieval -c "SELECT 'hash_layout' AS layout, (SELECT count(*) FROM hash_layout.movies) AS movies, (SELECT count(*) FROM hash_layout.ingestion_runs WHERE status = 'completed') AS completed_runs UNION ALL SELECT 'industry_layout', (SELECT count(*) FROM industry_layout.movies), (SELECT count(*) FROM industry_layout.ingestion_runs WHERE status = 'completed');"
```

Confirm the loaded movie counts on PostgreSQL shard B:

```bash
docker compose exec -T postgres-shard-b psql -U retrieval_user -d retrieval -c "SELECT 'hash_layout' AS layout, (SELECT count(*) FROM hash_layout.movies) AS movies, (SELECT count(*) FROM hash_layout.ingestion_runs WHERE status = 'completed') AS completed_runs UNION ALL SELECT 'industry_layout', (SELECT count(*) FROM industry_layout.movies), (SELECT count(*) FROM industry_layout.ingestion_runs WHERE status = 'completed');"
```

The expected counts are listed in step 7. The Python tests use temporary data
and mocked TMDB responses, so they do not make network requests.

To stop the system later while retaining its data volumes, run:

```bash
docker compose down --remove-orphans
```

# Data

## Canonical Corpus

- The target is 20,000 usable movies.
- However, the following filters determine the actual number of
  movies we are able to fetch.

Filters

- Countries:
  - US
  - IN
- Languages:
  - English
  - Hindi
- Release Dates:
  - 1990-01-01 to 2026-08-01
- Minimum Vote Count:
  - 20
- No adult and video content

- The cache-only mode makes no TMDB requests and does not require credentials.
- Both modes normalize records in ascending TMDB movie-ID order, making repeated
  runs against the same raw cache deterministic.
- Movies with blank titles or
  overviews are excluded, and the output can therefore contain fewer records than
  the requested target.

## Layout Manifests

The manifests are written beneath `data/processed/layouts`. They contain
movie IDs and audit metadata; generating them does not load PostgreSQL or
Qdrant.

### Manifest checksums

Every manifest contains an `input_checksum` identifying the exact
`movies.json` file from which its movie IDs were generated. This prevents a
manifest created for one corpus version from being used accidentally with a
different version. Without this check, added, removed, or reordered movies
could make the manifest and canonical records disagree while still producing
apparently valid shard IDs.

`generate_layout_manifests.py` reads `movies.json` as raw bytes and calculates a
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

`layout_strategy` identifies
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

## Persistent PostgreSQL Layouts

Both layout strategies use the same two PostgreSQL containers but remain
available at the same time in separate schemas:

```text
postgres-shard-a: hash_layout and industry_layout
postgres-shard-b: hash_layout and industry_layout
```

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

The relational-store build script uses Diesel for migration history and applies
the same finalized migration independently within each schema, as shown in the
Local Setup sequence.

### Loader behavior

The loader runs once for each of the four manifests. The manifest strategy
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
