use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionOrder {
    FilterThenSearch,
    SearchThenFilter,
}
