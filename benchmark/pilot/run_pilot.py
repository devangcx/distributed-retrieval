"""Run the small API/evaluation pilot against an already running orchestrator."""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PILOT_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_QUERY_PATH = PILOT_DIRECTORY / "queries.json"
DEFAULT_REPRESENTATION_PATH = PILOT_DIRECTORY / "representations.json"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:3000/query")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERY_PATH)
    parser.add_argument(
        "--representations", type=Path, default=DEFAULT_REPRESENTATION_PATH
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser.parse_args()


def contacted_shards(configuration: dict[str, Any]) -> list[str]:
    if configuration["routing"] == "broadcast":
        return ["a", "b"]
    return [configuration["shard"]]


def build_request(
    query: dict[str, Any],
    configuration: dict[str, Any],
    representations: dict[str, Any],
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "query_type": query["kind"],
        "layout": configuration["layout"],
        "routing": configuration["routing"],
        "limit": query["limit"],
    }
    if "shard" in configuration:
        request["shard"] = configuration["shard"]

    if query["kind"] == "relational":
        request["sql"] = query["sql"]
        return request

    request["execution_order"] = configuration["execution_order"]
    if "filter" in query:
        request["filter"] = query["filter"]
    representation = representations[query["id"]]
    if query["kind"] == "vector_dense":
        request["vector_name"] = query["vector_name"]
        request["vector"] = representation["vector"]
    else:
        request["indices"] = representation["indices"]
        request["values"] = representation["values"]
    return request


def relational_correctness(
    correctness: dict[str, Any], results: list[dict[str, Any]]
) -> bool:
    if correctness["type"] == "sum_field":
        actual = sum(int(result[correctness["field"]]) for result in results)
        return actual == correctness["expected"]
    if correctness["type"] == "movie_ids":
        actual = sorted(int(result["movie_id"]) for result in results)
        return actual == sorted(correctness["expected"])
    raise ValueError(
        f"unknown relational correctness check {correctness['type']}")


def target_rank(results: list[dict[str, Any]], target_movie_id: int) -> int | None:
    for index, result in enumerate(results):
        if int(result["movie_id"]) == target_movie_id:
            return index + 1
    return None


def request_once(url: str, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    encoded_body = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = json.loads(response.read().decode("utf-8"))
            status = response.status
            failure = None
    except urllib.error.HTTPError as error:
        status = error.code
        failure_body = error.read().decode("utf-8")
        try:
            response_body = json.loads(failure_body)
        except json.JSONDecodeError:
            response_body = None
        failure = failure_body
    except (urllib.error.URLError, TimeoutError) as error:
        status = None
        response_body = None
        failure = str(error)
    request_latency_ms = (time.perf_counter() - started) * 1000.0
    return {
        "request_latency_ms": request_latency_ms,
        "http_status": status,
        "response": response_body,
        "failure": failure,
    }


def result_record(
    query: dict[str, Any],
    configuration: dict[str, Any],
    repetition: int,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    response = outcome["response"] if isinstance(
        outcome["response"], dict) else {}
    results = response.get("results", [])
    record: dict[str, Any] = {
        "query_id": query["id"],
        "query_kind": query["kind"],
        "repetition": repetition,
        "layout": configuration["layout"],
        "routing": configuration["routing"],
        "execution_order": configuration.get("execution_order"),
        "contacted_shards": contacted_shards(configuration),
        "source_shards": [result.get("shard") for result in results],
        "request_latency_ms": outcome["request_latency_ms"],
        "http_status": outcome["http_status"],
        "failure": outcome["failure"],
        "total_ms": response.get("total_ms"),
        "vector_ms": response.get("vector_ms"),
        "enrichment_ms": response.get("enrichment_ms"),
        "shard_stats": response.get("shard_stats"),
        "ranking": [
            {
                "rank": index + 1,
                "movie_id": result.get("movie_id"),
                "score": result.get("score"),
                "source_shard": result.get("shard"),
            }
            for index, result in enumerate(results)
        ],
    }
    if query["kind"] == "relational" and outcome["http_status"] == 200:
        record["correct"] = relational_correctness(
            query["correctness"], results)
    if "target_movie_id" in query and outcome["http_status"] == 200:
        rank = target_rank(results, query["target_movie_id"])
        record["target_movie_id"] = query["target_movie_id"]
        record["target_rank"] = rank
        record["hit_at_k"] = rank is not None and rank <= query["limit"]
        record["reciprocal_rank"] = 0.0 if rank is None else 1.0 / rank
    if "relevant_movie_ids" in query and outcome["http_status"] == 200:
        returned_ids = {int(result["movie_id"]) for result in results}
        relevant_ids = {int(movie_id)
                        for movie_id in query["relevant_movie_ids"]}
        record["relevant_movie_ids"] = sorted(relevant_ids)
        record["hit_at_k"] = bool(returned_ids & relevant_ids)
    return record


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    relational_records = [
        record for record in records if record["query_kind"] == "relational"
    ]
    known_item_records = [
        record for record in records if "target_movie_id" in record]
    dense_records = [
        record for record in records if "relevant_movie_ids" in record]
    successful_known_items = [
        record for record in known_item_records if record["http_status"] == 200
    ]
    hit_rate = None
    mean_reciprocal_rank = None
    if successful_known_items:
        hit_rate = sum(bool(record["hit_at_k"]) for record in successful_known_items) / len(
            successful_known_items
        )
        mean_reciprocal_rank = sum(
            float(record["reciprocal_rank"]) for record in successful_known_items
        ) / len(successful_known_items)
    return {
        "pilot_only_not_final_benchmark": True,
        "request_count": len(records),
        "failure_count": sum(record["failure"] is not None for record in records),
        "relational_checks_passed": sum(
            record.get("correct") is True for record in relational_records
        ),
        "relational_checks_total": len(relational_records),
        "known_item_successful_runs": len(successful_known_items),
        "known_item_failed_runs": len(known_item_records) - len(successful_known_items),
        "hit_rate_at_k": hit_rate,
        "mean_reciprocal_rank": mean_reciprocal_rank,
        "dense_hit_rate_at_k": (
            sum(bool(record["hit_at_k"])
                for record in dense_records) / len(dense_records)
            if dense_records
            else None
        ),
    }


def main() -> None:
    arguments = parse_arguments()
    if arguments.repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    query_document = json.loads(arguments.queries.read_text(encoding="utf-8"))
    representation_document = json.loads(
        arguments.representations.read_text(encoding="utf-8")
    )
    if query_document["schema_version"] != representation_document["schema_version"]:
        raise ValueError(
            "query and representation schema versions do not match")
    representations = representation_document["representations"]

    arguments.output_directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for query in query_document["queries"]:
        for configuration in query["configurations"]:
            request_body = build_request(query, configuration, representations)
            for repetition in range(1, arguments.repetitions + 1):
                outcome = request_once(
                    arguments.url, request_body, arguments.timeout_seconds
                )
                records.append(
                    result_record(query, configuration, repetition, outcome)
                )

    result_path = arguments.output_directory / "requests.jsonl"
    result_lines = [json.dumps(record, separators=(",", ":"))
                    for record in records]
    result_path.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
    summary = build_summary(records)
    summary_path = arguments.output_directory / "summary.json"
    summary_path.write_text(json.dumps(
        summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
