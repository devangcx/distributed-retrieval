use axum::Router;
use axum::http::StatusCode;
use axum::routing::{get, post};

async fn hello_world() -> &'static str {
    "Hello, World!"
}

async fn hello_axum() -> String {
    format!("Welcome to Axum {}!", env!("CARGO_PKG_VERSION"))
}

async fn health_check() -> &'static str {
    "OK"
}

async fn with_status() -> (StatusCode, &'static str) {
    (StatusCode::OK, "Resource created.")
}

async fn conditional_response() -> (StatusCode, &'static str) {
    let condition = true; // This could be based on some logic
    if condition {
        (StatusCode::OK, "Everything is working.")
    } else {
        (StatusCode::BAD_REQUEST, "Service is down.")
    }
}

async fn echo(body: String) -> String {
    format!("You said: {}", body)
}

#[tokio::main]
async fn main() {
    let app = Router::new()
        .route("/", get(hello_world))
        .route("/hello", get(hello_axum))
        .route("/health", get(health_check))
        .route("/created", get(with_status))
        .route("/conditional", get(conditional_response))
        .route("/echo", post(echo));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .expect("Failed to bind to port 3000");

    println!("Server running on port 3000");

    axum::serve(listener, app).await.unwrap();
}
