use serde::Deserialize;

use super::Industry;

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Filters {
    pub movie_id: Option<i64>,
    pub industry: Option<Industry>,
    pub year_from: Option<i32>,
    pub year_to: Option<i32>,
    pub genre_id: Option<i32>,
    pub director_id: Option<i64>,
}

impl Filters {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        match self.movie_id {
            Some(movie_id) =>{
                if movie_id <= 0 {
                    return Err("movie_id must be positive");
                }
            }
            _ => {}
        }

        match self.genre_id {
            Some(genre_id) =>{
                if genre_id <= 0 {
                    return Err("genre_id must be positive");
                }
            }
            _ => {}
        }

        match self.director_id {
            Some(director_id) =>{
                if director_id <= 0 {
                    return Err("director_id must be positive");
                }
            }
            _ => {}
        }

        match self.year_from {
            Some(year_from) => {
                if !(1..=9999).contains(&year_from) {
                    return Err("year_from must be between 1 and 9999");
                }
            }
            _ => {}
        }

        match self.year_to {
            Some(year_to) => {
                if !(1..=9999).contains(&year_to) {
                    return Err("year_to must be between 1 and 9999");
                }
            }
            _ => {}
        }

        match (self.year_from, self.year_to) {
            (Some(year_from), Some(year_to)) => {
                if year_from > year_to {
                    return Err("year_from must not exceed year_to");
                }
            }
            _ => {}
        }

        Ok(())
    }
}
