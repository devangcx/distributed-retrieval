use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct VectorFilter {
    pub release_year_from: Option<i32>,
    pub release_year_to: Option<i32>,
    pub genre_ids: Vec<i32>,
    pub industry: Option<String>,
    pub country_codes: Vec<String>,
}

impl Default for VectorFilter {
    fn default() -> Self {
        Self {
            release_year_from: None,
            release_year_to: None,
            genre_ids: Vec::new(),
            industry: None,
            country_codes: Vec::new(),
        }
    }
}

impl VectorFilter {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        if self.release_year_from.is_none()
            && self.release_year_to.is_none()
            && self.genre_ids.is_empty()
            && self.industry.is_none()
            && self.country_codes.is_empty()
        {
            return Err("filter must contain at least one condition");
        }

        if let (Some(from), Some(to)) = (self.release_year_from, self.release_year_to) {
            if from > to {
                return Err("release_year_from must not exceed release_year_to");
            }
        }

        if let Some(industry) = &self.industry {
            if industry.trim().is_empty() {
                return Err("filter industry must not be blank");
            }
        }

        if self
            .country_codes
            .iter()
            .any(|country_code| country_code.trim().is_empty())
        {
            return Err("filter country codes must not be blank");
        }

        Ok(())
    }

    pub(crate) fn json(&self) -> Value {
        let mut conditions = Vec::new();

        if self.release_year_from.is_some() || self.release_year_to.is_some() {
            let mut range = serde_json::Map::new();

            if let Some(year) = self.release_year_from {
                range.insert("gte".to_owned(), json!(year));
            }

            if let Some(year) = self.release_year_to {
                range.insert("lte".to_owned(), json!(year));
            }

            conditions.push(json!({
                "key": "release_year",
                "range": range,
            }));
        }

        if !self.genre_ids.is_empty() {
            conditions.push(json!({
                "key": "genre_ids",
                "match": { "any": self.genre_ids },
            }));
        }

        if let Some(industry) = &self.industry {
            conditions.push(json!({
                "key": "industry",
                "match": { "value": industry },
            }));
        }

        if !self.country_codes.is_empty() {
            conditions.push(json!({
                "key": "country_codes",
                "match": { "any": self.country_codes },
            }));
        }

        json!({ "must": conditions })
    }
}
