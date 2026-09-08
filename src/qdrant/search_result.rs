use crate::Shard;
use serde::Serialize;

#[derive(Debug, PartialEq, Serialize)]
pub struct VectorSearchResult {
    pub movie_id: u64,
    pub score: f32,
    pub shard: Shard,
}
