use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use distributed_retrieval::{Orchestrator, api};
use serde_json::{Value, json};
use std::time::Duration;
use tower::ServiceExt;

fn app() -> axum::Router {
    // Invalid connection strings make any unexpected database access fail.
    api::router(
        Orchestrator::new("invalid".into(), "invalid".into(), Duration::from_secs(1)).unwrap(),
    )
}

async fn post(body: Value) -> (StatusCode, Value) {
    let response = app()
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
    let app = api::router(Orchestrator::new(url.clone(), url, Duration::from_millis(30)).unwrap());
    let request = Request::post("/query")
        .header("content-type", "application/json")
        .body(Body::from(json!({"layout":"industry", "routing":"selective", "shard":"a", "sql":"SELECT movie_id FROM movies", "limit":10}).to_string()))
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
