use distributed_retrieval::{ApiQueryRequest, Layout, Routing, Shard};
use serde_json::json;

#[test]
fn deserializes_relational_query() {
    // Arrange
    let json = json!({
        "query_type": "relational",
        "layout": "industry",
        "routing": "selective",
        "shard": "a",
        "sql": "SELECT movie_id FROM movies LIMIT 10",
        "limit": 10
    });

    // Act
    let request: ApiQueryRequest = serde_json::from_value(json).unwrap();

    // Assert
    match request {
        ApiQueryRequest::Relational(request) => {
            assert_eq!(request.layout, Layout::Industry);
            assert!(request.routing == Routing::Selective);
            assert_eq!(request.shard, Some(Shard::A));
        }
        ApiQueryRequest::VectorDense(..) | ApiQueryRequest::VectorSparse(..) => {
            panic!("expected a relational request")
        }
    }
}

#[test]
fn deserializes_and_validates_dense_query() {
    // Arrange
    let json = json!({
        "query_type": "vector_dense",
        "layout": "hash",
        "routing": "broadcast",
        "vector_name": "overview_dense",
        "vector": vec![0.1; 1024],
        "filter": {
            "release_year_from": 2000,
            "genre_ids": [28],
            "industry": "hollywood"
        },
        "limit": 10
    });

    // Act
    let request: ApiQueryRequest = serde_json::from_value(json).unwrap();
    let validation = match request {
        ApiQueryRequest::VectorDense(request) => request.into_search_request().validate(),
        ApiQueryRequest::Relational(..) | ApiQueryRequest::VectorSparse(..) => {
            panic!("expected a dense vector request")
        }
    };

    // Assert
    assert!(validation.is_ok());
}

#[test]
fn deserializes_and_validates_sparse_query() {
    // Arrange
    let json = json!({
        "query_type": "vector_sparse",
        "layout": "hash",
        "routing": "selective",
        "shard": "b",
        "indices": [12, 87, 401],
        "values": [1.7, 0.9, 2.1],
        "limit": 10
    });

    // Act
    let request: ApiQueryRequest = serde_json::from_value(json).unwrap();
    let validation = match request {
        ApiQueryRequest::VectorSparse(request) => request.into_search_request().validate(),
        ApiQueryRequest::Relational(..) | ApiQueryRequest::VectorDense(..) => {
            panic!("expected a sparse vector request")
        }
    };

    // Assert
    assert!(validation.is_ok());
}

#[test]
fn rejects_fields_from_a_different_query_type() {
    // Arrange
    let json = json!({
        "query_type": "vector_sparse",
        "layout": "hash",
        "routing": "broadcast",
        "indices": [12],
        "values": [1.7],
        "sql": "SELECT movie_id FROM movies",
        "limit": 10
    });

    // Act
    let result = serde_json::from_value::<ApiQueryRequest>(json);

    // Assert
    assert!(result.is_err());
}
