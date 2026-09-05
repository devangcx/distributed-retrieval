use serde::Deserialize;

use super::{Layout, Routing, Shard};

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
