use super::Shard;

#[derive(Debug)]
pub enum QueryError {
    Invalid(&'static str),
    InvalidSql(Shard, String),
    InvalidResult(Shard, String),
    PoolUnavailable(Shard),
    DatabaseUnavailable(Shard),
    Timeout(Shard),
}
