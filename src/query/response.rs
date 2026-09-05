use serde::Serialize;
use serde_json::Value;

use super::{Layout, Routing};
use crate::ShardTiming;

#[derive(Serialize)]
pub struct QueryResponse {
    pub layout: Layout,
    pub routing: Routing,
    pub results: Vec<Value>,
    pub shards: Vec<ShardTiming>,
    pub total_ms: f64,
}
