use serde::Deserialize;

use crate::{
    Layout, Routing, Shard,
    qdrant::{QueryVector, VectorFilter, VectorSearchRequest},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DenseQueryRequest {
    pub layout: Layout,
    pub routing: Routing,
    pub shard: Option<Shard>,
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
            vector_name: self.vector_name,
            vector: QueryVector::Dense(self.vector),
            filter: self.filter,
            limit: self.limit,
        }
    }
}
