# Local Setup

Install Git, Docker Desktop, Python 3.12, and the Rust toolchain before starting.
After cloning, run the remaining steps from the repository root in the order
shown. This sequence builds the canonical corpus, layout manifests,
PostgreSQL stores, and Qdrant stores.

If you already have cached data, directly start at `Remove Existing Containers and Volumes`
step.

## Clone the Repository

```bash
git clone https://github.com/devangcx/distributed-retrieval.git
cd distributed-retrieval
```

## Configure Environment Variables

Get a TMDB API key and read access token from
https://developer.themoviedb.org/docs/getting-started, and add an OpenAI API
key before Qdrant ingestion. Keep the local database URLs unchanged unless
their Compose ports have been changed.

```
TMDB_API_READ_ACCESS_TOKEN="your_read_access_token"
TMDB_API_KEY="your_api_key"
POSTGRES_SHARD_A_URL="postgres://retrieval_user:retrieval_password@localhost:5433/retrieval"
POSTGRES_SHARD_B_URL="postgres://retrieval_user:retrieval_password@localhost:5434/retrieval"
QDRANT_SHARD_A_URL="http://localhost:6333"
QDRANT_SHARD_B_URL="http://localhost:6335"
OPENAI_API_KEY="your_openai_api_key"
```

## Create the Development Environment

Create a local Python environment and install the Python dependencies:

```bash
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Install the Diesel CLI used by the relational-schema initialization script:

```bash
cargo install diesel_cli --version 2.3.12 --no-default-features --features postgres
diesel --version
```

## Build the Canonical Movie Corpus

> [!WARNING]
> This step will fetch data from the TMDB API and overwrite any existing
> canonical corpus. You only need to do this if you want fresh data. Use the
> cached data at `data/raw` instead if you do not need fresh data.

View the available corpus options:

```bash
python scripts/fetch_data.py --help
```

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

## Generate the Layout Manifests

Generate both hash and industry manifests from the canonical corpus.

> [!NOTE]
> Read more about the hash and industry layout in the layout manifests section.

View the available manifest-generation options:

```bash
python scripts/generate_layout_manifests.py --help
```

```bash
python scripts/generate_layout_manifests.py
```

This writes the four manifests beneath `data/processed/layouts`. Each
manifest records its source checksum, strategy, shard, and assigned movie IDs.

## Remove Existing Containers and Volumes

For a clean rebuild, remove the existing containers and their PostgreSQL and
Qdrant data volumes. This permanently deletes the locally stored databases:

```bash
docker compose down --volumes --remove-orphans
```

## Start the Containers

> [!NOTE]
> Make sure the Docker daemon is installed (using Docker Desktop) and running.

Build the Rust orchestrator image and start it with the PostgreSQL and Qdrant
containers:

```bash
docker compose up -d --build
```

Inspect status of all container and the network

```bash
docker compose ps -a
```

The orchestrator is available from the host at `http://localhost:3000`. Inside
the Compose network it connects to the stores using their service names and
internal ports. The `.env` database and Qdrant URLs continue to use `localhost`
and the published ports because the Python ingestion and benchmark commands run
directly on the host, not in containers.

Check that the orchestrator process is running:

```bash
curl http://localhost:3000/health
```

## Build the PostgreSQL Relational Stores

After Docker reports both PostgreSQL containers as healthy, create and migrate
the `hash_layout` and `industry_layout` schemas on both shards:

```bash
python scripts/initiate_relational_schema.py
```

This command creates both schemas on both PostgreSQL shards and runs
`diesel migration run` once against each schema. No separate migration command
is required.

View the available loader options:

```bash
python scripts/load_postgres.py --help
```

Load each manifest into its matching physical shard:

```bash
python scripts/load_postgres.py --manifest data/processed/layouts/hash/shard_a.json --database-url-env POSTGRES_SHARD_A_URL
```

```bash
python scripts/load_postgres.py --manifest data/processed/layouts/hash/shard_b.json --database-url-env POSTGRES_SHARD_B_URL
```

```bash
python scripts/load_postgres.py --manifest data/processed/layouts/industry/shard_a.json --database-url-env POSTGRES_SHARD_A_URL
```

```bash
python scripts/load_postgres.py --manifest data/processed/layouts/industry/shard_b.json --database-url-env POSTGRES_SHARD_B_URL
```

## Verify the Build

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
docker compose exec -T postgres-shard-a psql -U retrieval_user -d retrieval -c "
SELECT
    'hash_layout' AS layout,
    (SELECT count(*) FROM hash_layout.movies) AS movies,
    (SELECT count(*) FROM hash_layout.ingestion_runs
        WHERE status = 'completed') AS completed_runs
UNION ALL
SELECT
    'industry_layout',
    (SELECT count(*) FROM industry_layout.movies),
    (SELECT count(*) FROM industry_layout.ingestion_runs
        WHERE status = 'completed');
"
```

Confirm the loaded movie counts on PostgreSQL shard B:

```bash
docker compose exec -T postgres-shard-b psql -U retrieval_user -d retrieval -c "
SELECT
    'hash_layout' AS layout,
    (SELECT count(*) FROM hash_layout.movies) AS movies,
    (SELECT count(*) FROM hash_layout.ingestion_runs
        WHERE status = 'completed') AS completed_runs
UNION ALL
SELECT
    'industry_layout',
    (SELECT count(*) FROM industry_layout.movies),
    (SELECT count(*) FROM industry_layout.ingestion_runs
        WHERE status = 'completed');
"
```

Each load validates its manifest and corpus checksum before connecting, then
transactionally replaces the selected layout schema. The expected movie counts
are:

> [!NOTE]
> Read more about checksum below in layout manifests.

| PostgreSQL shard | Layout            | Movies |
| ---------------- | ----------------- | -----: |
| A                | `hash_layout`     |  5,485 |
| A                | `industry_layout` |  9,559 |
| B                | `hash_layout`     |  5,608 |
| B                | `industry_layout` |  1,534 |

## Build the Qdrant Vector Stores

The versioned vector settings are recorded in
`config/qdrant_contract.json`. Validate the contract, canonical corpus, and all
four layout manifests without contacting OpenAI or Qdrant:

```bash
python -m scripts.load_qdrant
```

The validation reports 11,093 unique movies and 22,186 dense input texts: one
overview and one full document per movie.

After both Qdrant containers are running and the API key has been configured,
create and populate all four collections:

```bash
python -m scripts.load_qdrant --execute
```

The loader embeds each canonical movie once and routes the resulting point to
both layouts using only the validated manifest assignments:

| Qdrant shard | Collection           | Expected points |
| ------------ | -------------------- | --------------: |
| A            | `movies_hash_v1`     |           5,485 |
| B            | `movies_hash_v1`     |           5,608 |
| A            | `movies_industry_v1` |           9,559 |
| B            | `movies_industry_v1` |           1,534 |

The loader refuses to replace an existing collection. It count-verifies every
collection after ingestion and reports the paid OpenAI token total returned by
the embeddings API.

Paid dense embeddings are preserved beneath
`data/processed/embeddings/movie-retrieval-v1/` as two little-endian float32
files plus checksummed metadata. These artifacts are committed so a clone can
rebuild Qdrant without another OpenAI request.

The two binary files remain
separate so each stays below GitHub's individual-file size limit.

BM25 vectors
are regenerated locally because they do not incur an API charge.

To stop the system later while retaining its data volumes, run:

```bash
docker compose down --remove-orphans
```

# Data

## Canonical Corpus

The target is 20,000 usable movies. However, the following filters determine
the actual number of movies we are able to fetch.

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
- Both modes normalize records in ascending TMDB movie-ID order, making repeated
  runs against the same raw cache deterministic.
- Movies with blank titles or
  overviews are excluded, and the output can therefore contain fewer records than
  the requested target.

## Layout Manifests

The manifests are written beneath `data/processed/layouts`. They contain
movie IDs and audit metadata; generating them does not load PostgreSQL or
Qdrant.

### Layout strategies

The experiments compare two ways of assigning the same 11,093 movies across
two shards:

```text
Hash layout
  shard A: even movie IDs
  shard B: odd movie IDs

Industry layout
  shard A: Hollywood
  shard B: Bollywood and other_or_ambiguous
```

The hash layout produces similarly sized shards. The industry layout groups movies
by domain metadata and deliberately produces an imbalanced distribution. Each
strategy generates one manifest for shard A and one for shard B.
Within a strategy, the two manifests are disjoint and together cover the
complete canonical corpus.

A movie's PostgreSQL rows and future Qdrant point always use the same manifest
assignment. The two layouts are never mixed within a PostgreSQL schema or
Qdrant collection.

### Persistent storage

Both layouts remain available at the same time in separate schemas on the same
two PostgreSQL containers:

```text
postgres-shard-a: hash_layout and industry_layout
postgres-shard-b: hash_layout and industry_layout
```

Using the same containers holds PostgreSQL configuration and host resource
conditions constant between strategies. Separate schemas isolate the layouts
without doubling the container count. Both layouts remain stored, but they are
benchmarked one at a time so they do not compete during measurements.

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

### Loader behavior

The loader runs once for each layout manifest. The manifest's strategy selects
`hash_layout` or `industry_layout`, while its shard ID determines whether the
destination is physical shard A or B. Before connecting, the loader verifies
the source checksum, corpus count, manifest count, and movie IDs.

Each load transactionally clears and rebuilds only its selected layout schema.
A failure rolls back the replacement without changing the other layout.

At the loader boundary, canonical `other_or_ambiguous` industry values map to
the PostgreSQL enum value `other`; the source JSON is not rewritten.

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

Dense vectors use OpenAI `text-embedding-3-large` shortened through the API to
1,024 dimensions.

Sparse vectors use FastEmbed `Qdrant/bm25`, with Qdrant's IDF
modifier enabled at collection creation.

#### Vector indexes

- `overview_dense` and `full_document_dense` use Qdrant's dense HNSW
  (Hierarchical Navigable Small World) index for approximate nearest-neighbor
  search with cosine distance. The loader does not set `hnsw_config`, so these
  indexes use the defaults supplied by the deployed Qdrant version.
- `metadata_sparse` uses Qdrant's sparse inverted index rather than HNSW. Its
  `Qdrant/bm25` representation maps lexical terms to sparse weights, and the
  collection's `IDF` modifier incorporates collection-wide term frequency at
  query time. Common terms are therefore less influential than rarer, more
  discriminating terms.

The HNSW and sparse-index settings are independent: HNSW serves semantic dense
retrieval, while the inverted index serves lexical sparse retrieval. Their
ranked results can be fused by the retrieval layer.

The deterministic template normalizes surrounding and repeated whitespace but
preserves case and punctuation. Missing scalar values use `[unknown]`; empty
lists use `[none]`. Canonical list order is preserved.

```text
Title: {title}
Original title: {original_title}
Release year: {release_year}
Genres: {genre names}
Industry: {industry}
Countries: {country names}
Directors: {director names}
Cast: {actor name as character}
Overview: {overview}
```

`full_document_dense` uses the complete template. `metadata_sparse` omits the
overview line, while `overview_dense` contains only the normalized overview.

#### Qdrant payload

```text
movie_id       integer
release_year   integer
genre_ids      integer array
industry       keyword
country_codes  keyword array
layout_strategy keyword
shard_id        keyword
embedding_version keyword
source_checksum keyword
```

- `release_year`, `genre_ids`, `industry`, and `country_codes` receive payload
  indexes and provide hard filters during vector search. `movie_id` is already
  the Qdrant point ID; the remaining fields record provenance.
- Payload is duplicated search metadata, not the source of truth.
- Director data remains PostgreSQL-only initially and will be duplicated into
  payload only if benchmarks justify it.

Changing an embedding model, sparse model, source fields, text template,
preprocessing rule, or cast limit creates a new embedding version. Different
versions use separate Qdrant collections while preserving `movie_id` as the
point ID.
