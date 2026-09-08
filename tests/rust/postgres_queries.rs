//! Read-only checks against the loaded canonical corpus; no database writes.
use distributed_retrieval::{Layout, Orchestrator, QueryError, QueryRequest, Routing, Shard};
use std::time::Duration;

fn orchestrator() -> Orchestrator {
    dotenvy::dotenv().ok();
    Orchestrator::new(
        std::env::var("POSTGRES_SHARD_A_URL").unwrap(),
        std::env::var("POSTGRES_SHARD_B_URL").unwrap(),
        String::from("http://127.0.0.1:6333"),
        String::from("http://127.0.0.1:6335"),
        Duration::from_secs(5),
    )
    .unwrap()
}

#[tokio::test]
#[ignore = "requires both populated PostgreSQL shards and local .env"]
async fn broadcast_combines_results_in_shard_order_for_both_layouts() {
    for layout in [Layout::Hash, Layout::Industry] {
        // Arrange
        let executor = orchestrator();
        let mut expected = Vec::new();
        for shard in [Shard::A, Shard::B] {
            let response = executor
                .query(QueryRequest {
                    layout,
                    routing: Routing::Selective,
                    shard: Some(shard),
                    sql: "SELECT movie_id FROM movies ORDER BY movie_id LIMIT 10".into(),
                    limit: 100,
                })
                .await
                .unwrap();
            expected.extend(response.results);
        }

        // Act
        let response = orchestrator()
            .query(QueryRequest {
                layout,
                routing: Routing::Broadcast,
                shard: None,
                sql: "SELECT movie_id FROM movies ORDER BY movie_id LIMIT 10".into(),
                limit: 100,
            })
            .await
            .unwrap();

        // Assert
        assert_eq!(response.results, expected);
        assert_eq!(response.shard_stats.len(), 2);
    }
}

#[tokio::test]
#[ignore = "requires a populated PostgreSQL shard and local .env"]
async fn transaction_rejects_mutating_sql() {
    // Arrange
    let orchestrator = orchestrator();
    orchestrator
        .query(QueryRequest {
            layout: Layout::Hash,
            routing: Routing::Selective,
            shard: Some(Shard::A),
            sql: "SELECT movie_id FROM movies LIMIT 1".into(),
            limit: 1,
        })
        .await
        .expect("the database must be available before testing read-only enforcement");

    // Act
    let result = orchestrator
        .query(QueryRequest {
            layout: Layout::Hash,
            routing: Routing::Selective,
            shard: Some(Shard::A),
            sql: "DELETE FROM movies RETURNING movie_id".into(),
            limit: 10,
        })
        .await;

    // Assert
    assert!(matches!(result, Err(QueryError::InvalidSql(_, _))));
}
