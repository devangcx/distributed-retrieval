pub(crate) struct Env {
    pub(crate) postgres_shard_a_url: String,
    pub(crate) postgres_shard_b_url: String,
    pub(crate) qdrant_shard_a_url: String,
    pub(crate) qdrant_shard_b_url: String,
    pub(crate) orchestrator_bind: String,
}

impl Env {
    /// Load optional `.env` settings and require all four database URL variables.
    /// Default the bind address to `localhost:3000`.
    pub(crate) fn load() -> Result<Self, &'static str> {
        dotenvy::dotenv().ok();

        let postgres_shard_a_url =
            required_variable("POSTGRES_SHARD_A_URL", "POSTGRES_SHARD_A_URL is required")?;
        let postgres_shard_b_url =
            required_variable("POSTGRES_SHARD_B_URL", "POSTGRES_SHARD_B_URL is required")?;
        let qdrant_shard_a_url =
            required_variable("QDRANT_SHARD_A_URL", "QDRANT_SHARD_A_URL is required")?;
        let qdrant_shard_b_url =
            required_variable("QDRANT_SHARD_B_URL", "QDRANT_SHARD_B_URL is required")?;
        let orchestrator_bind = match std::env::var("ORCHESTRATOR_BIND") {
            Ok(value) => value,
            Err(..) => "localhost:3000".to_owned(),
        };

        Ok(Self {
            postgres_shard_a_url,
            postgres_shard_b_url,
            qdrant_shard_a_url,
            qdrant_shard_b_url,
            orchestrator_bind,
        })
    }
}

fn required_variable(
    name: &'static str,
    missing_message: &'static str,
) -> Result<String, &'static str> {
    match std::env::var(name) {
        Ok(value) => Ok(value),
        Err(..) => Err(missing_message),
    }
}
