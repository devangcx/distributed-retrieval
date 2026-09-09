use crate::{QueryError, qdrant::QdrantError};

pub enum VectorQueryError {
    Qdrant(QdrantError),
    Postgres(QueryError),
}
