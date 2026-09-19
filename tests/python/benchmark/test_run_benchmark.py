import io
import json

from benchmark.run_benchmark import (
    DEFAULT_QUERY_PATH,
    build_run_manifest,
    build_request,
    configured_queries,
    load_json,
    run_requests,
)


def test_expands_all_explicit_query_configurations() -> None:
    # Arrange
    query_document = load_json(DEFAULT_QUERY_PATH)

    # Act
    configured = configured_queries(query_document)

    # Assert
    assert len(configured) == 109
    assert {query_kind for query_kind, _, _ in configured} == {
        "relational",
        "vector_dense",
        "vector_sparse",
    }


def test_builds_each_api_request_type() -> None:
    # Arrange
    configuration = {
        "layout": "industry",
        "routing": "selective",
        "shard": "b",
        "execution_order": "search_then_filter",
    }
    representations = {
        "dense": {"vector": [0.1, 0.2]},
        "sparse": {"indices": [10, 20], "values": [1.0, 2.0]},
    }

    # Act
    relational = build_request(
        "relational",
        {"id": "rel", "sql": "SELECT 1", "limit": 2},
        configuration,
        representations,
    )
    dense = build_request(
        "vector_dense",
        {"id": "dense", "vector_name": "overview_dense", "limit": 10},
        configuration,
        representations,
    )
    sparse = build_request(
        "vector_sparse",
        {"id": "sparse", "limit": 10},
        configuration,
        representations,
    )

    # Assert
    assert relational["sql"] == "SELECT 1"
    assert "execution_order" not in relational
    assert dense["vector"] == [0.1, 0.2]
    assert dense["vector_name"] == "overview_dense"
    assert sparse["indices"] == [10, 20]
    assert sparse["values"] == [1.0, 2.0]


def test_warmups_are_excluded_and_measured_records_are_written_immediately() -> None:
    # Arrange
    query_document = {
        "default_limit": 10,
        "queries": {
            "relational": [
                {
                    "id": "rel",
                    "sql": "SELECT 1 AS movie_id",
                    "limit": 1,
                    "correctness": {"type": "movie_ids", "expected": [1]},
                    "configurations": [
                        {"layout": "hash", "routing": "broadcast"}
                    ],
                }
            ]
        }
    }
    calls = []
    output = io.StringIO()

    def send_request(url, request_body, timeout_seconds):
        calls.append((url, request_body, timeout_seconds))
        return {
            "request_latency_ms": 5.0,
            "http_status": 200,
            "response": {
                "results": [{"movie_id": 1}],
                "total_ms": 4.0,
                "shard_stats": [],
            },
            "failure": None,
        }

    # Act
    measured_count = run_requests(
        query_document,
        {},
        output,
        "http://localhost:3000/query",
        warmups=1,
        repetitions=2,
        timeout_seconds=15.0,
        send_request=send_request,
    )
    records = [json.loads(line) for line in output.getvalue().splitlines()]

    # Assert
    assert len(calls) == 3
    assert measured_count == 2
    assert len(records) == 2
    assert [record["repetition"] for record in records] == [1, 2]
    assert records[0]["contacted_shards"] == ["a", "b"]
    assert records[0]["ranking"][0]["movie_id"] == 1
    assert records[0]["relational_results"] == [{"movie_id": 1}]
    assert records[0]["correctness"] == {"type": "movie_ids", "expected": [1]}


def test_applies_the_document_default_limit() -> None:
    # Arrange
    query_document = {
        "default_limit": 10,
        "queries": {
            "vector_dense": [
                {
                    "id": "dense",
                    "configurations": [
                        {
                            "layout": "hash",
                            "routing": "broadcast",
                            "execution_order": "filter_then_search",
                        }
                    ],
                }
            ]
        },
    }

    # Act
    configured = configured_queries(query_document)

    # Assert
    assert configured[0][1]["limit"] == 10


def test_builds_manifest_from_run_inputs_and_selected_settings(
    tmp_path, monkeypatch
) -> None:
    # Arrange
    query_path = tmp_path / "queries.json"
    representation_path = tmp_path / "representations.json"
    query_path.write_bytes(b'{"queries": []}\n')
    representation_path.write_bytes(b'{"representations": {}}\n')
    query_document = {
        "default_limit": 10,
        "queries": {
            "vector_dense": [{
                "id": "dense",
                "configurations": [
                    {
                        "layout": "hash",
                        "routing": "broadcast",
                        "execution_order": "filter_then_search",
                    },
                    {
                        "layout": "industry",
                        "routing": "selective",
                        "shard": "a",
                        "execution_order": "search_then_filter",
                    },
                ],
            }],
        },
    }
    monkeypatch.setattr("platform.system", lambda: "TestOS")
    monkeypatch.setattr("platform.release", lambda: "1.0")
    monkeypatch.setattr("platform.python_implementation", lambda: "CPython")
    monkeypatch.setattr("platform.python_version", lambda: "3.12.0")

    # Act
    manifest = build_run_manifest(
        query_path,
        representation_path,
        query_document,
        warmups=2,
        repetitions=30,
    )

    # Assert
    assert manifest["inputs"]["queries"]["sha256"] == (
        "99d9e7655e12570b9f9aeb608530ec4a1976698ce2b940d2a4f072b0c542ca99"
    )
    assert manifest["inputs"]["representations"]["sha256"] == (
        "c23aced79ac684c1b1c441c35fbd3d0b98a97393a0381611b826524c1affbe57"
    )
    assert manifest["warmups_per_configuration"] == 2
    assert manifest["repetitions_per_configuration"] == 30
    assert manifest["configured_query_count"] == 2
    assert manifest["selected_configurations"] == [
        {"layout": "hash", "routing": "broadcast", "shard": None},
        {"layout": "industry", "routing": "selective", "shard": "a"},
    ]
    assert manifest["execution_orders"] == [
        "filter_then_search",
        "search_then_filter",
    ]
    assert manifest["environment"] == {
        "operating_system": {"system": "TestOS", "release": "1.0"},
        "python": {"implementation": "CPython", "version": "3.12.0"},
    }
