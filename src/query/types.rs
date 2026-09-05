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

#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Industry {
    Hollywood,
    Bollywood,
    OtherOrAmbiguous,
}

impl Industry {
    pub(crate) fn postgres_label(self) -> &'static str {
        match self {
            Self::Hollywood => "hollywood",
            Self::Bollywood => "bollywood",
            Self::OtherOrAmbiguous => "other",
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Shard {
    A,
    B,
}
