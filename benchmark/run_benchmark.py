"""Run the fixed benchmark queries against an already running orchestrator."""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, TextIO

if __package__:
    from benchmark.benchmark_metrics import BenchmarkMetrics
else:
    # Direct script execution places the benchmark folder, not the repository
    # root, on Python's import path.
    from benchmark_metrics import BenchmarkMetrics

BENCHMARK_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_QUERY_PATH = BENCHMARK_DIRECTORY / "queries.json"
DEFAULT_REPRESENTATION_PATH = BENCHMARK_DIRECTORY / "representations.json"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:3000/query")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERY_PATH)
    parser.add_argument(
        "--representations",
        type=Path,
        default=DEFAULT_REPRESENTATION_PATH,
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument(
        "--warmups",
        type=int,
        default=1,
        help=(
            "unmeasured requests sent before each configuration so connection "
            "pools, database pages, and indexes are ready before timing begins"
        ),
    )
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def configured_queries(
    query_document: dict[str, Any],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Create one item for every request the runner needs to send.

    For example, a query with two configurations becomes two items. The first
    item contains the query and its first configuration. The second item
    contains the same query and its second configuration. The runner later
    sends one request for each item.

    Each item also includes the query type. If a query has no limit, its copy
    receives the default limit from the top of ``queries.json``. The original
    query is not changed.
    """
    configured: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    default_limit = query_document["default_limit"]
    for query_kind, queries in query_document["queries"].items():
        for query in queries:
            query_with_defaults = dict(query)
            if "limit" not in query_with_defaults:
                query_with_defaults["limit"] = default_limit
            for configuration in query["configurations"]:
                configured.append((query_kind, query_with_defaults, configuration))
    return configured


def build_request(
    query_kind: str,
    query: dict[str, Any],
    configuration: dict[str, Any],
    representations: dict[str, Any],
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "query_type": query_kind,
        "layout": configuration["layout"],
        "routing": configuration["routing"],
        "limit": query["limit"],
    }
    if "shard" in configuration:
        request["shard"] = configuration["shard"]

    if query_kind == "relational":
        request["sql"] = query["sql"]
        return request

    request["execution_order"] = configuration["execution_order"]
    if "filter" in query:
        request["filter"] = query["filter"]

    representation = representations[query["id"]]
    if query_kind == "vector_dense":
        request["vector_name"] = query["vector_name"]
        request["vector"] = representation["vector"]
    else:
        request["indices"] = representation["indices"]
        request["values"] = representation["values"]
    return request


def request_once(
    url: str, request_body: dict[str, Any], timeout_seconds: float
) -> dict[str, Any]:
    # Compact JSON reduces the request size, especially for 1,024-value vectors.
    encoded_body = json.dumps(
        request_body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # The client timer covers the complete HTTP round trip and response decoding.
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = json.loads(response.read().decode("utf-8"))
            status = response.status
            failure = None
    except urllib.error.HTTPError as error:
        # HTTP failures retain the status and server message for later analysis.
        status = error.code
        failure = error.read().decode("utf-8")
        response_body = None
    except (urllib.error.URLError, TimeoutError) as error:
        # Connection and timeout failures have no HTTP response status or body.
        status = None
        failure = str(error)
        response_body = None
    request_latency_ms = (time.perf_counter() - started) * 1000.0
    return {
        "request_latency_ms": request_latency_ms,
        "http_status": status,
        "response": response_body,
        "failure": failure,
    }


def contacted_shards(configuration: dict[str, Any]) -> list[str]:
    if configuration["routing"] == "broadcast":
        return ["a", "b"]
    return [configuration["shard"]]


def build_record(
    query_kind: str,
    query: dict[str, Any],
    configuration: dict[str, Any],
    repetition: int,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    response = outcome["response"]
    if not isinstance(response, dict):
        response = {}
    results = response.get("results", [])

    record: dict[str, Any] = {
        "query_id": query["id"],
        "query_kind": query_kind,
        "limit": query["limit"],
        "repetition": repetition,
        "layout": configuration["layout"],
        "routing": configuration["routing"],
        "shard": configuration.get("shard"),
        "execution_order": configuration.get("execution_order"),
        "contacted_shards": contacted_shards(configuration),
        "source_shards": [result.get("shard") for result in results],
        "request_latency_ms": outcome["request_latency_ms"],
        "total_ms": response.get("total_ms"),
        "vector_ms": response.get("vector_ms"),
        "enrichment_ms": response.get("enrichment_ms"),
        "shard_stats": response.get("shard_stats"),
        "ranking": [
            {
                "rank": index,
                "movie_id": result.get("movie_id"),
                "score": result.get("score"),
                "source_shard": result.get("shard"),
            }
            for index, result in enumerate(results, start=1)
        ],
        "http_status": outcome["http_status"],
        "failure": outcome["failure"],
    }

    if query_kind == "relational":
        record["relational_results"] = results
        record["correctness"] = query["correctness"]
    if query_kind == "vector_dense":
        record["relevant_movie_ids"] = query["relevant_movie_ids"]
    if query_kind == "vector_sparse":
        record["target_movie_id"] = query["target_movie_id"]

    return record


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        records.append(json.loads(line))
    return records


def run_requests(
    query_document: dict[str, Any],
    representations: dict[str, Any],
    output_file: TextIO,
    url: str,
    warmups: int,
    repetitions: int,
    timeout_seconds: float,
    send_request: Callable[[str, dict[str, Any], float], dict[str, Any]],
) -> int:
    measured_request_count = 0
    for query_kind, query, configuration in configured_queries(query_document):
        request_body = build_request(
            query_kind, query, configuration, representations
        )

        # Warmups exercise the same path without recording timings. This gives
        # connection pools, database pages, and indexes a chance to become warm
        # before the measured repetitions for this configuration begin.
        for _warmup in range(warmups):
            outcome = send_request(url, request_body, timeout_seconds)
            if outcome["http_status"] != 200:
                raise RuntimeError(
                    f"warmup failed for {query['id']} with "
                    f"{configuration}: {outcome['failure']}"
                )

        for repetition in range(1, repetitions + 1):
            outcome = send_request(url, request_body, timeout_seconds)
            record = build_record(
                query_kind,
                query,
                configuration,
                repetition,
                outcome,
            )
            output_file.write(json.dumps(record, separators=(",", ":")) + "\n")
            output_file.flush()
            measured_request_count += 1

    return measured_request_count


def main() -> None:
    arguments = parse_arguments()
    if arguments.warmups < 0:
        raise ValueError("warmups must be at least 0")
    if arguments.repetitions < 1:
        raise ValueError("repetitions must be at least 1")

    query_document = load_json(arguments.queries)
    representation_document = load_json(arguments.representations)
    representations = representation_document["representations"]

    arguments.output_directory.mkdir(parents=True, exist_ok=True)
    result_path = arguments.output_directory / "requests.jsonl"
    with result_path.open("x", encoding="utf-8") as output_file:
        measured_request_count = run_requests(
            query_document,
            representations,
            output_file,
            arguments.url,
            arguments.warmups,
            arguments.repetitions,
            arguments.timeout_seconds,
            request_once,
        )

    records = load_records(result_path)
    summary = BenchmarkMetrics(records).calculate()
    summary_path = arguments.output_directory / "summary.json"
    summary_path.write_text(json.dumps(
        summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"saved {measured_request_count} measured requests to {result_path}")


if __name__ == "__main__":
    main()
