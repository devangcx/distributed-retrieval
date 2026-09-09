use crate::{
    QueryError, QueryRequest, QueryResponse, Shard, ShardStats,
    postgres::{self, ExecuteError, PgPool},
    qdrant::{
        EnrichedVectorResult, ExecutionOrder, QdrantExecutor, VectorQueryError,
        VectorQueryResponse, VectorSearchRequest, VectorSearchResult,
    },
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
        mut request: VectorSearchRequest,
    ) -> Result<VectorQueryResponse, VectorQueryError> {
        let started = Instant::now();
        let layout = request.layout;
        let routing = request.routing;
        let execution_order = request.execution_order;
        let final_limit = request.limit;
        let search_then_filter = match execution_order {
            ExecutionOrder::FilterThenSearch => None,
            ExecutionOrder::SearchThenFilter => {
                // Take the filter out of the request before searching
                let filter = request.filter.take();
                request.limit = 100;
                filter
            }
        };

        let vector_started = Instant::now();
        let ranked_results: Vec<VectorSearchResult> = match self.qdrant.search(request).await {
            Ok(results) => results,
            Err(error) => return Err(VectorQueryError::Qdrant(error)),
        };
        let vector_ms: f64 = vector_started.elapsed().as_secs_f64() * 1000.0;

        let enrichment_started = Instant::now();
        let mut results: Vec<EnrichedVectorResult> =
            match self.enrich_vector_results(layout, ranked_results).await {
                Ok(results) => results,
                Err(error) => return Err(VectorQueryError::Postgres(error)),
            };
        
        // Apply the filter to the results if we are in SearchThenFilter mode
        if let Some(filter) = search_then_filter {
            let mut filtered_results: Vec<EnrichedVectorResult> = Vec::new();

            for result in results {
                let matches_filter = filter.matches(&result.details);

                if matches_filter {
                    filtered_results.push(result);
                }
            }

            results = filtered_results;
        }

        results.truncate(final_limit);
        let enrichment_ms: f64 = enrichment_started.elapsed().as_secs_f64() * 1000.0;

        Ok(VectorQueryResponse {
            layout,
            routing,
            execution_order,
            results,
            vector_ms,
            enrichment_ms,
            total_ms: started.elapsed().as_secs_f64() * 1000.0,
        })
    }

    async fn enrich_vector_results(
        &self,
        layout: crate::Layout,
        ranked_results: Vec<VectorSearchResult>,
    ) -> Result<Vec<EnrichedVectorResult>, QueryError> {
        let mut shard_a_ids: Vec<i64> = Vec::new();
        let mut shard_b_ids: Vec<i64> = Vec::new();

        for result in &ranked_results {
            let movie_id = match i64::try_from(result.movie_id) {
                Ok(movie_id) => movie_id,
                Err(..) => {
                    return Err(QueryError::InvalidResult(
                        result.shard,
                        String::from(
                            "Qdrant returned a movie ID outside PostgreSQL's BIGINT range",
                        ),
                    ));
                }
            };
            match result.shard {
                Shard::A => shard_a_ids.push(movie_id),
                Shard::B => shard_b_ids.push(movie_id),
            }
        }

        let (shard_a_result, shard_b_result) = tokio::join!(
            self.load_details_from_shard(Shard::A, layout, shard_a_ids),
            self.load_details_from_shard(Shard::B, layout, shard_b_ids)
        );
        let mut movie_details: Vec<postgres::MovieDetails> = match shard_a_result {
            Ok(details) => details,
            Err(error) => return Err(error),
        };
        let shard_b_movie_details: Vec<postgres::MovieDetails> = match shard_b_result {
            Ok(details) => details,
            Err(error) => return Err(error),
        };
        movie_details.extend(shard_b_movie_details);

        let mut enriched_results = Vec::with_capacity(ranked_results.len());

        for result in ranked_results {
            let movie_details = movie_details
                .iter()
                .find(|details: &&postgres::MovieDetails| {
                    details.movie_id == result.movie_id && details.shard == result.shard
                });

            let details_value = match movie_details {
                Some(movie_details) => movie_details.details.clone(),
                None => {
                    return Err(QueryError::InvalidResult(
                        result.shard,
                        format!("PostgreSQL did not return movie {}", result.movie_id),
                    ));
                }
            };

            enriched_results.push(EnrichedVectorResult {
                movie_id: result.movie_id,
                score: result.score,
                shard: result.shard,
                details: details_value,
            });
        }

        Ok(enriched_results)
    }

    async fn load_details_from_shard(
        &self,
        shard: Shard,
        layout: crate::Layout,
        movie_ids: Vec<i64>,
    ) -> Result<Vec<postgres::MovieDetails>, QueryError> {
        let pool = match shard {
            Shard::A => &self.shard_a,
            Shard::B => &self.shard_b,
        };

        let timed_result = tokio::time::timeout(
            self.shard_timeout,
            postgres::fetch_movie_details(pool, layout, shard, movie_ids),
        )
        .await;

        match timed_result {
            Ok(Ok(details)) => Ok(details),
            Ok(Err(error)) => Err(query_error_for(shard, error)),
            Err(timeout_error) => {
                drop(timeout_error);
                Err(QueryError::Timeout(shard))
            }
        }
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
