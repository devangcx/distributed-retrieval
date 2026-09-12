import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
QUERY_PATH = REPOSITORY_ROOT / "benchmark" / "queries.json"
CORPUS_PATH = REPOSITORY_ROOT / "data" / "processed" / "movies.json"
LAYOUT_DIRECTORY = REPOSITORY_ROOT / "data" / "processed" / "layouts"
VALID_EXECUTION_ORDERS = {"filter_then_search", "search_then_filter"}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_assignments() -> dict[str, dict[int, str]]:
    assignments: dict[str, dict[int, str]] = {"hash": {}, "industry": {}}
    for layout in assignments:
        for shard in ("a", "b"):
            manifest_path = LAYOUT_DIRECTORY / layout / f"shard_{shard}.json"
            manifest = load_json(manifest_path)
            for movie_id in manifest["movie_ids"]:
                assignments[layout][movie_id] = shard
    return assignments


def movie_matches_filter(movie, vector_filter) -> bool:
    release_date = movie.get("release_date")
    release_year = None
    if isinstance(release_date, str) and len(release_date) >= 4:
        release_year = int(release_date[:4])

    if release_year is not None:
        if release_year < vector_filter.get("release_year_from", release_year):
            return False
        if release_year > vector_filter.get("release_year_to", release_year):
            return False
    elif "release_year_from" in vector_filter or "release_year_to" in vector_filter:
        return False

    expected_genres = set(vector_filter.get("genre_ids", []))
    movie_genres = {genre["genre_id"] for genre in movie["genres"]}
    if expected_genres and not expected_genres.intersection(movie_genres):
        return False

    expected_industry = vector_filter.get("industry")
    if expected_industry is not None and movie["industry"] != expected_industry:
        return False

    expected_countries = set(vector_filter.get("country_codes", []))
    movie_countries = {country["country_code"] for country in movie["countries"]}
    if expected_countries and not expected_countries.intersection(movie_countries):
        return False

    return True


def test_query_set_has_the_required_balance_and_coverage() -> None:
    # Arrange
    query_document = load_json(QUERY_PATH)
    query_groups = query_document["queries"]

    # Act
    all_queries = [query for group in query_groups.values() for query in group]
    vector_queries = [
        query
        for kind in ("vector_dense", "vector_sparse")
        for query in query_groups[kind]
    ]
    filtered_queries = [query for query in vector_queries if "filter" in query]
    filter_fields = {
        field for query in filtered_queries for field in query["filter"]
    }

    # Assert
    assert query_document["schema_version"] == "benchmark-v1"
    assert set(query_groups) == {"relational", "vector_dense", "vector_sparse"}
    assert {kind: len(group) for kind, group in query_groups.items()} == {
        "relational": 12,
        "vector_dense": 12,
        "vector_sparse": 12,
    }
    assert len(all_queries) == 36
    assert len({query["id"] for query in all_queries}) == 36
    assert len(filtered_queries) == 12
    assert len(vector_queries) - len(filtered_queries) == 12
    assert filter_fields == {
        "release_year_from",
        "release_year_to",
        "genre_ids",
        "industry",
        "country_codes",
    }


def test_query_configurations_cover_the_required_experiments() -> None:
    # Arrange
    query_groups = load_json(QUERY_PATH)["queries"]

    # Act
    configured_queries = [
        (kind, query)
        for kind, group in query_groups.items()
        for query in group
    ]

    # Assert
    assert sum(len(query["configurations"]) for _, query in configured_queries) == 110
    for kind, query in configured_queries:
        configurations = query["configurations"]
        serialized = [json.dumps(item, sort_keys=True) for item in configurations]
        assert configurations
        assert len(serialized) == len(set(serialized))
        assert {item["layout"] for item in configurations} == {"hash", "industry"}

        for configuration in configurations:
            routing = configuration["routing"]
            assert routing in {"broadcast", "selective"}
            assert routing != "broadcast" or "shard" not in configuration
            assert routing != "selective" or configuration.get("shard") in {"a", "b"}
            if kind == "relational":
                assert "execution_order" not in configuration
            else:
                assert configuration["execution_order"] in VALID_EXECUTION_ORDERS

        if kind != "relational" and "filter" in query:
            for layout in ("hash", "industry"):
                orders = {
                    item["execution_order"]
                    for item in configurations
                    if item["layout"] == layout
                }
                assert orders == VALID_EXECUTION_ORDERS

            assert all(
                item["routing"] == "broadcast"
                for item in configurations
                if item["layout"] == "hash"
            )
            industry = query["filter"].get("industry")
            expected_shard = None
            if industry == "hollywood":
                expected_shard = "a"
            if industry in {"bollywood", "other_or_ambiguous"}:
                expected_shard = "b"
            industry_configurations = [
                item for item in configurations if item["layout"] == "industry"
            ]
            if expected_shard is None:
                assert all(
                    item["routing"] == "broadcast"
                    for item in industry_configurations
                )
            else:
                assert all(
                    item["routing"] == "selective"
                    and item["shard"] == expected_shard
                    for item in industry_configurations
                )
        elif kind == "vector_dense":
            assert all(item["routing"] == "broadcast" for item in configurations)
        elif kind == "vector_sparse":
            for layout in ("hash", "industry"):
                layout_configurations = [
                    item for item in configurations if item["layout"] == layout
                ]
                assert {item["routing"] for item in layout_configurations} == {
                    "broadcast",
                    "selective",
                }
                selective = next(
                    item
                    for item in layout_configurations
                    if item["routing"] == "selective"
                )
                assert selective["shard"] == query["target_shards"][layout]


def test_relevance_judgments_match_the_corpus_filters_and_shards() -> None:
    # Arrange
    query_groups = load_json(QUERY_PATH)["queries"]
    movies = load_json(CORPUS_PATH)
    movies_by_id = {movie["movie_id"]: movie for movie in movies}
    assignments = load_assignments()

    # Act
    relational_queries = query_groups["relational"]
    vector_queries = [
        query
        for kind in ("vector_dense", "vector_sparse")
        for query in query_groups[kind]
    ]

    # Assert
    assert all("correctness" in query for query in relational_queries)
    assert all(query["query_text"].strip() for query in vector_queries)
    assert len({query["query_text"] for query in vector_queries}) == 24

    for query in vector_queries:
        judged_ids = query.get("relevant_movie_ids", [])
        if "target_movie_id" in query:
            judged_ids = [query["target_movie_id"]]
        assert judged_ids
        assert all(movie_id in movies_by_id for movie_id in judged_ids)
        if "filter" in query:
            assert all(
                movie_matches_filter(movies_by_id[movie_id], query["filter"])
                for movie_id in judged_ids
            )

        if "target_shards" in query:
            target_movie_id = query["target_movie_id"]
            assert query["target_shards"] == {
                layout: assignments[layout][target_movie_id]
                for layout in ("hash", "industry")
            }
