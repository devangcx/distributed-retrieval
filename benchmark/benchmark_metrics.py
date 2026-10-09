"""Calculate correctness, retrieval quality, and timing benchmark metrics."""

import math
import random
import statistics
from itertools import combinations
from typing import Any


CONFIDENCE_LEVEL = 0.95
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20260915
MINIMUM_CONFIDENCE_INTERVAL_SAMPLES = 2


class BenchmarkMetrics:
    """Calculate metrics from the completed request records."""

    def __init__(
        self,
        records: list[dict[str, Any]],
        bootstrap_seed: int = BOOTSTRAP_SEED,
        bootstrap_resamples: int = BOOTSTRAP_RESAMPLES,
    ) -> None:
        """Evaluate copies of request records and group them for summaries.

        Reject a resample count below one. Failed requests retain their original fields.
        """
        if bootstrap_resamples < 1:
            raise ValueError("bootstrap_resamples must be at least 1")
        self.bootstrap_seed = bootstrap_seed
        self.bootstrap_resamples = bootstrap_resamples
        self.records = [self.evaluate_record(record) for record in records]
        self.relational_records = self.records_for_kind("relational")
        self.dense_records = self.successful_records_for_kind("vector_dense")
        self.sparse_records = self.successful_records_for_kind("vector_sparse")
        self.configuration_groups = self.group_by_configuration()

    def records_for_kind(self, query_kind: str) -> list[dict[str, Any]]:
        """Select one query kind, retaining both successful and failed requests."""
        return [
            record
            for record in self.records
            if record["query_kind"] == query_kind
        ]

    def successful_records_for_kind(
        self, query_kind: str
    ) -> list[dict[str, Any]]:
        """Select only HTTP 200 records for one query kind."""
        return [
            record
            for record in self.records_for_kind(query_kind)
            if record["http_status"] == 200
        ]

    def evaluate_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return a copy with correctness or retrieval metrics for successful requests."""
        evaluated_record = dict(record)
        if record["http_status"] != 200:
            return evaluated_record

        query_kind = record["query_kind"]
        if query_kind == "relational":
            evaluated_record["correct"] = self.relational_correctness(record)
        if query_kind == "vector_dense":
            evaluated_record.update(self.dense_metrics(record))
        if query_kind == "vector_sparse":
            rank = self.target_rank(record)
            evaluated_record["target_rank"] = rank
            evaluated_record["hit_at_k"] = (
                rank is not None and rank <= record["limit"]
            )
            evaluated_record["reciprocal_rank"] = (
                0.0 if rank is None else 1.0 / rank
            )
        return evaluated_record

    def relational_correctness(self, record: dict[str, Any]) -> bool:
        """Compare a relational response with its fixed expected answer.

        Broadcast count queries return one row per shard, so ``sum_field`` adds
        the shard values before comparing them. ``movie_ids`` compares the
        complete ID set without depending on the order returned by layouts.
        """
        correctness = record["correctness"]
        results = record["relational_results"]
        if correctness["type"] == "sum_field":
            actual = sum(int(result[correctness["field"]]) for result in results)
            return actual == correctness["expected"]
        if correctness["type"] == "movie_ids":
            actual = sorted(int(result["movie_id"]) for result in results)
            expected = sorted(int(movie_id) for movie_id in correctness["expected"])
            return actual == expected
        raise ValueError(f"unknown relational correctness type {correctness['type']}")

    def target_rank(self, record: dict[str, Any]) -> int | None:
        """Return the saved one-based target rank, or None if the target is absent."""
        for result in record["ranking"]:
            if int(result["movie_id"]) == record["target_movie_id"]:
                return int(result["rank"])
        return None

    def binary_ndcg_at_k(self, record: dict[str, Any]) -> float:
        """Measure how early binary-relevant movies appear in the first K results."""
        relevant_ids = set(record["relevant_movie_ids"])
        limit = record["limit"]
        discounted_gain = 0.0
        for result in record["ranking"][:limit]:
            if int(result["movie_id"]) in relevant_ids:
                discounted_gain += 1.0 / math.log2(int(result["rank"]) + 1)

        ideal_relevant_count = min(len(relevant_ids), limit)
        ideal_discounted_gain = sum(
            1.0 / math.log2(rank + 1)
            for rank in range(1, ideal_relevant_count + 1)
        )
        if ideal_discounted_gain == 0.0:
            return 0.0
        return discounted_gain / ideal_discounted_gain

    def dense_metrics(self, record: dict[str, Any]) -> dict[str, float | bool]:
        """Calculate binary retrieval metrics against the fixed relevance set."""
        limit = record["limit"]
        returned_ids = {
            int(result["movie_id"])
            for result in record["ranking"][:limit]
        }
        relevant_ids = set(record["relevant_movie_ids"])
        relevant_returned = len(returned_ids.intersection(relevant_ids))

        # Precision@K uses K as its denominator, even when fewer than K rows return.
        precision = relevant_returned / limit
        recall = relevant_returned / len(relevant_ids)
        f_measure = 0.0
        if precision + recall > 0.0:
            f_measure = 2.0 * precision * recall / (precision + recall)

        return {
            "hit_at_k": relevant_returned > 0,
            "precision_at_k": precision,
            "recall_at_k": recall,
            "f_measure_at_k": f_measure,
            "binary_ndcg_at_k": self.binary_ndcg_at_k(record),
        }

    def mean(self, values: list[float]) -> float | None:
        """Return the arithmetic mean, or None when there are no observations."""
        if not values:
            return None
        return statistics.fmean(values)

    def nearest_rank_percentile(
        self, values: list[float], percentile: float
    ) -> float | None:
        """Return a percentile using the explicit nearest-rank definition."""
        if not values:
            return None
        ordered_values = sorted(values)
        rank = math.ceil(percentile * len(ordered_values))
        return ordered_values[rank - 1]

    def mean_confidence_interval(
        self, values: list[float]
    ) -> dict[str, float | int | str | None]:
        """Estimate a reproducible percentile-bootstrap interval for the mean."""
        interval: dict[str, float | int | str | None] = {
            "confidence_level": CONFIDENCE_LEVEL,
            "method": "percentile_bootstrap",
            "resampling_seed": self.bootstrap_seed,
            "resample_count": self.bootstrap_resamples,
            "lower": None,
            "upper": None,
            "status": "insufficient_samples",
        }
        if len(values) < MINIMUM_CONFIDENCE_INTERVAL_SAMPLES:
            return interval

        random_generator = random.Random(self.bootstrap_seed)
        sample_size = len(values)
        bootstrap_means = []
        for _resample_number in range(self.bootstrap_resamples):
            resample = random_generator.choices(values, k=sample_size)
            bootstrap_means.append(statistics.fmean(resample))

        tail_probability = (1.0 - CONFIDENCE_LEVEL) / 2.0
        interval["lower"] = self.nearest_rank_percentile(
            bootstrap_means, tail_probability
        )
        interval["upper"] = self.nearest_rank_percentile(
            bootstrap_means, 1.0 - tail_probability
        )
        interval["status"] = "available"
        return interval

    def timing_statistics(
        self, records: list[dict[str, Any]], field: str
    ) -> dict[str, Any]:
        """Summarize available timings from HTTP 200 records, excluding failures."""
        values = [
            float(record[field])
            for record in records
            if record["http_status"] == 200 and record.get(field) is not None
        ]
        return {
            "sample_count": len(values),
            "mean": self.mean(values),
            "p95": self.nearest_rank_percentile(values, 0.95),
            "p99": self.nearest_rank_percentile(values, 0.99),
            "mean_95_confidence_interval": self.mean_confidence_interval(
                values
            ),
        }

    def metric_mean(
        self, records: list[dict[str, Any]], field: str
    ) -> float | None:
        """Average available metric values from HTTP 200 records, or return None."""
        values = [
            float(record[field])
            for record in records
            if record["http_status"] == 200 and record.get(field) is not None
        ]
        return self.mean(values)

    def configuration_summary(
        self, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Summarize a nonempty group for one query and configuration.

        Failures contribute to counts but are excluded from timing and retrieval means.
        """
        first = records[0]
        summary: dict[str, Any] = {
            "query_id": first["query_id"],
            "query_kind": first["query_kind"],
            "layout": first["layout"],
            "routing": first["routing"],
            "shard": first["shard"],
            "execution_order": first["execution_order"],
            "request_count": len(records),
            "failure_count": sum(
                record["http_status"] != 200 for record in records
            ),
            "request_latency_ms": self.timing_statistics(
                records, "request_latency_ms"
            ),
            "total_ms": self.timing_statistics(records, "total_ms"),
            "vector_ms": self.timing_statistics(records, "vector_ms"),
            "enrichment_ms": self.timing_statistics(records, "enrichment_ms"),
        }

        query_kind = first["query_kind"]
        if query_kind == "relational":
            summary["correct_runs"] = sum(
                record.get("correct") is True for record in records
            )
        if query_kind == "vector_dense":
            for field in (
                "hit_at_k",
                "precision_at_k",
                "recall_at_k",
                "f_measure_at_k",
                "binary_ndcg_at_k",
            ):
                summary[field] = self.metric_mean(records, field)
        if query_kind == "vector_sparse":
            summary["hit_at_k"] = self.metric_mean(records, "hit_at_k")
            summary["mean_reciprocal_rank"] = self.metric_mean(
                records, "reciprocal_rank"
            )
        return summary

    def strategy_name(self, record: dict[str, Any]) -> str:
        """Label a strategy by layout, routing, and optional execution order."""
        parts = [record["layout"], record["routing"]]
        if record.get("execution_order") is not None:
            parts.append(record["execution_order"])
        return " / ".join(parts)

    def group_by_strategy(self) -> list[list[dict[str, Any]]]:
        """Group by query kind, layout, routing, and execution order across queries."""
        groups: dict[
            tuple[str, str, str, str | None],
            list[dict[str, Any]],
        ] = {}
        for record in self.records:
            key = (
                record["query_kind"],
                record["layout"],
                record["routing"],
                record.get("execution_order"),
            )
            if key not in groups:
                groups[key] = []
            groups[key].append(record)
        return list(groups.values())

    def strategy_rows(self) -> list[dict[str, Any]]:
        """Summarize records with the same retrieval strategy."""
        rows = []
        for records in self.group_by_strategy():
            first = records[0]
            request_latency = self.timing_statistics(
                records, "request_latency_ms"
            )
            total_time = self.timing_statistics(records, "total_ms")
            vector_time = self.timing_statistics(records, "vector_ms")
            enrichment_time = self.timing_statistics(records, "enrichment_ms")
            row = {
                "query_kind": first["query_kind"],
                "strategy": self.strategy_name(first),
                "query_count": len(
                    {record["query_id"] for record in records}
                ),
                "request_count": len(records),
                "failure_count": sum(
                    record["http_status"] != 200 for record in records
                ),
                "client_mean_ms": request_latency["mean"],
                "client_p95_ms": request_latency["p95"],
                "client_p99_ms": request_latency["p99"],
                "total_mean_ms": total_time["mean"],
                "total_p95_ms": total_time["p95"],
                "total_p99_ms": total_time["p99"],
                "vector_mean_ms": vector_time["mean"],
                "enrichment_mean_ms": enrichment_time["mean"],
                "hit_rate_at_k": self.metric_mean(records, "hit_at_k"),
                "precision_at_k": self.metric_mean(
                    records, "precision_at_k"
                ),
                "recall_at_k": self.metric_mean(records, "recall_at_k"),
                "f_measure_at_k": self.metric_mean(
                    records, "f_measure_at_k"
                ),
                "binary_ndcg_at_k": self.metric_mean(
                    records, "binary_ndcg_at_k"
                ),
                "mean_reciprocal_rank": self.metric_mean(
                    records, "reciprocal_rank"
                ),
            }
            rows.append(row)
        return sorted(
            rows,
            key=lambda row: (row["query_kind"], row["strategy"]),
        )

    def per_query_timing_means(
        self, records: list[dict[str, Any]], field: str
    ) -> dict[str, float]:
        """Average repetitions so every logical query has equal weight."""
        values_by_query: dict[str, list[float]] = {}
        for record in records:
            if record["http_status"] != 200 or record.get(field) is None:
                continue
            query_id = record["query_id"]
            if query_id not in values_by_query:
                values_by_query[query_id] = []
            values_by_query[query_id].append(float(record[field]))
        return {
            query_id: statistics.fmean(values)
            for query_id, values in values_by_query.items()
        }

    def strategy_comparison_row(
        self,
        first_records: list[dict[str, Any]],
        second_records: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Compare two strategies using only matched logical queries."""
        first_client = self.per_query_timing_means(
            first_records, "request_latency_ms"
        )
        second_client = self.per_query_timing_means(
            second_records, "request_latency_ms"
        )
        first_orchestrator = self.per_query_timing_means(
            first_records, "total_ms"
        )
        second_orchestrator = self.per_query_timing_means(
            second_records, "total_ms"
        )
        matched_query_ids = sorted(
            set(first_client)
            .intersection(second_client)
            .intersection(first_orchestrator)
            .intersection(second_orchestrator)
        )
        if not matched_query_ids:
            return None

        client_differences = [
            second_client[query_id] - first_client[query_id]
            for query_id in matched_query_ids
        ]
        orchestrator_differences = [
            second_orchestrator[query_id] - first_orchestrator[query_id]
            for query_id in matched_query_ids
        ]
        return {
            "query_kind": first_records[0]["query_kind"],
            "strategy_a": self.strategy_name(first_records[0]),
            "strategy_b": self.strategy_name(second_records[0]),
            "difference_direction": "strategy_b_minus_strategy_a",
            "matched_query_count": len(matched_query_ids),
            "matched_query_ids": matched_query_ids,
            "client_mean_difference_ms": self.mean(client_differences),
            "client_mean_difference_95_confidence_interval": (
                self.mean_confidence_interval(client_differences)
            ),
            "orchestrator_mean_difference_ms": self.mean(
                orchestrator_differences
            ),
            "orchestrator_mean_difference_95_confidence_interval": (
                self.mean_confidence_interval(orchestrator_differences)
            ),
            "sampling_unit": "matched logical query",
            "scope": (
                "This comparison describes only the selected benchmark "
                "workload shared by both strategies. Repetitions were averaged "
                "within each query before comparing strategies."
            ),
        }

    def strategy_comparison_rows(self) -> list[dict[str, Any]]:
        """Build pairwise, matched-query comparisons within each query kind."""
        groups_by_kind: dict[str, list[list[dict[str, Any]]]] = {}
        for records in self.group_by_strategy():
            query_kind = records[0]["query_kind"]
            if query_kind not in groups_by_kind:
                groups_by_kind[query_kind] = []
            groups_by_kind[query_kind].append(records)

        rows = []
        for query_kind in sorted(groups_by_kind):
            strategy_groups = sorted(
                groups_by_kind[query_kind],
                key=lambda records: self.strategy_name(records[0]),
            )
            for first_records, second_records in combinations(
                strategy_groups, 2
            ):
                row = self.strategy_comparison_row(
                    first_records, second_records
                )
                if row is not None:
                    rows.append(row)
        return rows

    def group_by_configuration(self) -> list[list[dict[str, Any]]]:
        """Group repetitions by query ID, layout, routing, shard, and execution order."""
        groups: dict[
            tuple[str, str, str, str | None, str | None],
            list[dict[str, Any]],
        ] = {}
        for record in self.records:
            key = (
                record["query_id"],
                record["layout"],
                record["routing"],
                record["shard"],
                record["execution_order"],
            )
            if key not in groups:
                groups[key] = []
            groups[key].append(record)
        return list(groups.values())

    def calculate(self) -> dict[str, Any]:
        """Calculate the complete summary for all measured request records."""
        return {
            "confidence_intervals": {
                "confidence_level": CONFIDENCE_LEVEL,
                "method": "percentile bootstrap of the arithmetic mean",
                "resampling_seed": self.bootstrap_seed,
                "resample_count": self.bootstrap_resamples,
                "minimum_sample_count": MINIMUM_CONFIDENCE_INTERVAL_SAMPLES,
                "sampling_unit": (
                    "successful repeated timings within one logical query and "
                    "configuration"
                ),
                "independence_assumption": (
                    "Repeated timings are treated as independent observations "
                    "within each query/configuration. They are not treated as "
                    "distinct logical queries."
                ),
            },
            "request_count": len(self.records),
            "failure_count": sum(
                record["http_status"] != 200 for record in self.records
            ),
            "relational_checks_total": len(self.relational_records),
            "relational_checks_passed": sum(
                record.get("correct") is True
                for record in self.relational_records
            ),
            "dense_successful_runs": len(self.dense_records),
            "dense_hit_rate_at_k": self.metric_mean(
                self.dense_records, "hit_at_k"
            ),
            "dense_mean_precision_at_k": self.metric_mean(
                self.dense_records, "precision_at_k"
            ),
            "dense_mean_recall_at_k": self.metric_mean(
                self.dense_records, "recall_at_k"
            ),
            "dense_mean_f_measure_at_k": self.metric_mean(
                self.dense_records, "f_measure_at_k"
            ),
            "dense_mean_binary_ndcg_at_k": self.metric_mean(
                self.dense_records, "binary_ndcg_at_k"
            ),
            "known_item_successful_runs": len(self.sparse_records),
            "known_item_hit_rate_at_k": self.metric_mean(
                self.sparse_records, "hit_at_k"
            ),
            "known_item_mean_reciprocal_rank": self.metric_mean(
                self.sparse_records, "reciprocal_rank"
            ),
            "strategy_comparisons": self.strategy_comparison_rows(),
            "configurations": [
                self.configuration_summary(group)
                for group in self.configuration_groups
            ],
        }
