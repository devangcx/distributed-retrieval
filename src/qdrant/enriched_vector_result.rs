use serde::Serialize;
use serde_json::Value;

use crate::Shard;

#[derive(Serialize)]
pub struct EnrichedVectorResult {
    pub movie_id: u64,
    pub score: f32,
    pub shard: Shard,
    pub details: Value,
}
