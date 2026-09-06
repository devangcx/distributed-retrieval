use std::time::Duration;

use distributed_retrieval::Orchestrator;
use distributed_retrieval::api;

mod env;
use crate::env::Env;

#[tokio::main]
async fn main() {
    let env = Env::load().unwrap_or_else(|err| {
        eprintln!("Failed to load environment: {}", err);
        std::process::exit(1);
    });

    let orchestrator = Orchestrator::new(
        env.postgres_shard_a_url,
        env.postgres_shard_b_url,
        Duration::from_secs(5),
    )
    .expect("Invalid orchestrator configuration");

    let listener = tokio::net::TcpListener::bind(&env.orchestrator_bind)
        .await
        .expect("Could not bind the HTTP server");

    println!("Orchestrator listening on {}", env.orchestrator_bind);
    axum::serve(listener, api::router(orchestrator))
        .await
        .expect("HTTP server failed");
}
