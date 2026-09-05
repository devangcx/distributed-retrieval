use distributed_retrieval::env::Env;

mod env;
use crate::env::Env;

#[tokio::main]
async fn main() {
    let env = Env::load().unwrap_or_else(|err| {
        eprintln!("Failed to load the environment variables: {}", err);
        std::process::exit(1);
    });

    println!("App successfully started using orchestrator bind: {}", env.orchestrator_bind);
}
