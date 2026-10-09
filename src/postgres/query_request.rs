use serde::Deserialize;

use crate::{Layout, Routing, Shard};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QueryRequest {
    pub layout: Layout,
    pub routing: Routing,
    pub shard: Option<Shard>,
    pub sql: String,
    // Maximum number of results to return (must be between 1 and 100)
    pub limit: usize,
}

impl QueryRequest {
    /// Check the result limit, nonempty SQL, and routing/shard combination.
    /// SQL syntax and read-only enforcement are left to PostgreSQL at execution.
    pub fn validate(&self) -> Result<(), &'static str> {
        if !(1..=100).contains(&self.limit) {
            return Err("limit must be between 1 and 100");
        }

        if self.sql.trim().is_empty() {
            return Err("sql must not be empty");
        }

        match self.routing {
            Routing::Broadcast if self.shard.is_some() => {
                Err("broadcast routing must not specify a shard")
            }
            Routing::Selective if self.shard.is_none() => Err("selective routing requires a shard"),
            Routing::Broadcast | Routing::Selective => Ok(()),
        }
    }

    /// Return the physical shards selected by this request.
    ///
    /// Call `validate` successfully before selecting shards.
    ///
    /// # Panics
    ///
    /// Panics if routing is selective and no shard was supplied.
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
