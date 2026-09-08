use crate::{
    ApiQueryRequest, Orchestrator, QueryError, error_body::ErrorBody, qdrant::QdrantError,
};
use axum::{
    Json, Router,
    extract::{State, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};

pub fn router(orchestrator: Orchestrator) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/query", post(query))
        .with_state(orchestrator)
}

async fn health() -> &'static str {
    "OK"
}

async fn query(
    State(orchestrator): State<Orchestrator>,
    body: Result<Json<ApiQueryRequest>, JsonRejection>,
) -> Response {
    let request = match body {
        Ok(Json(request)) => request,
        Err(error) => return error_response(error.status(), error.body_text()),
    };

    match request {
        ApiQueryRequest::Relational(request) => match orchestrator.query(request).await {
            Ok(response) => Json(response).into_response(),
            Err(error) => relational_error_response(error),
        },
        ApiQueryRequest::VectorDense(request) => {
            let search_request = request.into_search_request();
            match orchestrator.vector_query(search_request).await {
                Ok(response) => Json(response).into_response(),
                Err(error) => qdrant_error_response(error),
            }
        }
        ApiQueryRequest::VectorSparse(request) => {
            let search_request = request.into_search_request();
            match orchestrator.vector_query(search_request).await {
                Ok(response) => Json(response).into_response(),
                Err(error) => qdrant_error_response(error),
            }
        }
    }
}

fn relational_error_response(error: QueryError) -> Response {
    match error {
        QueryError::Invalid(message) => {
            error_response(StatusCode::BAD_REQUEST, String::from(message))
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
    }
}

fn qdrant_error_response(error: QdrantError) -> Response {
    match error {
        QdrantError::Invalid(message) => {
            error_response(StatusCode::BAD_REQUEST, String::from(message))
        }
        QdrantError::Unavailable(shard) => error_response(
            StatusCode::SERVICE_UNAVAILABLE,
            format!("Qdrant shard {shard:?} is unavailable"),
        ),
        QdrantError::InvalidResponse(shard) => error_response(
            StatusCode::BAD_GATEWAY,
            format!("Qdrant shard {shard:?} returned an unreadable response"),
        ),
        QdrantError::Timeout(shard) => error_response(
            StatusCode::GATEWAY_TIMEOUT,
            format!("Qdrant shard {shard:?} timed out"),
        ),
    }
}

fn error_response(status: StatusCode, message: String) -> Response {
    let body = ErrorBody::new(message);
    (status, Json(body)).into_response()
}
