use serde::Deserialize;

use crate::{DenseQueryRequest, QueryRequest, SparseQueryRequest};

#[derive(Deserialize)]
#[serde(tag = "query_type", rename_all = "snake_case")]
pub enum ApiQueryRequest {
    Relational(QueryRequest),
    VectorDense(DenseQueryRequest),
    VectorSparse(SparseQueryRequest),
}
