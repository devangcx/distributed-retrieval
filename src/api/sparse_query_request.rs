use serde::Deserialize;

use crate::{
    Layout, Routing, Shard,
    qdrant::{QueryVector, SparseVector, VectorFilter, VectorSearchRequest},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SparseQueryRequest {
    pub layout: Layout,
    pub routing: Routing,
    pub shard: Option<Shard>,
    pub indices: Vec<u32>,
    pub values: Vec<f32>,
    pub filter: Option<VectorFilter>,
    pub limit: usize,
}

impl SparseQueryRequest {
    pub fn into_search_request(self) -> VectorSearchRequest {
        VectorSearchRequest {
            layout: self.layout,
            routing: self.routing,
            shard: self.shard,
            vector_name: String::from("metadata_sparse"),
            vector: QueryVector::Sparse(SparseVector {
                indices: self.indices,
                values: self.values,
            }),
            filter: self.filter,
            limit: self.limit,
        }
    }
}
