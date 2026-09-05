mod error;
mod request;
mod response;
mod types;

// Make these available under the `query` module
pub use error::QueryError;
pub use request::QueryRequest;
pub use response::QueryResponse;
pub use types::{Layout, Routing, Shard};
