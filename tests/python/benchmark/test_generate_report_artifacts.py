import csv
import json

import pytest

from benchmark import generate_report_artifacts
from benchmark.generate_report_artifacts import (
    configuration_latency_chart_values,
    generate_artifacts,
    matched_latency_chart_values,
)


def test_generates_comparison_tables_and_charts(tmp_path, monkeypatch) -> None:
    # Arrange
    rendered_charts = []

    def write_test_chart(
        path,
        title,
        value_label,
        values,
        overwrite=False,
        show_zero_line=False,
        show_values=True,
    ):
        rendered_charts.append({
            "title": title,
            "value_label": value_label,
            "values": values,
            "show_zero_line": show_zero_line,
            "show_values": show_values,
        })
        path.write_bytes(b"test png")

    monkeypatch.setattr(
        generate_report_artifacts,
        "write_horizontal_bar_png",
        write_test_chart,
    )
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
    selective_record = dict(record)
    selective_record["routing"] = "selective"
    selective_record["shard"] = "a"
    selective_record["request_latency_ms"] = 4.0
    selective_record["total_ms"] = 3.0
    requests_path = tmp_path / "requests.jsonl"
    requests_path.write_text(
        json.dumps(record) + "\n" + json.dumps(selective_record) + "\n",
        encoding="utf-8",
    )
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
    assert len(generated_paths) == 6
    assert all(path.exists() for path in generated_paths)
    with (tmp_path / "strategy_comparison.csv").open(encoding="utf-8") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["strategy"] == "hash / broadcast / filter_then_search"
    assert rows[0]["binary_ndcg_at_k"] == "1.0"
    with (tmp_path / "configuration_comparison.csv").open(
        encoding="utf-8"
    ) as input_file:
        configuration_rows = list(csv.DictReader(input_file))
    assert configuration_rows[0]["total_ms_sample_count"] == "1"
    assert configuration_rows[0]["total_ms_mean_95_ci_status"] == (
        "insufficient_samples"
    )
    assert "interpret cautiously" in (
        configuration_rows[0]["total_ms_p99_qualification"]
    )
    with (tmp_path / "matched_strategy_comparison.csv").open(
        encoding="utf-8"
    ) as input_file:
        matched_rows = list(csv.DictReader(input_file))
    assert matched_rows[0]["matched_query_count"] == "1"
    assert matched_rows[0]["orchestrator_mean_difference_ms"] == "-1.0"
    assert (tmp_path / "mean_total_latency.png").read_bytes() == b"test png"
    assert rendered_charts[0]["values"][0] == (
        "dense_01: hash / broadcast / filter_then_search",
        4.0,
        None,
        None,
    )
    assert rendered_charts[2]["show_zero_line"] is True
    assert rendered_charts[0]["show_values"] is True
    assert rendered_charts[1]["show_values"] is True
    assert rendered_charts[2]["show_values"] is True
    assert (tmp_path / "matched_latency_difference.png").exists()
    assert rendered_charts[1]["values"][0][0].startswith("Dense NDCG")


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
        tmp_path / "matched_strategy_comparison.csv",
        tmp_path / "configuration_comparison.csv",
        tmp_path / "mean_total_latency.png",
        tmp_path / "retrieval_quality.png",
        tmp_path / "matched_latency_difference.png",
    ]

    # Act
    with pytest.raises(FileNotFoundError) as error:
        generate_artifacts(tmp_path)

    # Assert
    assert str(error.value) == (
        "required benchmark files are missing: requests.jsonl, summary.json"
    )
    assert all(not path.exists() for path in expected_output_paths)


def test_prepares_chart_data_without_plotly_rendering() -> None:
    # Arrange
    configurations = [{
        "query_id": "query_one",
        "layout": "hash",
        "routing": "broadcast",
        "execution_order": None,
        "total_ms_mean": 12.0,
        "total_ms_mean_95_ci_lower": 10.0,
        "total_ms_mean_95_ci_upper": 15.0,
    }]
    comparisons = [{
        "query_kind": "relational",
        "strategy_a": "hash / broadcast",
        "strategy_b": "industry / selective",
        "matched_query_count": 6,
        "orchestrator_mean_difference_ms": -3.0,
        "orchestrator_mean_difference_95_ci_lower": -5.0,
        "orchestrator_mean_difference_95_ci_upper": -1.0,
    }]

    # Act
    latency_values = configuration_latency_chart_values(configurations)
    difference_values = matched_latency_chart_values(comparisons)

    # Assert
    assert latency_values == [(
        "query_one: hash / broadcast / None",
        12.0,
        10.0,
        15.0,
    )]
    assert difference_values == [(
        "relational: hash / broadcast vs industry / selective (n=6)",
        -3.0,
        -5.0,
        -1.0,
    )]
