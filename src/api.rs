use crate::{Orchestrator, QueryError, QueryResponse, error_body::ErrorBody, query::QueryRequest};
use axum::{
    Json, Router,
    extract::{State, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};

pub fn router(orchestrator: Orchestrator) -> Router {
    Router::new()
        .route("/health", get(|| async { "OK" }))
        .route("/query", post(query))
        .with_state(orchestrator)
}

async fn query(
    State(orchestrator): State<Orchestrator>,
    body: Result<Json<QueryRequest>, JsonRejection>,
) -> Result<Json<QueryResponse>, Response> {
    
    let request = match body {
        Ok(Json(request)) => request,
        Err(error) => return Err(error_response(error.status(), error.body_text())),
    };

    match orchestrator.query(request).await {
        Ok(response) => Ok(Json(response)),
        Err(error) => {
            let response = match error {
                QueryError::Invalid(message) => {
                    error_response(StatusCode::BAD_REQUEST, message.to_owned())
                }
                QueryError::InvalidSql(shard, message) => error_response(
                    StatusCode::BAD_REQUEST,
                    format!("PostgreSQL shard {shard:?} rejected the SQL: {message}"),
                ),
                QueryError::InvalidResult(shard, message) => error_response(
                    StatusCode::BAD_REQUEST,
                    format!("PostgreSQL shard {shard:?} returned an unreadable result: {message}"),
                ),
                QueryError::PoolUnavailable(shard) => error_response(
                    StatusCode::SERVICE_UNAVAILABLE,
                    format!("PostgreSQL shard {shard:?} connection pool is unavailable"),
                ),
                QueryError::DatabaseUnavailable(shard) => error_response(
                    StatusCode::SERVICE_UNAVAILABLE,
                    format!("PostgreSQL shard {shard:?} connection failed"),
                ),
                QueryError::Timeout(shard) => error_response(
                    StatusCode::GATEWAY_TIMEOUT,
                    format!("PostgreSQL shard {shard:?} timed out"),
                ),
            };
            Err(response)
        }
    }
}

fn error_response(status: StatusCode, message: String) -> Response {
    let body = ErrorBody::new(message);
    (status, Json(body)).into_response()
}
