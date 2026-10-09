use reqwest::Client;
use serde_json::{Value, json};
use std::time::Duration;

use crate::{
    Layout, Shard,
    qdrant::{QdrantError, VectorSearchRequest, VectorSearchResult},
};

#[derive(Clone)]
pub struct QdrantExecutor {
    client: Client,
    shard_a_url: String,
    shard_b_url: String,
}

impl QdrantExecutor {
    /// Build a reusable HTTP client with a timeout for each Qdrant request.
    /// Return client construction errors without checking shard availability.
    pub fn new(
        shard_a_url: String,
        shard_b_url: String,
        timeout: Duration,
    ) -> Result<Self, String> {
        let client = match Client::builder().timeout(timeout).build() {
            Ok(client) => client,
            Err(error) => return Err(error.to_string()),
        };

        Ok(Self {
            client,
            shard_a_url: shard_a_url.trim_end_matches('/').to_owned(),
            shard_b_url: shard_b_url.trim_end_matches('/').to_owned(),
        })
    }

    /// Validate and search selected shards, merging by descending score to the limit.
    ///
    /// Any selected shard failure fails the search. This layer applies a supplied
    /// Qdrant filter directly. Execution-order handling belongs to the orchestrator.
    pub async fn search(
        &self,
        request: VectorSearchRequest,
    ) -> Result<Vec<VectorSearchResult>, QdrantError> {
        if let Err(message) = request.validate() {
            return Err(QdrantError::Invalid(message));
        }

        let selected_shards = request.shards();

        let mut results = if selected_shards.len() == 2 {
            let (shard_a_result, shard_b_result) = tokio::join!(
                self.search_shard(Shard::A, &request),
                self.search_shard(Shard::B, &request)
            );
            let mut shard_a_results = match shard_a_result {
                Ok(results) => results,
                Err(error) => return Err(error),
            };
            let shard_b_results = match shard_b_result {
                Ok(results) => results,
                Err(error) => return Err(error),
            };
            shard_a_results.extend(shard_b_results);
            shard_a_results
        } else {
            match self.search_shard(selected_shards[0], &request).await {
                Ok(results) => results,
                Err(error) => return Err(error),
            }
        };

        // Sort the results by their score in descending order before truncating
        // to the requested limit
        results.sort_by(|left, right| right.score.total_cmp(&left.score));
        results.truncate(request.limit);
        Ok(results)
    }

    /// Query the layout's collection on one shard and tag results with their source.
    /// Return a shard-specific error for timeout, HTTP failure, or unreadable data.
    async fn search_shard(
        &self,
        shard: Shard,
        request: &VectorSearchRequest,
    ) -> Result<Vec<VectorSearchResult>, QdrantError> {
        let base_url = match shard {
            Shard::A => &self.shard_a_url,
            Shard::B => &self.shard_b_url,
        };

        let collection = match request.layout {
            Layout::Hash => "movies_hash_v1",
            Layout::Industry => "movies_industry_v1",
        };

        let url = format!("{base_url}/collections/{collection}/points/query");
        let query_vector = request.vector.json();
        let mut body = json!({
            "query": query_vector,
            "using": request.vector_name,
            "limit": request.limit,
            "with_payload": false,
            "with_vector": false
        });

        if let Some(filter) = &request.filter {
            body["filter"] = filter.json();
        }

        let response = match self.client.post(url).json(&body).send().await {
            Ok(response) => response,
            Err(error) if error.is_timeout() => return Err(QdrantError::Timeout(shard)),
            Err(..) => return Err(QdrantError::Unavailable(shard)),
        };

        if !response.status().is_success() {
            return Err(QdrantError::Unavailable(shard));
        }

        let response_body: Value = match response.json().await {
            Ok(response_body) => response_body,
            Err(..) => return Err(QdrantError::InvalidResponse(shard)),
        };

        parse_results(&response_body, shard)
    }
}

/// Read Qdrant points in response order, requiring unsigned numeric IDs and scores.
/// Reject the whole response if any point is missing a readable ID or score.
fn parse_results(response: &Value, shard: Shard) -> Result<Vec<VectorSearchResult>, QdrantError> {
    let points = match response.pointer("/result/points").and_then(Value::as_array) {
        Some(points) => points,
        None => return Err(QdrantError::InvalidResponse(shard)),
    };

    let mut results = Vec::with_capacity(points.len());

    for point in points {
        let movie_id = match point.get("id").and_then(Value::as_u64) {
            Some(movie_id) => movie_id,
            None => return Err(QdrantError::InvalidResponse(shard)),
        };

        let score = match point.get("score").and_then(Value::as_f64) {
            Some(score) => score as f32,
            None => return Err(QdrantError::InvalidResponse(shard)),
        };

        results.push(VectorSearchResult {
            movie_id,
            score,
            shard,
        });
    }

    Ok(results)
}
