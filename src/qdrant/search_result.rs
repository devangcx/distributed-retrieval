use crate::Shard;

#[derive(Debug, PartialEq)]
pub struct VectorSearchResult {
    pub movie_id: u64,
    pub score: f32,
    pub shard: Shard,
}
