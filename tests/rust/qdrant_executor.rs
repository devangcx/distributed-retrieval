use distributed_retrieval::{
    ExecutionOrder, Layout, Routing, Shard,
    qdrant::{
        QdrantError, QdrantExecutor, QueryVector, SparseVector, VectorFilter, VectorSearchRequest,
    },
};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

async fn fake_qdrant(
    response_body: &'static str,
    vector_name: &'static str,
    expected_request_parts: &'static [&'static str],
) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();

    tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = vec![0; 32_768];
        let bytes_read = stream.read(&mut request).await.unwrap();
        let request_text = String::from_utf8_lossy(&request[..bytes_read]);
        assert!(request_text.contains("POST /collections/movies_hash_v1/points/query"));
        assert!(request_text.contains(&format!("\"using\":\"{vector_name}\"")));
        for expected_part in expected_request_parts {
            assert!(request_text.contains(expected_part));
        }

        let response = format!(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
            response_body.len(),
            response_body
        );
        stream.write_all(response.as_bytes()).await.unwrap();
    });

    format!("http://{address}")
}

fn request(routing: Routing, shard: Option<Shard>) -> VectorSearchRequest {
    VectorSearchRequest {
        layout: Layout::Hash,
        routing,
        shard,
        execution_order: ExecutionOrder::FilterThenSearch,
        vector_name: "overview_dense".to_owned(),
        vector: QueryVector::Dense(vec![0.1; 1024]),
        filter: None,
        limit: 2,
    }
}

#[tokio::test]
async fn selective_search_returns_qdrant_results() {
    // Arrange
    let shard_a_url = fake_qdrant(
        r#"{"result":{"points":[{"id":42,"score":0.91},{"id":7,"score":0.72}]},"status":"ok"}"#,
        "overview_dense",
        &[],
    )
    .await;
    let executor = QdrantExecutor::new(
        shard_a_url,
        "http://127.0.0.1:1".to_owned(),
        Duration::from_secs(1),
    )
    .unwrap();

    // Act
    let results = executor
        .search(request(Routing::Selective, Some(Shard::A)))
        .await
        .unwrap();

    // Assert
    assert_eq!(results.len(), 2);
    assert_eq!(results[0].movie_id, 42);
    assert_eq!(results[0].score, 0.91);
    assert_eq!(results[0].shard, Shard::A);
}

#[tokio::test]
async fn broadcast_merges_results_by_score_and_applies_the_limit() {
    // Arrange
    let shard_a_url = fake_qdrant(
        r#"{"result":{"points":[{"id":1,"score":0.90},{"id":2,"score":0.60}]}}"#,
        "overview_dense",
        &[],
    )
    .await;
    let shard_b_url = fake_qdrant(
        r#"{"result":{"points":[{"id":3,"score":0.95},{"id":4,"score":0.70}]}}"#,
        "overview_dense",
        &[],
    )
    .await;
    let executor = QdrantExecutor::new(shard_a_url, shard_b_url, Duration::from_secs(1)).unwrap();

    // Act
    let results = executor
        .search(request(Routing::Broadcast, None))
        .await
        .unwrap();

    // Assert
    assert_eq!(results.len(), 2);
    assert_eq!(results[0].movie_id, 3);
    assert_eq!(results[1].movie_id, 1);
}

#[tokio::test]
async fn invalid_vector_is_rejected_before_qdrant_access() {
    // Arrange
    let executor = QdrantExecutor::new(
        "http://127.0.0.1:1".to_owned(),
        "http://127.0.0.1:1".to_owned(),
        Duration::from_secs(1),
    )
    .unwrap();
    let mut invalid_request = request(Routing::Selective, Some(Shard::A));
    invalid_request.vector = QueryVector::Dense(vec![0.1; 1023]);

    // Act
    let result = executor.search(invalid_request).await;

    // Assert
    assert!(matches!(result, Err(QdrantError::Invalid(_))));
}

#[tokio::test]
async fn sparse_search_uses_the_bm25_vector() {
    // Arrange
    let shard_a_url = fake_qdrant(
        r#"{"result":{"points":[{"id":12,"score":3.4}]}}"#,
        "metadata_sparse",
        &[],
    )
    .await;
    let executor = QdrantExecutor::new(
        shard_a_url,
        "http://127.0.0.1:1".to_owned(),
        Duration::from_secs(1),
    )
    .unwrap();
    let mut sparse_request = request(Routing::Selective, Some(Shard::A));
    sparse_request.vector_name = "metadata_sparse".to_owned();
    sparse_request.vector = QueryVector::Sparse(SparseVector {
        indices: vec![10, 42],
        values: vec![1.2, 0.8],
    });

    // Act
    let results = executor.search(sparse_request).await.unwrap();

    // Assert
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].movie_id, 12);
}

#[tokio::test]
async fn search_sends_supported_payload_filters_to_qdrant() {
    // Arrange
    let expected_request_parts = &[
        r#""key":"release_year","range":{"gte":2000,"lte":2010}"#,
        r#""key":"genre_ids","match":{"any":[28,878]}"#,
        r#""key":"industry","match":{"value":"hollywood"}"#,
        r#""key":"country_codes","match":{"any":["US","CA"]}"#,
    ];
    let shard_a_url = fake_qdrant(
        r#"{"result":{"points":[{"id":42,"score":0.91}]}}"#,
        "overview_dense",
        expected_request_parts,
    )
    .await;
    let executor = QdrantExecutor::new(
        shard_a_url,
        "http://127.0.0.1:1".to_owned(),
        Duration::from_secs(1),
    )
    .unwrap();
    let mut filtered_request = request(Routing::Selective, Some(Shard::A));
    filtered_request.filter = Some(VectorFilter {
        release_year_from: Some(2000),
        release_year_to: Some(2010),
        genre_ids: vec![28, 878],
        industry: Some("hollywood".to_owned()),
        country_codes: vec!["US".to_owned(), "CA".to_owned()],
    });

    // Act
    let results = executor.search(filtered_request).await.unwrap();

    // Assert
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].movie_id, 42);
}

#[tokio::test]
async fn invalid_filter_is_rejected_before_qdrant_access() {
    // Arrange
    let executor = QdrantExecutor::new(
        "http://127.0.0.1:1".to_owned(),
        "http://127.0.0.1:1".to_owned(),
        Duration::from_secs(1),
    )
    .unwrap();
    let mut filtered_request = request(Routing::Selective, Some(Shard::A));
    filtered_request.filter = Some(VectorFilter {
        release_year_from: Some(2020),
        release_year_to: Some(2010),
        genre_ids: Vec::new(),
        industry: None,
        country_codes: Vec::new(),
    });

    // Act
    let result = executor.search(filtered_request).await;

    // Assert
    assert!(matches!(result, Err(QdrantError::Invalid(_))));
}
