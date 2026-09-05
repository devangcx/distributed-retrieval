use serde::Deserialize;

use super::{Filters, Industry, Layout, Routing, Shard};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QueryRequest {
    pub layout: Layout,
    pub routing: Routing,
    #[serde(default)]
    pub filters: Filters,
    // Maximum number of results to return (must be between 1 and 100)
    pub limit: usize,
}

impl QueryRequest {
    pub fn validate(&self) -> Result<(), &'static str> {
        if !(1..=100).contains(&self.limit) {
            return Err("limit must be between 1 and 100");
        }

        self.filters.validate()
    }

    pub fn shards(&self) -> Vec<Shard> {
        // Skip a shard only when it cannot contain a match
        if self.routing == Routing::Selective && 
            self.layout == Layout::Industry {
            match self.filters.industry {
                Some(Industry::Hollywood) => return vec![Shard::A],
                Some(Industry::Bollywood) | Some(Industry::OtherOrAmbiguous) => {
                    return vec![Shard::B];
                }
                None => {}
            }
        }

        vec![Shard::A, Shard::B]
    }
}
