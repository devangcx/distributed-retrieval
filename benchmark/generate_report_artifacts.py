"""Create report-ready comparison tables and charts from one benchmark run."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

if __package__:
    from benchmark.benchmark_metrics import BenchmarkMetrics
    from benchmark.plotly_charts import ChartValue, write_horizontal_bar_png
else:
    from benchmark_metrics import BenchmarkMetrics
    from plotly_charts import ChartValue, write_horizontal_bar_png


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace report artifacts already present in the run directory",
    )
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        records.append(json.loads(line))
    return records


def configuration_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten per-configuration metrics and confidence intervals into table rows."""
    rows = []
    for configuration in summary["configurations"]:
        row = {
            "query_id": configuration["query_id"],
            "query_kind": configuration["query_kind"],
            "layout": configuration["layout"],
            "routing": configuration["routing"],
            "shard": configuration["shard"],
            "execution_order": configuration["execution_order"],
            "request_count": configuration["request_count"],
            "failure_count": configuration["failure_count"],
        }
        for timing_name in (
            "request_latency_ms",
            "total_ms",
            "vector_ms",
            "enrichment_ms",
        ):
            timing = configuration[timing_name]
            row[f"{timing_name}_sample_count"] = timing["sample_count"]
            for statistic in ("mean", "p95", "p99"):
                row[f"{timing_name}_{statistic}"] = timing[statistic]
            interval = timing["mean_95_confidence_interval"]
            row[f"{timing_name}_mean_95_ci_lower"] = interval["lower"]
            row[f"{timing_name}_mean_95_ci_upper"] = interval["upper"]
            row[f"{timing_name}_mean_95_ci_status"] = interval["status"]
            row[f"{timing_name}_p99_qualification"] = (
                p99_qualification(timing["sample_count"])
            )
        for metric_name in (
            "correct_runs",
            "hit_at_k",
            "precision_at_k",
            "recall_at_k",
            "f_measure_at_k",
            "binary_ndcg_at_k",
            "mean_reciprocal_rank",
        ):
            row[metric_name] = configuration.get(metric_name)
        rows.append(row)
    return rows


def p99_qualification(sample_count: int) -> str:
    """Explain the limited precision of P99 when at most 30 timings are available."""
    if sample_count <= 30:
        return (
            f"P99 is the maximum or near-maximum of only {sample_count} "
            "timings; interpret cautiously."
        )
    return ""


def matched_comparison_rows(metrics: BenchmarkMetrics) -> list[dict[str, Any]]:
    """Flatten matched-query strategy differences and intervals into table rows."""
    rows = []
    for comparison in metrics.strategy_comparison_rows():
        client_interval = comparison[
            "client_mean_difference_95_confidence_interval"
        ]
        orchestrator_interval = comparison[
            "orchestrator_mean_difference_95_confidence_interval"
        ]
        rows.append({
            "query_kind": comparison["query_kind"],
            "strategy_a": comparison["strategy_a"],
            "strategy_b": comparison["strategy_b"],
            "difference_direction": comparison["difference_direction"],
            "matched_query_count": comparison["matched_query_count"],
            "matched_query_ids": ";".join(comparison["matched_query_ids"]),
            "client_mean_difference_ms": comparison[
                "client_mean_difference_ms"
            ],
            "client_mean_difference_95_ci_lower": client_interval["lower"],
            "client_mean_difference_95_ci_upper": client_interval["upper"],
            "client_mean_difference_95_ci_status": client_interval["status"],
            "orchestrator_mean_difference_ms": comparison[
                "orchestrator_mean_difference_ms"
            ],
            "orchestrator_mean_difference_95_ci_lower": (
                orchestrator_interval["lower"]
            ),
            "orchestrator_mean_difference_95_ci_upper": (
                orchestrator_interval["upper"]
            ),
            "orchestrator_mean_difference_95_ci_status": (
                orchestrator_interval["status"]
            ),
            "resampling_seed": orchestrator_interval["resampling_seed"],
            "resample_count": orchestrator_interval["resample_count"],
            "sampling_unit": comparison["sampling_unit"],
            "scope": comparison["scope"],
        })
    return rows


def write_csv(
    path: Path, rows: list[dict[str, Any]], overwrite: bool = False
) -> None:
    """Write a nonempty table using the first row's keys as columns.

    Raise ValueError for empty input. Refuse existing files unless overwrite is set.
    """
    if not rows:
        raise ValueError("cannot write an empty comparison table")
    mode = "w" if overwrite else "x"
    with path.open(mode, encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def configuration_latency_chart_values(
    configurations: list[dict[str, Any]],
) -> list[ChartValue]:
    """Extract available orchestrator means and intervals, skipping missing means."""
    return [
        (
            f'{row["query_id"]}: {row["layout"]} / {row["routing"]} / {row["execution_order"]}',
            row["total_ms_mean"],
            row["total_ms_mean_95_ci_lower"],
            row["total_ms_mean_95_ci_upper"],
        )
        for row in configurations
        if row["total_ms_mean"] is not None
    ]


def retrieval_quality_chart_values(
    strategies: list[dict[str, Any]],
) -> list[ChartValue]:
    """Extract dense ranking quality and sparse reciprocal rank without intervals."""
    values = []
    for row in strategies:
        if (
            row["query_kind"] == "vector_dense"
            and row["binary_ndcg_at_k"] is not None
        ):
            values.append((
                f'Dense NDCG: {row["strategy"]}',
                row["binary_ndcg_at_k"],
                None,
                None,
            ))
        if (
            row["query_kind"] == "vector_sparse"
            and row["mean_reciprocal_rank"] is not None
        ):
            values.append((
                f'Sparse MRR: {row["strategy"]}',
                row["mean_reciprocal_rank"],
                None,
                None,
            ))
    return values


def matched_latency_chart_values(
    comparisons: list[dict[str, Any]],
) -> list[ChartValue]:
    """Extract paired orchestrator differences and intervals, using B minus A."""
    return [
        (
            f'{row["query_kind"]}: {row["strategy_a"]} vs {row["strategy_b"]} (n={row["matched_query_count"]})',
            row["orchestrator_mean_difference_ms"],
            row["orchestrator_mean_difference_95_ci_lower"],
            row["orchestrator_mean_difference_95_ci_upper"],
        )
        for row in comparisons
    ]


def validate_artifact_paths(
    run_directory: Path,
    input_paths: list[Path],
    output_paths: list[Path],
) -> None:
    """Check every required path before reading or calculating run data."""
    if not run_directory.is_dir():
        raise NotADirectoryError(
            f"run directory does not exist: {run_directory}")

    missing_paths = [path for path in input_paths if not path.is_file()]
    if missing_paths:
        missing_names = ", ".join(path.name for path in missing_paths)
        raise FileNotFoundError(
            f"required benchmark files are missing: {missing_names}")

    existing_paths = [path for path in output_paths if path.exists()]
    if existing_paths:
        raise FileExistsError(
            f"report artifact already exists: {existing_paths[0]}")


def generate_artifacts(
    run_directory: Path, overwrite: bool = False
) -> list[Path]:
    """Recalculate metrics from saved requests and return written table/chart paths.

    Require requests.jsonl and summary.json. Refuse existing outputs unless
    overwrite is set. Raw requests and the saved summary are left unchanged.
    """
    requests_path = run_directory / "requests.jsonl"
    summary_path = run_directory / "summary.json"
    strategy_path = run_directory / "strategy_comparison.csv"
    matched_strategy_path = run_directory / "matched_strategy_comparison.csv"
    configuration_path = run_directory / "configuration_comparison.csv"
    latency_chart_path = run_directory / "mean_total_latency.png"
    quality_chart_path = run_directory / "retrieval_quality.png"
    matched_chart_path = run_directory / "matched_latency_difference.png"

    input_paths = [requests_path, summary_path]
    output_paths = [
        strategy_path,
        matched_strategy_path,
        configuration_path,
        latency_chart_path,
        quality_chart_path,
        matched_chart_path,
    ]

    paths_to_validate = [] if overwrite else output_paths
    validate_artifact_paths(run_directory, input_paths, paths_to_validate)

    records = load_records(requests_path)
    metrics = BenchmarkMetrics(records)
    summary = metrics.calculate()
    strategies = metrics.strategy_rows()
    comparisons = matched_comparison_rows(metrics)

    write_csv(strategy_path, strategies, overwrite)
    write_csv(matched_strategy_path, comparisons, overwrite)
    configurations = configuration_rows(summary)
    write_csv(configuration_path, configurations, overwrite)
    latency_values = configuration_latency_chart_values(configurations)
    write_horizontal_bar_png(
        latency_chart_path,
        "Mean orchestrator latency by query and configuration",
        "Mean total_ms with 95% confidence intervals (milliseconds)",
        latency_values,
        overwrite=overwrite,
    )
    quality_values = retrieval_quality_chart_values(strategies)
    write_horizontal_bar_png(
        quality_chart_path,
        "Retrieval quality by strategy",
        "Binary NDCG@K for dense; MRR for sparse",
        quality_values,
        overwrite=overwrite,
    )
    matched_values = matched_latency_chart_values(comparisons)
    write_horizontal_bar_png(
        matched_chart_path,
        "Matched-query orchestrator latency differences",
        "Strategy B minus strategy A (milliseconds); 95% confidence intervals",
        matched_values,
        overwrite=overwrite,
        show_zero_line=True,
    )
    return output_paths


def main() -> None:
    """Generate report tables and charts from the command-line run directory."""
    arguments = parse_arguments()
    generated_paths = generate_artifacts(
        arguments.run_directory, overwrite=arguments.overwrite
    )
    for path in generated_paths:
        print(f"saved {path}")


if __name__ == "__main__":
    main()
