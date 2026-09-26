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

// Keep each assertion about a known target's eligibility: the two execution
// orders have different candidate budgets and need not return identical lists.
async fn run_target_filter_cases(cases: Vec<(&str, Value, bool)>) -> Vec<(String, bool, bool)> {
    let target_id = 27205; // Inception: 2010, Action/SF/Adventure, GB/US, Hollywood.
    let vector = overview_vector_for_movie(target_id);
    let router = app();
    let mut outcomes = Vec::new();
    for layout in ["hash", "industry"] {
        for order in ["filter_then_search", "search_then_filter"] {
            // A positive control prevents an absent target from making all
            // negative filter cases pass vacuously.
            let (status, body) = post(
                router.clone(),
                json!({
                    "query_type": "vector_dense", "execution_order": order,
                    "layout": layout, "routing": "broadcast",
                    "vector_name": "overview_dense", "vector": vector, "limit": 100
                }),
            )
            .await;
            assert_eq!(status, StatusCode::OK, "{layout}/{order}: {body}");
            let control_found = contains_movie(&body, target_id);
            outcomes.push((
                format!("positive control/{layout}/{order}"),
                control_found,
                true,
            ));
            for (name, filter, expected) in &cases {
                let (status, body) = post(
                    router.clone(),
                    json!({
                        "query_type": "vector_dense", "execution_order": order,
                        "layout": layout, "routing": "broadcast",
                        "vector_name": "overview_dense", "vector": vector,
                        "filter": filter, "limit": 100
                    }),
                )
                .await;
                assert_eq!(status, StatusCode::OK, "{name}/{layout}/{order}: {body}");
                let found = contains_movie(&body, target_id);
                outcomes.push((format!("{name}/{layout}/{order}"), found, *expected));
            }
        }
    }
    outcomes
}

fn contains_movie(response: &Value, movie_id: u64) -> bool {
    let results = response["results"]
        .as_array()
        .expect("response must contain results");
    for result in results {
        if result["movie_id"] == movie_id {
            return true;
        }
    }
    false
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL and Qdrant shards and local .env"]
async fn both_orders_respect_inclusive_year_boundaries_in_both_layouts() {
    // Arrange
    let cases = vec![
        (
            "equal bounds",
            json!({"release_year_from":2010,"release_year_to":2010}),
            true,
        ),
        (
            "lower endpoint",
            json!({"release_year_from":2010,"release_year_to":2011}),
            true,
        ),
        (
            "upper endpoint",
            json!({"release_year_from":2009,"release_year_to":2010}),
            true,
        ),
        ("below range", json!({"release_year_from":2011}), false),
        ("above range", json!({"release_year_to":2009}), false),
        ("open upper bound", json!({"release_year_from":2010}), true),
        ("open lower bound", json!({"release_year_to":2010}), true),
    ];

    // Act
    let outcomes = run_target_filter_cases(cases).await;

    // Assert
    for (case_name, actual, expected) in outcomes {
        assert_eq!(actual, expected, "{case_name}");
    }
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL and Qdrant shards and local .env"]
async fn both_orders_use_or_within_fields_and_and_across_fields() {
    // Arrange
    let cases = vec![
        ("genre alternative", json!({"genre_ids":[-1,28]}), true),
        (
            "country alternative",
            json!({"country_codes":["ZZ","US"]}),
            true,
        ),
        (
            "all fields match",
            json!({"release_year_from":2010,"release_year_to":2010,
            "genre_ids":[-1,878],"country_codes":["ZZ","GB"],"industry":"hollywood"}),
            true,
        ),
        (
            "year alone fails",
            json!({"release_year_from":2011,
            "genre_ids":[28],"country_codes":["US"],"industry":"hollywood"}),
            false,
        ),
        (
            "genre alone fails",
            json!({"release_year_from":2010,
            "genre_ids":[-1],"country_codes":["US"],"industry":"hollywood"}),
            false,
        ),
        (
            "country alone fails",
            json!({"release_year_from":2010,
            "genre_ids":[28],"country_codes":["ZZ"],"industry":"hollywood"}),
            false,
        ),
        (
            "industry alone fails",
            json!({"release_year_from":2010,
            "genre_ids":[28],"country_codes":["US"],"industry":"bollywood"}),
            false,
        ),
    ];

    // Act
    let outcomes = run_target_filter_cases(cases).await;

    // Assert
    for (case_name, actual, expected) in outcomes {
        assert_eq!(actual, expected, "{case_name}");
    }
}

#[tokio::test]
#[ignore = "requires populated PostgreSQL shards and local .env"]
async fn missing_relational_record_rejects_enrichment_without_partial_results() {
    // Arrange
    let real_app = app();
    for layout in ["hash", "industry"] {
        // Establish both a real record and an absent ID without changing data.
        let (status, body) = post(real_app.clone(), json!({
            "query_type":"relational", "layout":layout, "routing":"selective",
            "shard":"a", "sql":"SELECT min(movie_id) AS present_id, max(movie_id) + 1 AS absent_id FROM movies",
            "limit":1
        })).await;
        assert_eq!(status, StatusCode::OK, "{body}");
        let present_id = body["results"][0]["present_id"].as_u64().unwrap();
        let absent_id = body["results"][0]["absent_id"].as_u64().unwrap();
        for order in ["filter_then_search", "search_then_filter"] {
            let points = json!({"result":{"points":[
                {"id":present_id,"score":0.99}, {"id":absent_id,"score":0.90}
            ]}});
            // Axum requires an asynchronous handler. Each request owns a copy
            // of the fixed response so the handler can serve repeated calls.
            let fake_qdrant = Router::new().fallback(move || {
                let body = points.clone();
                async move { axum::Json(body) }
            });
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let url = format!("http://{}", listener.local_addr().unwrap());
            // Run the fake service concurrently while the application calls it.
            let server =
                tokio::spawn(async move { axum::serve(listener, fake_qdrant).await.unwrap() });
            let router = api::router(
                Orchestrator::new(
                    required_variable("POSTGRES_SHARD_A_URL"),
                    required_variable("POSTGRES_SHARD_B_URL"),
                    url.clone(),
                    url,
                    Duration::from_secs(5),
                )
                .unwrap(),
            );
            // Act
            let (status, body) = post(
                router,
                json!({
                    "query_type":"vector_dense", "execution_order":order,
                    "layout":layout, "routing":"selective", "shard":"a",
                    "vector_name":"overview_dense", "vector":vec![0.1;1024], "limit":2
                }),
            )
            .await;
            server.abort();
            // Assert
            assert_eq!(status, StatusCode::BAD_REQUEST, "{layout}/{order}: {body}");
            let error = body["error"].as_str().unwrap();
            assert!(error.contains("PostgreSQL shard A"), "{error}");
            assert!(
                error.contains(&format!("PostgreSQL did not return movie {absent_id}")),
                "{error}"
            );
            assert!(
                body.get("results").is_none(),
                "must not silently return the valid hit"
            );
        }
    }
}
