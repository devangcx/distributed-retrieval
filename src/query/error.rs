use super::Shard;

#[derive(Debug)]
pub enum QueryError {
    Invalid(&'static str),
    Unavailable(Shard),
    Timeout(Shard),
}
