use serde_json::Value;

use crate::Shard;

pub struct MovieDetails {
    pub movie_id: u64,
    pub shard: Shard,
    pub details: Value,
}
