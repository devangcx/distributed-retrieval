use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use distributed_retrieval::{Layout, Orchestrator, QueryRequest, Routing, Shard, api};
use serde_json::{Value, json};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tower::ServiceExt;

fn app() -> axum::Router {
    // Invalid connection strings make any unexpected database access fail.
    api::router(
        Orchestrator::new(
            String::from("invalid"),
            String::from("invalid"),
            String::from("http://127.0.0.1:1"),
            String::from("http://127.0.0.1:1"),
            Duration::from_secs(1),
        )
        .unwrap(),
    )
}

async fn post(body: Value) -> (StatusCode, Value) {
    post_to(app(), body).await
}

async fn post_to(app: axum::Router, body: Value) -> (StatusCode, Value) {
    let response = app
        .oneshot(
            Request::post("/query")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 8192).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn health_does_not_require_databases() {
    // Arrange
    let request = Request::get("/health").body(Body::empty()).unwrap();

    // Act
    let response = app().oneshot(request).await.unwrap();

    // Assert
    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn validates_before_database_access() {
    // Arrange
    let request_body = json!({
        "query_type":"relational",
        "layout":"hash",
        "routing":"broadcast",
        "shard": null,
        "sql":"SELECT movie_id FROM movies",
        "limit":0
    });

    // Act
    let (status, body) = post(request_body).await;

    // Assert
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body["error"].as_str().unwrap().contains("limit"));
}

#[tokio::test]
async fn rejects_unknown_fields_instead_of_silently_ignoring_them() {
    // Arrange
    let request_body = json!({
        "query_type":"relational",
        "layout":"hash",
        "routing":"broadcast",
        "sql":"SELECT movie_id FROM movies",
        "limit":10,
        "filters":{}
    });

    // Act
    let (status, _) = post(request_body).await;

    // Assert
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
}

#[tokio::test]
async fn failed_shard_does_not_return_partial_success() {
    // Arrange
    let request_body = json!({
        "query_type":"relational",
        "layout":"hash",
        "routing":"broadcast",
        "sql":"SELECT movie_id FROM movies",
        "limit":10
    });

    // Act
    let (status, body) = post(request_body).await;

    // Assert
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert!(body.get("results").is_none());
    assert!(!body.to_string().contains("invalid"));
}

#[tokio::test]
async fn stalled_database_returns_gateway_timeout() {
    // Arrange
    // A listening socket that never speaks PostgreSQL stalls connection setup.
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!(
        "postgres://test:test@{}/test",
        listener.local_addr().unwrap()
    );
    let app = api::router(
        Orchestrator::new(
            url.clone(),
            url,
            String::from("http://127.0.0.1:1"),
            String::from("http://127.0.0.1:1"),
            Duration::from_millis(30),
        )
        .unwrap(),
    );
    let request = Request::post("/query")
        .header("content-type", "application/json")
        .body(Body::from(json!({"query_type":"relational", "layout":"industry", "routing":"selective", "shard":"a", "sql":"SELECT movie_id FROM movies", "limit":10}).to_string()))
        .unwrap();

    // Act
    let response = app.oneshot(request).await.unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 8192).await.unwrap();
    let body: Value = serde_json::from_slice(&bytes).unwrap();

    // Assert
    assert_eq!(status, StatusCode::GATEWAY_TIMEOUT);
    assert!(
        body["error"]
            .as_str()
            .unwrap()
            .contains("shard A timed out")
    );
    assert!(body.get("results").is_none());
}

#[tokio::test]
async fn maps_unavailable_qdrant_to_service_unavailable() {
    // Arrange
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let unavailable_url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = vec![0; 32_768];
        let _bytes_read = stream.read(&mut request).await.unwrap();
        let response =
            "HTTP/1.1 503 Service Unavailable\r\ncontent-length: 0\r\nconnection: close\r\n\r\n";
        stream.write_all(response.as_bytes()).await.unwrap();
    });
    let app = api::router(
        Orchestrator::new(
            String::from("invalid"),
            String::from("invalid"),
            unavailable_url.clone(),
            unavailable_url,
            Duration::from_secs(1),
        )
        .unwrap(),
    );
    let request_body = json!({
        "query_type": "vector_dense",
        "execution_order": "filter_then_search",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": vec![0.1; 1024],
        "limit": 10
    });

    // Act
    let (status, body) = post_to(app, request_body).await;

    // Assert
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert!(body["error"].as_str().unwrap().contains("Qdrant shard A"));
}

#[tokio::test]
async fn maps_postgres_enrichment_failure_after_vector_search() {
    // Arrange
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let qdrant_url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = vec![0; 32_768];
        let _bytes_read = stream.read(&mut request).await.unwrap();
        let body = r#"{"result":{"points":[{"id":42,"score":0.91}]}}"#;
        let response = format!(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        stream.write_all(response.as_bytes()).await.unwrap();
    });
    let app = api::router(
        Orchestrator::new(
            String::from("invalid"),
            String::from("invalid"),
            qdrant_url,
            String::from("http://127.0.0.1:1"),
            Duration::from_secs(1),
        )
        .unwrap(),
    );
    let request_body = json!({
        "query_type": "vector_dense",
        "execution_order": "filter_then_search",
        "layout": "hash",
        "routing": "selective",
        "shard": "a",
        "vector_name": "overview_dense",
        "vector": vec![0.1; 1024],
        "limit": 10
    });

    // Act
    let (status, body) = post_to(app, request_body).await;

    // Assert
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert!(
        body["error"]
            .as_str()
            .unwrap()
            .contains("PostgreSQL shard A")
    );
}

#[tokio::test]
async fn validates_sparse_contract_before_qdrant_access() {
    // Arrange
    let request_body = json!({
        "query_type": "vector_sparse",
        "execution_order": "filter_then_search",
        "layout": "hash",
        "routing": "broadcast",
        "indices": [12, 87],
        "values": [1.7],
        "limit": 10
    });

    // Act
    let (status, body) = post(request_body).await;

    // Assert
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body["error"].as_str().unwrap().contains("equal lengths"));
}

#[tokio::test]
async fn search_then_filter_validates_original_limit_before_qdrant_access() {
    // Arrange
    let request_body = json!({
        "query_type": "vector_dense",
        "execution_order": "search_then_filter",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": vec![0.1; 1024],
        "limit": 0
    });

    // Act
    let (status, body) = post(request_body).await;

    // Assert
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body["error"].as_str().unwrap().contains("limit"));
}

#[tokio::test]
async fn search_then_filter_validates_original_filter_before_qdrant_access() {
    // Arrange
    let request_body = json!({
        "query_type": "vector_dense",
        "execution_order": "search_then_filter",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": vec![0.1; 1024],
        "filter": {},
        "limit": 10
    });

    // Act
    let (status, body) = post(request_body).await;

    // Assert
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body["error"].as_str().unwrap().contains("condition"));
}

#[tokio::test]
#[ignore = "requires a populated PostgreSQL shard and local .env"]
async fn enriches_vector_results_and_preserves_rank() {
    // Arrange
    dotenvy::dotenv().ok();
    let postgres_shard_a_url = std::env::var("POSTGRES_SHARD_A_URL").unwrap();
    let postgres_shard_b_url = std::env::var("POSTGRES_SHARD_B_URL").unwrap();
    let preliminary_orchestrator = Orchestrator::new(
        postgres_shard_a_url.clone(),
        postgres_shard_b_url.clone(),
        String::from("http://127.0.0.1:1"),
        String::from("http://127.0.0.1:1"),
        Duration::from_secs(5),
    )
    .unwrap();
    let relational_response = preliminary_orchestrator
        .query(QueryRequest {
            layout: Layout::Hash,
            routing: Routing::Selective,
            shard: Some(Shard::A),
            sql: String::from("SELECT movie_id FROM movies ORDER BY movie_id LIMIT 2"),
            limit: 2,
        })
        .await
        .unwrap();
    let first_movie_id = relational_response.results[0]["movie_id"].as_u64().unwrap();
    let second_movie_id = relational_response.results[1]["movie_id"].as_u64().unwrap();

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let qdrant_url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = vec![0; 32_768];
        let _bytes_read = stream.read(&mut request).await.unwrap();
        let body = format!(
            r#"{{"result":{{"points":[{{"id":{second_movie_id},"score":0.95}},{{"id":{first_movie_id},"score":0.80}}]}}}}"#
        );
        let response = format!(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        stream.write_all(response.as_bytes()).await.unwrap();
    });
    let app = api::router(
        Orchestrator::new(
            postgres_shard_a_url,
            postgres_shard_b_url,
            qdrant_url,
            String::from("http://127.0.0.1:1"),
            Duration::from_secs(5),
        )
        .unwrap(),
    );
    let request_body = json!({
        "query_type": "vector_dense",
        "execution_order": "filter_then_search",
        "layout": "hash",
        "routing": "selective",
        "shard": "a",
        "vector_name": "overview_dense",
        "vector": vec![0.1; 1024],
        "limit": 2
    });

    // Act
    let (status, body) = post_to(app, request_body).await;

    // Assert
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["results"][0]["movie_id"], second_movie_id);
    assert_eq!(body["results"][1]["movie_id"], first_movie_id);
    assert_eq!(body["results"][0]["score"], 0.95);
    assert!(body["results"][0]["details"]["title"].is_string());
    assert!(body["results"][0]["details"]["genres"].is_array());
    assert!(body["results"][0]["details"]["directors"].is_array());
    assert!(body["vector_ms"].as_f64().unwrap() >= 0.0);
    assert!(body["enrichment_ms"].as_f64().unwrap() >= 0.0);
    assert!(body["total_ms"].as_f64().unwrap() >= 0.0);
}
