use super::database::classify_query_error;
use crate::Shard;

#[derive(Debug)]
pub enum QueryError {
    Invalid(&'static str),
    InvalidSql(Shard, String),
    InvalidResult(Shard, String),
    PoolUnavailable(Shard),
    DatabaseUnavailable(Shard),
    Timeout(Shard),
}

#[derive(Debug)]
pub(crate) enum ExecuteError {
    PoolUnavailable,
    InvalidStatement(String),
    InvalidResult(String),
    DatabaseUnavailable,
}

// Map Diesel errors to ExecuteError
impl From<diesel::result::Error> for ExecuteError {
    fn from(error: diesel::result::Error) -> Self {
        classify_query_error(error)
    }
}
