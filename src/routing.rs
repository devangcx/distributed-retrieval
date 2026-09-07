use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Layout {
    Hash,
    Industry,
}

impl Layout {
    pub(crate) fn schema(self) -> &'static str {
        match self {
            Self::Hash => "hash_layout",
            Self::Industry => "industry_layout",
        }
    }
}

#[derive(Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Routing {
    Broadcast,
    Selective,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Shard {
    A,
    B,
}
