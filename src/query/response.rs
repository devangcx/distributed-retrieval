use serde::Serialize;
use serde_json::Value;

use super::{Layout, Routing};
use crate::ShardStats;

#[derive(Serialize)]
pub struct QueryResponse {
    pub layout: Layout,
    pub routing: Routing,
    pub results: Vec<Value>,
    pub shard_stats: Vec<ShardStats>,
    pub total_ms: f64,
}
