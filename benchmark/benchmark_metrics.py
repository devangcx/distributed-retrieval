"""Calculate correctness, retrieval quality, and timing benchmark metrics."""

import math
import statistics
from typing import Any


class BenchmarkMetrics:
    """Calculate metrics from the completed request records."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = [self.evaluate_record(record) for record in records]
        self.relational_records = self.records_for_kind("relational")
        self.dense_records = self.successful_records_for_kind("vector_dense")
        self.sparse_records = self.successful_records_for_kind("vector_sparse")
        self.configuration_groups = self.group_by_configuration()

    def records_for_kind(self, query_kind: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self.records
            if record["query_kind"] == query_kind
        ]

    def successful_records_for_kind(
        self, query_kind: str
    ) -> list[dict[str, Any]]:
        return [
            record
            for record in self.records_for_kind(query_kind)
            if record["http_status"] == 200
        ]

    def evaluate_record(self, record: dict[str, Any]) -> dict[str, Any]:
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

    def timing_statistics(
        self, records: list[dict[str, Any]], field: str
    ) -> dict[str, float | int | None]:
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
        }

    def metric_mean(
        self, records: list[dict[str, Any]], field: str
    ) -> float | None:
        values = [
            float(record[field])
            for record in records
            if record["http_status"] == 200 and record.get(field) is not None
        ]
        return self.mean(values)

    def configuration_summary(
        self, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
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
        parts = [record["layout"], record["routing"]]
        if record.get("execution_order") is not None:
            parts.append(record["execution_order"])
        return " / ".join(parts)

    def group_by_strategy(self) -> list[list[dict[str, Any]]]:
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

    def group_by_configuration(self) -> list[list[dict[str, Any]]]:
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
            "configurations": [
                self.configuration_summary(group)
                for group in self.configuration_groups
            ],
        }
