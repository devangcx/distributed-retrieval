use serde_json::{Value, json};

use super::SparseVector;

pub enum QueryVector {
    Dense(Vec<f32>),
    Sparse(SparseVector),
}

impl QueryVector {
    /// Encode a dense array or sparse index/value object for Qdrant's query field.
    pub(crate) fn json(&self) -> Value {
        match self {
            Self::Dense(values) => json!(values),
            Self::Sparse(vector) => json!({
                "indices": vector.indices,
                "values": vector.values,
            }),
        }
    }
}
