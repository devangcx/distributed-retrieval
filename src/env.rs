pub (crate) struct Env {
    pub (crate) postgres_shard_a_url: String,
    pub (crate) postgres_shard_b_url: String,
    pub (crate) qdrant_shard_a_url: String,
    pub (crate) qdrant_shard_b_url: String,
    pub orchestrator_bind: String,
}

impl Env {
    pub (crate) fn load() -> Result<Self, &'static str> {
        dotenvy::dotenv().ok();

        let postgres_shard_a_url = std::env::var("POSTGRES_SHARD_A_URL")
            .map_err(|_| "POSTGRES_SHARD_A_URL is required")?;

        let postgres_shard_b_url = std::env::var("POSTGRES_SHARD_B_URL")
            .map_err(|_| "POSTGRES_SHARD_B_URL is required")?;

        let qdrant_shard_a_url = std::env::var("QDRANT_SHARD_A_URL")
            .map_err(|_| "QDRANT_SHARD_A_URL is required")?;

        let qdrant_shard_b_url = std::env::var("QDRANT_SHARD_B_URL")
            .map_err(|_| "QDRANT_SHARD_B_URL is required")?;
        
        let orchestrator_bind = std::env::var("ORCHESTRATOR_BIND")
            .unwrap_or_else(|_| "localhost:3000".to_owned());

        Ok(Self {
            postgres_shard_a_url,
            postgres_shard_b_url,
            orchestrator_bind,
            qdrant_shard_a_url,
            qdrant_shard_b_url,
        })
    }
}
