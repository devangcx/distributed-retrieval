use distributed_retrieval::query::{Layout, QueryRequest, Routing, Shard};

#[test]
fn broadcast_queries_both_shards() {
    // Arrange
    let request = QueryRequest {
        layout: Layout::Hash,
        routing: Routing::Broadcast,
        shard: None,
        sql: "SELECT movie_id FROM movies".into(),
        limit: 10,
    };

    // Act
    let shards = request.shards();

    // Assert
    assert_eq!(shards, vec![Shard::A, Shard::B]);
}

#[test]
fn selective_queries_the_requested_shard() {
    // Arrange
    let request = QueryRequest {
        layout: Layout::Industry,
        routing: Routing::Selective,
        shard: Some(Shard::B),
        sql: "SELECT movie_id FROM movies".into(),
        limit: 10,
    };

    // Act
    let shards = request.shards();

    // Assert
    assert_eq!(shards, vec![Shard::B]);
}

#[test]
fn rejects_invalid_execution_contracts() {
    // Arrange
    let request = |routing, shard, sql: &str, limit| QueryRequest {
        layout: Layout::Hash,
        routing,
        shard,
        sql: sql.into(),
        limit,
    };
    let zero_limit = request(Routing::Broadcast, None, "SELECT movie_id FROM movies", 0);
    let excessive_limit = request(Routing::Broadcast, None, "SELECT movie_id FROM movies", 101);
    let blank_sql = request(Routing::Broadcast, None, "   ", 10);
    let broadcast_with_shard = request(
        Routing::Broadcast,
        Some(Shard::A),
        "SELECT movie_id FROM movies",
        10,
    );
    let valid_selective = request(
        Routing::Selective,
        Some(Shard::A),
        "SELECT movie_id FROM movies",
        10,
    );
    let selective_without_shard =
        request(Routing::Selective, None, "SELECT movie_id FROM movies", 10);

    // Act
    let zero_limit_result = zero_limit.validate();
    let excessive_limit_result = excessive_limit.validate();
    let blank_sql_result = blank_sql.validate();
    let broadcast_with_shard_result = broadcast_with_shard.validate();
    let valid_selective_result = valid_selective.validate();
    let selective_without_shard_result = selective_without_shard.validate();

    // Assert
    assert!(zero_limit_result.is_err());
    assert!(excessive_limit_result.is_err());
    assert!(blank_sql_result.is_err());
    assert!(broadcast_with_shard_result.is_err());
    assert!(valid_selective_result.is_ok());
    assert!(selective_without_shard_result.is_err());
}
