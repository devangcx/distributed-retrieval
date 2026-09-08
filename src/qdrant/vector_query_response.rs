use serde::Serialize;

use crate::{Layout, Routing, qdrant::VectorSearchResult};

#[derive(Serialize)]
pub struct VectorQueryResponse {
    pub layout: Layout,
    pub routing: Routing,
    pub results: Vec<VectorSearchResult>,
    pub total_ms: f64,
}
