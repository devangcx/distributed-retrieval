use serde::Serialize;

#[derive(Serialize)]
pub(crate) struct ErrorBody {
    error: String,
}

impl ErrorBody {
    pub(crate) fn new(error: String) -> Self {
        Self { error }
    }
}
