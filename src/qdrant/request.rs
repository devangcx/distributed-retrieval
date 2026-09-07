use crate::{Layout, Routing, Shard};

use super::{QueryVector, VectorFilter};

pub const DENSE_VECTOR_DIMENSIONS: usize = 1024;

pub struct VectorSearchRequest {
    pub layout: Layout,
    pub routing: Routing,
    pub shard: Option<Shard>,
    pub vector_name: String,
    pub vector: QueryVector,
    pub filter: Option<VectorFilter>,
    pub limit: usize,
}

impl VectorSearchRequest {
    pub fn validate(&self) -> Result<(), &'static str> {
        if !(1..=100).contains(&self.limit) {
            return Err("limit must be between 1 and 100");
        }

        match &self.vector {
            QueryVector::Dense(values) => {
                if self.vector_name != "overview_dense" && self.vector_name != "full_document_dense"
                {
                    return Err("dense vector_name must be overview_dense or full_document_dense");
                }
                if values.len() != DENSE_VECTOR_DIMENSIONS {
                    return Err("dense vector must contain 1024 values");
                }
                if values.iter().any(|value| !value.is_finite()) {
                    return Err("dense vector values must be finite");
                }
            }
            QueryVector::Sparse(vector) => {
                if self.vector_name != "metadata_sparse" {
                    return Err("sparse vector_name must be metadata_sparse");
                }
                if vector.indices.is_empty() {
                    return Err("sparse vector must not be empty");
                }
                if vector.indices.len() != vector.values.len() {
                    return Err("sparse vector indices and values must have equal lengths");
                }
                if vector.values.iter().any(|value| !value.is_finite()) {
                    return Err("sparse vector values must be finite");
                }
            }
        }

        if let Some(filter) = &self.filter {
            if let Err(message) = filter.validate() {
                return Err(message);
            }
        }

        match self.routing {
            Routing::Broadcast if self.shard.is_some() => {
                Err("broadcast routing must not specify a shard")
            }
            Routing::Selective if self.shard.is_none() => Err("selective routing requires a shard"),
            Routing::Broadcast | Routing::Selective => Ok(()),
        }
    }

    pub fn shards(&self) -> Vec<Shard> {
        match self.routing {
            Routing::Broadcast => vec![Shard::A, Shard::B],
            Routing::Selective => vec![
                self.shard
                    .expect("validated selective requests always identify a shard"),
            ],
        }
    }
}
