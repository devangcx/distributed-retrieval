use serde::Deserialize;

use crate::{
    Layout, Routing, Shard,
    qdrant::{ExecutionOrder, QueryVector, VectorFilter, VectorSearchRequest},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DenseQueryRequest {
    pub layout: Layout,
    pub routing: Routing,
    pub shard: Option<Shard>,
    pub execution_order: ExecutionOrder,
    pub vector_name: String,
    pub vector: Vec<f32>,
    pub filter: Option<VectorFilter>,
    pub limit: usize,
}

impl DenseQueryRequest {
    pub fn into_search_request(self) -> VectorSearchRequest {
        VectorSearchRequest {
            layout: self.layout,
            routing: self.routing,
            shard: self.shard,
            execution_order: self.execution_order,
            vector_name: self.vector_name,
            vector: QueryVector::Dense(self.vector),
            filter: self.filter,
            limit: self.limit,
        }
    }
}
