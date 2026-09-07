use serde::Serialize;

use crate::Shard;

/// Per-shard execution statistics included for observability.
///
/// These values make it possible to identify slow or uneven shards and are
/// kept separate from the query's total duration because broadcast queries
/// execute concurrently.
#[derive(Serialize)]
pub struct ShardStats {
    pub shard: Shard,
    pub elapsed_ms: f64,
    pub returned_rows: usize,
}
