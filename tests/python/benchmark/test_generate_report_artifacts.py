import csv
import json

import pytest

from benchmark.generate_report_artifacts import generate_artifacts


def test_generates_comparison_tables_and_charts(tmp_path) -> None:
    # Arrange
    record = {
        "query_id": "dense_01",
        "query_kind": "vector_dense",
        "limit": 2,
        "layout": "hash",
        "routing": "broadcast",
        "shard": None,
        "execution_order": "filter_then_search",
        "request_latency_ms": 5.0,
        "total_ms": 4.0,
        "vector_ms": 1.0,
        "enrichment_ms": 3.0,
        "http_status": 200,
        "ranking": [{"rank": 1, "movie_id": 20}],
        "relevant_movie_ids": [20],
    }
    requests_path = tmp_path / "requests.jsonl"
    requests_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    summary = {
        "configurations": [
            {
                "query_id": "dense_01",
                "query_kind": "vector_dense",
                "layout": "hash",
                "routing": "broadcast",
                "shard": None,
                "execution_order": "filter_then_search",
                "request_count": 1,
                "failure_count": 0,
                "request_latency_ms": {"mean": 5.0, "p95": 5.0, "p99": 5.0},
                "total_ms": {"mean": 4.0, "p95": 4.0, "p99": 4.0},
                "vector_ms": {"mean": 1.0, "p95": 1.0, "p99": 1.0},
                "enrichment_ms": {"mean": 3.0, "p95": 3.0, "p99": 3.0},
                "hit_at_k": 1.0,
                "precision_at_k": 0.5,
                "recall_at_k": 1.0,
                "f_measure_at_k": 2.0 / 3.0,
                "binary_ndcg_at_k": 1.0,
            }
        ]
    }
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    # Act
    generated_paths = generate_artifacts(tmp_path)

    # Assert
    assert len(generated_paths) == 4
    assert all(path.exists() for path in generated_paths)
    with (tmp_path / "strategy_comparison.csv").open(encoding="utf-8") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["strategy"] == "hash / broadcast / filter_then_search"
    assert rows[0]["binary_ndcg_at_k"] == "1.0"
    assert (tmp_path / "mean_total_latency.svg").read_text(encoding="utf-8").startswith("<svg")
    assert "Dense NDCG" in (tmp_path / "retrieval_quality.svg").read_text(encoding="utf-8")


def test_rejects_a_missing_run_directory_before_processing(tmp_path) -> None:
    # Arrange
    missing_run_directory = tmp_path / "missing-run"

    # Act
    with pytest.raises(NotADirectoryError) as error:
        generate_artifacts(missing_run_directory)

    # Assert
    assert str(error.value) == (
        f"run directory does not exist: {missing_run_directory}"
    )


def test_reports_all_missing_required_files_before_processing(tmp_path) -> None:
    # Arrange
    expected_output_paths = [
        tmp_path / "strategy_comparison.csv",
        tmp_path / "configuration_comparison.csv",
        tmp_path / "mean_total_latency.svg",
        tmp_path / "retrieval_quality.svg",
    ]

    # Act
    with pytest.raises(FileNotFoundError) as error:
        generate_artifacts(tmp_path)

    # Assert
    assert str(error.value) == (
        "required benchmark files are missing: requests.jsonl, summary.json"
    )
    assert all(not path.exists() for path in expected_output_paths)
