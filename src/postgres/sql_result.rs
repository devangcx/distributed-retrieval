use diesel::sql_types::Json;
use serde_json::Value;

#[derive(diesel::QueryableByName)]
pub(crate) struct SqlResult {
    #[diesel(sql_type = Json)]
    pub(crate) row: Value,
}
