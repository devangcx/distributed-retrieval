use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use distributed_retrieval::{Orchestrator, api};
use serde_json::{Value, json};
use std::{fs, path::PathBuf, time::Duration};
use tower::ServiceExt;

fn required_variable(name: &str) -> String {
    match std::env::var(name) {
        Ok(value) => value,
        Err(error) => panic!("{name} is required for end-to-end tests: {error}"),
    }
}

fn app() -> Router {
    dotenvy::dotenv().ok();
    let orchestrator = Orchestrator::new(
        required_variable("POSTGRES_SHARD_A_URL"),
        required_variable("POSTGRES_SHARD_B_URL"),
        required_variable("QDRANT_SHARD_A_URL"),
        required_variable("QDRANT_SHARD_B_URL"),
        Duration::from_secs(5),
    )
    .unwrap();
    api::router(orchestrator)
}

async fn post(app: Router, request_body: Value) -> (StatusCode, Value) {
    let response = app
        .oneshot(
            Request::post("/query")
                .header("content-type", "application/json")
                .body(Body::from(request_body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 1_048_576).await.unwrap();
    let body = serde_json::from_slice(&bytes).unwrap();
    (status, body)
}

fn first_overview_vector() -> Vec<f32> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("data/processed/embeddings/movie-retrieval-v1/overview.f32");
    let bytes = fs::read(path).unwrap();
    let vector_bytes = &bytes[..1024 * 4];
    let mut vector = Vec::with_capacity(1024);

    for chunk in vector_bytes.chunks_exact(4) {
        vector.push(f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]));
    }

    vector
}

fn overview_vector_for_movie(movie_id: u64) -> Vec<f32> {
    let corpus_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("data/processed/movies.json");
    let corpus_bytes = fs::read(corpus_path).unwrap();
    let movies: Value = serde_json::from_slice(&corpus_bytes).unwrap();
    let movie_index = movies
        .as_array()
        .unwrap()
        .iter()
        .position(|movie| movie["movie_id"] == movie_id)
        .unwrap();

    let vector_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("data/processed/embeddings/movie-retrieval-v1/overview.f32");
    let vector_bytes = fs::read(vector_path).unwrap();
    let start = movie_index * 1024 * 4;
    let end = start + 1024 * 4;
    let mut vector = Vec::with_capacity(1024);

    for chunk in vector_bytes[start..end].chunks_exact(4) {
        vector.push(f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]));
    }

    vector
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL and Qdrant shards and local .env"]
async fn relational_query_runs_through_http_api() {
    // Arrange
    let request_body = json!({
        "query_type": "relational",
        "layout": "hash",
        "routing": "broadcast",
        "sql": "SELECT movie_id, title FROM movies ORDER BY movie_id LIMIT 2",
        "limit": 2
    });

    // Act
    let (status, body) = post(app(), request_body).await;

    // Assert
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["results"].as_array().unwrap().len(), 2);
    assert!(body["results"][0]["title"].is_string());
    assert!(body["total_ms"].is_number());
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL and Qdrant shards and local .env"]
async fn dense_query_runs_through_http_api_and_enrichment() {
    // Arrange
    let request_body = json!({
        "query_type": "vector_dense",
        "execution_order": "filter_then_search",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": first_overview_vector(),
        "limit": 2
    });

    // Act
    let (status, body) = post(app(), request_body).await;

    // Assert
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["results"].as_array().unwrap().len(), 2);
    assert!(body["results"][0]["score"].is_number());
    assert!(body["results"][0]["details"]["title"].is_string());
    assert!(body["vector_ms"].is_number());
    assert!(body["enrichment_ms"].is_number());
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL and Qdrant shards and local .env"]
async fn sparse_query_runs_through_http_api() {
    // Arrange
    let request_body = json!({
        "query_type": "vector_sparse",
        "execution_order": "filter_then_search",
        "layout": "hash",
        "routing": "broadcast",
        "indices": [12, 87, 401],
        "values": [1.7, 0.9, 2.1],
        "limit": 2
    });

    // Act
    let (status, body) = post(app(), request_body).await;

    // Assert
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(body["results"].is_array());
    assert!(body["vector_ms"].is_number());
    assert!(body["enrichment_ms"].is_number());
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL and Qdrant shards and local .env"]
async fn search_then_filter_applies_filter_after_retrieval() {
    // Arrange
    let request_body = json!({
        "query_type": "vector_dense",
        "execution_order": "search_then_filter",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": first_overview_vector(),
        "filter": { "industry": "does_not_exist" },
        "limit": 2
    });

    // Act
    let (status, body) = post(app(), request_body).await;

    // Assert
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["execution_order"], "search_then_filter");
    assert!(body["results"].as_array().unwrap().is_empty());
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL and Qdrant shards and local .env"]
async fn filter_then_search_applies_filter_during_retrieval() {
    // Arrange
    let request_body = json!({
        "query_type": "vector_dense",
        "execution_order": "filter_then_search",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": first_overview_vector(),
        "filter": { "industry": "hollywood" },
        "limit": 2
    });

    // Act
    let (status, body) = post(app(), request_body).await;

    // Assert
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["execution_order"], "filter_then_search");
    assert_eq!(body["results"].as_array().unwrap().len(), 2);
    for result in body["results"].as_array().unwrap() {
        assert_eq!(result["details"]["industry"], "hollywood");
    }
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL and Qdrant shards and local .env"]
async fn both_orders_use_canonical_other_industry_label() {
    // Arrange
    let resident_evil_id = 1576;
    let vector = overview_vector_for_movie(resident_evil_id);
    let filter_then_search_request = json!({
        "query_type": "vector_dense",
        "execution_order": "filter_then_search",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": vector,
        "filter": { "industry": "other_or_ambiguous" },
        "limit": 1
    });
    let search_then_filter_request = json!({
        "query_type": "vector_dense",
        "execution_order": "search_then_filter",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": overview_vector_for_movie(resident_evil_id),
        "filter": { "industry": "other_or_ambiguous" },
        "limit": 1
    });

    // Act
    let (filter_first_status, filter_first_body) = post(app(), filter_then_search_request).await;
    let (search_first_status, search_first_body) = post(app(), search_then_filter_request).await;

    // Assert
    assert_eq!(filter_first_status, StatusCode::OK, "{filter_first_body}");
    assert_eq!(search_first_status, StatusCode::OK, "{search_first_body}");
    for body in [filter_first_body, search_first_body] {
        assert_eq!(body["results"][0]["movie_id"], resident_evil_id);
        assert_eq!(
            body["results"][0]["details"]["industry"],
            "other_or_ambiguous"
        );
    }
}
