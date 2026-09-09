use serde::Serialize;

use crate::{ExecutionOrder, Layout, Routing, qdrant::EnrichedVectorResult};

#[derive(Serialize)]
pub struct VectorQueryResponse {
    pub layout: Layout,
    pub routing: Routing,
    pub execution_order: ExecutionOrder,
    pub results: Vec<EnrichedVectorResult>,
    pub vector_ms: f64,
    pub enrichment_ms: f64,
    pub total_ms: f64,
}
