from benchmark.benchmark_metrics import BenchmarkMetrics


def benchmark_record(query_id, query_kind, http_status=200):
    return {
        "query_id": query_id,
        "query_kind": query_kind,
        "limit": 2,
        "layout": "hash",
        "routing": "broadcast",
        "shard": None,
        "execution_order": None,
        "request_latency_ms": 5.0,
        "total_ms": 4.0,
        "vector_ms": None,
        "enrichment_ms": None,
        "http_status": http_status,
        "ranking": [],
    }


def test_uses_record_state_to_calculate_each_query_type() -> None:
    # Arrange
    relational = benchmark_record("rel", "relational")
    relational["correctness"] = {
        "type": "sum_field",
        "field": "movie_count",
        "expected": 11,
    }
    relational["relational_results"] = [
        {"movie_count": 5},
        {"movie_count": 6},
    ]

    dense = benchmark_record("dense", "vector_dense")
    dense["relevant_movie_ids"] = [20, 30]
    dense["ranking"] = [
        {"rank": 1, "movie_id": 10},
        {"rank": 2, "movie_id": 20},
    ]

    sparse = benchmark_record("sparse", "vector_sparse")
    sparse["target_movie_id"] = 20
    sparse["ranking"] = [
        {"rank": 1, "movie_id": 10},
        {"rank": 2, "movie_id": 20},
    ]

    # Act
    metrics = BenchmarkMetrics([relational, dense, sparse])

    # Assert
    assert metrics.relational_records[0]["correct"] is True
    assert metrics.dense_records[0]["hit_at_k"] is True
    assert metrics.dense_records[0]["precision_at_k"] == 0.5
    assert metrics.dense_records[0]["recall_at_k"] == 0.5
    assert metrics.dense_records[0]["f_measure_at_k"] == 0.5
    assert 0.38 < metrics.dense_records[0]["binary_ndcg_at_k"] < 0.39
    assert metrics.sparse_records[0]["target_rank"] == 2
    assert metrics.sparse_records[0]["hit_at_k"] is True
    assert metrics.sparse_records[0]["reciprocal_rank"] == 0.5


def test_calculates_summary_from_stored_records() -> None:
    # Arrange
    relational_pass = benchmark_record("rel", "relational")
    relational_pass["correctness"] = {"type": "movie_ids", "expected": [1]}
    relational_pass["relational_results"] = [{"movie_id": 1}]
    relational_fail = dict(relational_pass)
    relational_fail["relational_results"] = [{"movie_id": 2}]

    dense = benchmark_record("dense", "vector_dense")
    dense["relevant_movie_ids"] = [20, 30]
    dense["ranking"] = [
        {"rank": 1, "movie_id": 10},
        {"rank": 2, "movie_id": 20},
    ]
    dense_failure = dict(dense)
    dense_failure["http_status"] = 500

    sparse_hit = benchmark_record("sparse", "vector_sparse")
    sparse_hit["target_movie_id"] = 20
    sparse_hit["ranking"] = [
        {"rank": 1, "movie_id": 10},
        {"rank": 2, "movie_id": 20},
    ]
    sparse_miss = dict(sparse_hit)
    sparse_miss["ranking"] = [{"rank": 1, "movie_id": 10}]

    # Act
    metrics = BenchmarkMetrics(
        [
            relational_pass,
            relational_fail,
            dense,
            dense_failure,
            sparse_hit,
            sparse_miss,
        ]
    )
    summary = metrics.calculate()

    # Assert
    assert summary["request_count"] == 6
    assert summary["failure_count"] == 1
    assert summary["relational_checks_passed"] == 1
    assert summary["relational_checks_total"] == 2
    assert summary["dense_hit_rate_at_k"] == 1.0
    assert summary["dense_mean_precision_at_k"] == 0.5
    assert summary["dense_mean_recall_at_k"] == 0.5
    assert summary["dense_mean_f_measure_at_k"] == 0.5
    assert 0.38 < summary["dense_mean_binary_ndcg_at_k"] < 0.39
    assert summary["known_item_hit_rate_at_k"] == 0.5
    assert summary["known_item_mean_reciprocal_rank"] == 0.25
    assert len(summary["configurations"]) == 3


def test_calculates_mean_and_nearest_rank_latency_percentiles() -> None:
    # Arrange
    records = []
    for value in range(1, 101):
        record = benchmark_record("dense", "vector_dense")
        record["request_latency_ms"] = value
        record["relevant_movie_ids"] = [1]
        records.append(record)
    metrics = BenchmarkMetrics(records)

    # Act
    timing = metrics.timing_statistics(records, "request_latency_ms")
    p95 = metrics.nearest_rank_percentile([1.0, 2.0, 3.0, 4.0], 0.95)

    # Assert
    assert timing == {
        "sample_count": 100,
        "mean": 50.5,
        "p95": 95.0,
        "p99": 99.0,
        "mean_95_confidence_interval": {
            "confidence_level": 0.95,
            "method": "percentile_bootstrap",
            "resampling_seed": 20260915,
            "resample_count": 10_000,
            "lower": 44.81,
            "upper": 56.24,
            "status": "available",
        },
    }
    assert p95 == 4.0


def test_bootstrap_mean_confidence_interval_is_reproducible() -> None:
    # Arrange
    records = [benchmark_record("dense", "vector_dense")]
    records[0]["relevant_movie_ids"] = [1]
    metrics = BenchmarkMetrics(
        records, bootstrap_seed=7, bootstrap_resamples=1_000
    )

    # Act
    first_interval = metrics.mean_confidence_interval([1.0, 2.0, 3.0, 4.0])
    second_interval = metrics.mean_confidence_interval([1.0, 2.0, 3.0, 4.0])

    # Assert
    assert first_interval == second_interval
    assert first_interval == {
        "confidence_level": 0.95,
        "method": "percentile_bootstrap",
        "resampling_seed": 7,
        "resample_count": 1_000,
        "lower": 1.5,
        "upper": 3.5,
        "status": "available",
    }


def test_reports_insufficient_samples_for_mean_confidence_interval() -> None:
    # Arrange
    record = benchmark_record("dense", "vector_dense")
    record["relevant_movie_ids"] = [1]
    metrics = BenchmarkMetrics([record])

    # Act
    summary = metrics.calculate()
    timing = summary["configurations"][0]["request_latency_ms"]

    # Assert
    assert timing["mean"] == 5.0
    assert timing["sample_count"] == 1
    assert timing["mean_95_confidence_interval"]["lower"] is None
    assert timing["mean_95_confidence_interval"]["upper"] is None
    assert (
        timing["mean_95_confidence_interval"]["status"]
        == "insufficient_samples"
    )
    assert "not treated as distinct logical queries" in (
        summary["confidence_intervals"]["independence_assumption"]
    )


def test_summarizes_records_by_retrieval_strategy() -> None:
    # Arrange
    first = benchmark_record("dense_one", "vector_dense")
    first["execution_order"] = "filter_then_search"
    first["relevant_movie_ids"] = [1]
    first["ranking"] = [{"rank": 1, "movie_id": 1}]
    second = dict(first)
    second["query_id"] = "dense_two"
    second["total_ms"] = 6.0

    # Act
    rows = BenchmarkMetrics([first, second]).strategy_rows()

    # Assert
    assert len(rows) == 1
    assert rows[0]["strategy"] == "hash / broadcast / filter_then_search"
    assert rows[0]["query_count"] == 2
    assert rows[0]["request_count"] == 2
    assert rows[0]["total_mean_ms"] == 5.0
    assert rows[0]["hit_rate_at_k"] == 1.0


def test_compares_strategies_by_resampling_matched_queries() -> None:
    # Arrange
    records = []
    timings = {
        "query_one": {"broadcast": [10.0, 14.0], "selective": [7.0, 9.0]},
        "query_two": {"broadcast": [20.0, 24.0], "selective": [21.0, 23.0]},
        "unmatched": {"broadcast": [100.0], "selective": []},
    }
    for query_id, strategies in timings.items():
        for routing, values in strategies.items():
            for value in values:
                record = benchmark_record(query_id, "relational")
                record["routing"] = routing
                record["request_latency_ms"] = value
                record["total_ms"] = value - 1.0
                record["correctness"] = {
                    "type": "movie_ids",
                    "expected": [1],
                }
                record["relational_results"] = [{"movie_id": 1}]
                records.append(record)
    metrics = BenchmarkMetrics(
        records, bootstrap_seed=3, bootstrap_resamples=100
    )

    # Act
    rows = metrics.strategy_comparison_rows()

    # Assert
    assert len(rows) == 1
    assert rows[0]["strategy_a"] == "hash / broadcast"
    assert rows[0]["strategy_b"] == "hash / selective"
    assert rows[0]["matched_query_count"] == 2
    assert rows[0]["matched_query_ids"] == ["query_one", "query_two"]
    assert rows[0]["client_mean_difference_ms"] == -2.0
    assert rows[0]["orchestrator_mean_difference_ms"] == -2.0
    assert rows[0]["client_mean_difference_95_confidence_interval"] == {
        "confidence_level": 0.95,
        "method": "percentile_bootstrap",
        "resampling_seed": 3,
        "resample_count": 100,
        "lower": -4.0,
        "upper": 0.0,
        "status": "available",
    }
    assert rows[0]["sampling_unit"] == "matched logical query"
    assert "Repetitions were averaged" in rows[0]["scope"]
