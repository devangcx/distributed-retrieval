use crate::{
    QueryError, QueryRequest, QueryResponse, Shard, ShardStats,
    postgres::{self, ExecuteError, PgPool},
    qdrant::{QdrantError, QdrantExecutor, VectorQueryResponse, VectorSearchRequest},
};
use serde_json::Value;
use std::time::{Duration, Instant};

#[derive(Clone)]
pub struct Orchestrator {
    shard_a: PgPool,
    shard_b: PgPool,
    shard_timeout: Duration,
    qdrant: QdrantExecutor,
}

impl Orchestrator {
    pub fn new(
        shard_a_url: String,
        shard_b_url: String,
        qdrant_shard_a_url: String,
        qdrant_shard_b_url: String,
        shard_timeout: Duration,
    ) -> Result<Self, String> {

        let shard_a = match postgres::pool(shard_a_url) {
            Ok(pool) => pool,
            Err(message) => return Err(message),
        };

        let shard_b = match postgres::pool(shard_b_url) {
            Ok(pool) => pool,
            Err(message) => return Err(message),
        };

        let qdrant =
            match QdrantExecutor::new(qdrant_shard_a_url, qdrant_shard_b_url, shard_timeout) {
                Ok(executor) => executor,
                Err(message) => return Err(message),
            };

        Ok(Self {
            shard_a,
            shard_b,
            shard_timeout,
            qdrant,
        })
    }

    pub async fn vector_query(
        &self,
        request: VectorSearchRequest,
    ) -> Result<VectorQueryResponse, QdrantError> {
        
        let started = Instant::now();
        let layout = request.layout;
        let routing = request.routing;

        let results = match self.qdrant.search(request).await {
            Ok(results) => results,
            Err(error) => return Err(error),
        };

        Ok(VectorQueryResponse {
            layout,
            routing,
            results,
            total_ms: started.elapsed().as_secs_f64() * 1000.0,
        })
    }

    pub async fn query(&self, request: QueryRequest) -> Result<QueryResponse, QueryError> {
        let started = Instant::now();

        if let Err(message) = request.validate() {
            return Err(QueryError::Invalid(message));
        }

        let selected = request.shards();

        // Broadcast routing: query both shards concurrently using `tokio::join!`
        let (mut results, shard_stats) = if selected.len() == 2 {
            let (shard_a_result, shard_b_result) = tokio::join!(
                self.execute_on_shard(Shard::A, &request),
                self.execute_on_shard(Shard::B, &request)
            );

            // Handle the results from both shards
            let (mut shard_a_results, shard_a_timing) = match shard_a_result {
                Ok(result) => result,
                Err(error) => return Err(error),
            };
            let (shard_b_results, shard_b_timing) = match shard_b_result {
                Ok(result) => result,
                Err(error) => return Err(error),
            };

            // Combine the results from both shards
            shard_a_results.extend(shard_b_results);
            (shard_a_results, vec![shard_a_timing, shard_b_timing])

        // For one shard, simply await the result from that shard
        } else {
            let shard_result = self.execute_on_shard(selected[0], &request).await;
            let (results, timing) = match shard_result {
                Ok(result) => result,
                Err(error) => return Err(error),
            };
            (results, vec![timing])
        };

        // Truncate the results to the requested limit
        results.truncate(request.limit);

        Ok(QueryResponse {
            layout: request.layout,
            routing: request.routing,
            results,
            shard_stats,
            total_ms: started.elapsed().as_secs_f64() * 1000.0,
        })
    }

    async fn execute_on_shard(
        &self,
        shard: Shard,
        request: &QueryRequest,
    ) -> Result<(Vec<Value>, ShardStats), QueryError> {
        let started = Instant::now();

        let pool = match shard {
            Shard::A => &self.shard_a,
            Shard::B => &self.shard_b,
        };
        let timed_result = tokio::time::timeout(
            self.shard_timeout,
            postgres::execute(pool, request.layout.schema(), &request.sql),
        )
        .await;

        let execution_result = match timed_result {
            Ok(execution_result) => execution_result,
            Err(timeout_error) => {
                drop(timeout_error);
                return Err(QueryError::Timeout(shard));
            }
        };

        let results = match execution_result {
            Ok(results) => results,
            Err(error) => return Err(query_error_for(shard, error)),
        };

        let stats = ShardStats {
            shard,
            elapsed_ms: started.elapsed().as_secs_f64() * 1000.0,
            returned_rows: results.len(),
        };

        Ok((results, stats))
    }
}

fn query_error_for(shard: Shard, error: ExecuteError) -> QueryError {
    match error {
        ExecuteError::InvalidStatement(message) => QueryError::InvalidSql(shard, message),
        ExecuteError::InvalidResult(message) => QueryError::InvalidResult(shard, message),
        ExecuteError::PoolUnavailable => QueryError::PoolUnavailable(shard),
        ExecuteError::DatabaseUnavailable => QueryError::DatabaseUnavailable(shard),
    }
}
