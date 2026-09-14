"""Create report-ready comparison tables and charts from one benchmark run."""

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any

if __package__:
    from benchmark.benchmark_metrics import BenchmarkMetrics
else:
    from benchmark_metrics import BenchmarkMetrics


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        records.append(json.loads(line))
    return records


def configuration_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
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
            for statistic in ("mean", "p95", "p99"):
                row[f"{timing_name}_{statistic}"] = timing[statistic]
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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot write an empty comparison table")
    with path.open("x", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_bar_chart(
    path: Path,
    title: str,
    value_label: str,
    values: list[tuple[str, float]],
) -> None:
    width = 1200
    left_margin = 390
    right_margin = 50
    top_margin = 90
    row_height = 38
    height = top_margin + row_height * len(values) + 60
    largest_value = max(value for _, value in values)
    chart_width = width - left_margin - right_margin
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2}" y="35" text-anchor="middle" font-family="sans-serif" font-size="22">{html.escape(title)}</text>',
        f'<text x="{left_margin + chart_width / 2}" y="65" text-anchor="middle" font-family="sans-serif" font-size="14">{html.escape(value_label)}</text>',
    ]
    for index, (label, value) in enumerate(values):
        y = top_margin + index * row_height
        bar_width = 0.0
        if largest_value > 0.0:
            bar_width = chart_width * value / largest_value
        svg_lines.append(
            f'<text x="{left_margin - 12}" y="{y + 19}" text-anchor="end" font-family="sans-serif" font-size="13">{html.escape(label)}</text>'
        )
        svg_lines.append(
            f'<rect x="{left_margin}" y="{y + 4}" width="{bar_width:.2f}" height="22" fill="#3975a8"/>'
        )
        svg_lines.append(
            f'<text x="{left_margin + bar_width + 8:.2f}" y="{y + 20}" font-family="sans-serif" font-size="13">{value:.3f}</text>'
        )
    svg_lines.append("</svg>")
    with path.open("x", encoding="utf-8") as output_file:
        output_file.write("\n".join(svg_lines) + "\n")


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


def generate_artifacts(run_directory: Path) -> list[Path]:
    requests_path = run_directory / "requests.jsonl"
    summary_path = run_directory / "summary.json"
    strategy_path = run_directory / "strategy_comparison.csv"
    configuration_path = run_directory / "configuration_comparison.csv"
    latency_chart_path = run_directory / "mean_total_latency.svg"
    quality_chart_path = run_directory / "retrieval_quality.svg"

    input_paths = [requests_path, summary_path]
    output_paths = [
        strategy_path,
        configuration_path,
        latency_chart_path,
        quality_chart_path,
    ]

    validate_artifact_paths(run_directory, input_paths, output_paths)

    records = load_records(requests_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = BenchmarkMetrics(records)
    strategies = metrics.strategy_rows()

    write_csv(strategy_path, strategies)
    write_csv(configuration_path, configuration_rows(summary))
    latency_values = [
        (f'{row["query_kind"]}: {row["strategy"]}', row["total_mean_ms"])
        for row in strategies
        if row["total_mean_ms"] is not None
    ]
    write_bar_chart(
        latency_chart_path,
        "Mean orchestrator latency by retrieval strategy",
        "Mean total_ms across successful requests (milliseconds)",
        latency_values,
    )
    quality_values = []
    for row in strategies:
        if row["query_kind"] == "vector_dense" and row["binary_ndcg_at_k"] is not None:
            quality_values.append(
                (f'Dense NDCG: {row["strategy"]}', row["binary_ndcg_at_k"])
            )
        if row["query_kind"] == "vector_sparse" and row["mean_reciprocal_rank"] is not None:
            quality_values.append(
                (f'Sparse MRR: {row["strategy"]}', row["mean_reciprocal_rank"])
            )
    write_bar_chart(
        quality_chart_path,
        "Retrieval quality by strategy",
        "Binary NDCG@K for dense; MRR for sparse",
        quality_values,
    )
    return output_paths


def main() -> None:
    arguments = parse_arguments()
    generated_paths = generate_artifacts(arguments.run_directory)
    for path in generated_paths:
        print(f"saved {path}")


if __name__ == "__main__":
    main()
