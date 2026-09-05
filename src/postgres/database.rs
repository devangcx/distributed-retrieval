use diesel::{result::DatabaseErrorKind, sql_query};
use diesel_async::pooled_connection::{AsyncDieselConnectionManager, deadpool::Pool};
use diesel_async::{AsyncConnection, AsyncPgConnection, RunQueryDsl};
use serde_json::Value;

use super::{ExecuteError, SqlResult};

// Asynchronous PostgreSQL connection pool managed by Deadpool through Diesel Async.
pub(crate) type PgPool = Pool<AsyncPgConnection>;

pub(crate) fn pool(shard_url: String) -> Result<PgPool, String> {
    let manager = AsyncDieselConnectionManager::<AsyncPgConnection>::new(shard_url);
    let pool_result = Pool::builder(manager).max_size(4).build();

    match pool_result {
        Ok(pool) => Ok(pool),
        Err(error) => Err(format!("Could not create PostgreSQL pool: {error:?}")),
    }
}

pub(crate) async fn execute(
    pool: &PgPool,
    schema: &'static str,
    statement: &str,
) -> Result<Vec<Value>, ExecuteError> {
    // Checkout a connection from the pool
    let mut connection = match pool.get().await {
        Ok(connection) => connection,
        Err(error) => {
            // The pool's internal error is intentionally hidden from API responses.
            drop(error);
            return Err(ExecuteError::PoolUnavailable);
        }
    };

    let statement = statement.trim().trim_end_matches(';').trim_end();
    let json_statement =
        format!("SELECT row_to_json(query_result) AS row FROM ({statement}) AS query_result");

    connection
        .transaction(async |connection| {
            // Reject mutations.
            let read_only_result = sql_query("SET TRANSACTION READ ONLY")
                .execute(&mut *connection)
                .await;

            if let Err(error) = read_only_result {
                return Err(classify_query_error(error));
            }

            // SET LOCAL is discarded at transaction end, so a pooled connection
            // cannot leak this request's selected layout into another request.
            let select_layout_result = sql_query(format!("SET LOCAL search_path TO {schema}"))
                .execute(&mut *connection)
                .await;

            if let Err(error) = select_layout_result {
                return Err(classify_query_error(error));
            }

            let query_result = sql_query(json_statement)
                .load::<SqlResult>(&mut *connection)
                .await;

            let rows = match query_result {
                Ok(rows) => rows,
                Err(error) => return Err(classify_query_error(error)),
            };

            let mut results = Vec::with_capacity(rows.len());
            for row in rows {
                results.push(row.row);
            }

            Ok(results)
        })
        .await
}

pub(super) fn classify_query_error(error: diesel::result::Error) -> ExecuteError {
    match error {
        diesel::result::Error::DeserializationError(error) => {
            ExecuteError::InvalidResult(error.to_string())
        }
        diesel::result::Error::DatabaseError(
            DatabaseErrorKind::ClosedConnection,
            error_information,
        ) => {
            drop(error_information);
            ExecuteError::DatabaseUnavailable
        }
        diesel::result::Error::DatabaseError(
            DatabaseErrorKind::UnableToSendCommand,
            error_information,
        ) => {
            drop(error_information);
            ExecuteError::DatabaseUnavailable
        }
        diesel::result::Error::DatabaseError(
            DatabaseErrorKind::ReadOnlyTransaction,
            error_information,
        ) => {
            drop(error_information);
            ExecuteError::InvalidStatement("SQL statement must be read-only".to_owned())
        }
        diesel::result::Error::DatabaseError(error_kind, information) => {
            ExecuteError::InvalidStatement(format!("{} ({error_kind:?})", information.message()))
        }
        diesel::result::Error::InvalidCString(error) => {
            ExecuteError::InvalidStatement(error.to_string())
        }
        diesel::result::Error::QueryBuilderError(error) => {
            ExecuteError::InvalidStatement(error.to_string())
        }
        other_error => {
            drop(other_error);
            ExecuteError::DatabaseUnavailable
        }
    }
}
