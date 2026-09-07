use crate::Shard;

#[derive(Debug)]
pub enum QdrantError {
    Invalid(&'static str),
    Unavailable(Shard),
    InvalidResponse(Shard),
    Timeout(Shard),
}
