use diesel::{
    sql_query,
    sql_types::{Array, BigInt},
};
use diesel_async::{AsyncConnection, RunQueryDsl};

use crate::{Layout, Shard};

use super::{ExecuteError, MovieDetails, PgPool, SqlResult, database::classify_query_error};

/// Load authoritative details from one layout and tag them with the supplied shard.
///
/// Empty IDs avoid a database call. Results are unordered and may omit missing
/// movies. The orchestrator restores ranking and checks completeness. Industry
/// labels are translated back to the canonical values used by vector filters.
pub(crate) async fn fetch_movie_details(
    pool: &PgPool,
    layout: Layout,
    shard: Shard,
    movie_ids: Vec<i64>,
) -> Result<Vec<MovieDetails>, ExecuteError> {
    // Return early if there are no movie IDs to query.
    if movie_ids.is_empty() {
        return Ok(Vec::new());
    }

    let mut connection = match pool.get().await {
        Ok(connection) => connection,
        Err(error) => {
            drop(error);
            return Err(ExecuteError::PoolUnavailable);
        }
    };

    connection
        .transaction(async |connection| {
            let read_only_result = sql_query("SET TRANSACTION READ ONLY")
                .execute(&mut *connection)
                .await;
            if let Err(error) = read_only_result {
                return Err(classify_query_error(error));
            }

            let select_layout_result =
                sql_query(format!("SET LOCAL search_path TO {}", layout.schema()))
                    .execute(&mut *connection)
                    .await;
            if let Err(error) = select_layout_result {
                return Err(classify_query_error(error));
            }

            // Prepare the SQL statement for loading movie details.
            // Give me the following for loading movie details by their IDs.
            // movie titles, release dates, overviews, industry, genres,
            // directors, and countries by their IDs.
            // The genres, directors, and countries are aggregated as JSON arrays.
            let statement: &str = r#"
                SELECT json_build_object(
                    'movie_id', movies.movie_id,
                    'title', movies.title,
                    'release_date', movies.release_date,
                    'overview', movies.overview,
                    'industry', CASE movies.industry::text
                        WHEN 'other' THEN 'other_or_ambiguous'
                        ELSE movies.industry::text
                    END,
                    'genres', COALESCE((
                        SELECT json_agg(genres.name ORDER BY genres.name)
                        FROM movie_genres
                        JOIN genres ON genres.genre_id = movie_genres.genre_id
                        WHERE movie_genres.movie_id = movies.movie_id
                    ), '[]'::json),
                    'genre_ids', COALESCE((
                        SELECT json_agg(movie_genres.genre_id ORDER BY movie_genres.genre_id)
                        FROM movie_genres
                        WHERE movie_genres.movie_id = movies.movie_id
                    ), '[]'::json),
                    'directors', COALESCE((
                        SELECT json_agg(people.name ORDER BY people.name)
                        FROM movie_credits
                        JOIN people ON people.person_id = movie_credits.person_id
                        WHERE movie_credits.movie_id = movies.movie_id
                          AND movie_credits.credit_type = 'director'
                    ), '[]'::json),
                    'countries', COALESCE((
                        SELECT json_agg(countries.country_code ORDER BY countries.country_code)
                        FROM movie_countries
                        JOIN countries ON countries.country_code = movie_countries.country_code
                        WHERE movie_countries.movie_id = movies.movie_id
                    ), '[]'::json)
                ) AS row
                FROM movies
                WHERE movies.movie_id = ANY($1)
            "#;
            let query_result = sql_query(statement)
                .bind::<Array<BigInt>, _>(movie_ids)
                .load::<SqlResult>(&mut *connection)
                .await;

            let rows = match query_result {
                Ok(rows) => rows,
                Err(error) => return Err(classify_query_error(error)),
            };

            let mut details: Vec<MovieDetails> = Vec::with_capacity(rows.len());
            for row in rows {
                let movie_id = match row.row.get("movie_id").and_then(serde_json::Value::as_u64) {
                    Some(movie_id) => movie_id,
                    None => {
                        return Err(ExecuteError::InvalidResult(String::from(
                            "movie details did not contain a valid movie_id",
                        )));
                    }
                };
                details.push(MovieDetails {
                    movie_id,
                    shard,
                    details: row.row,
                });
            }

            Ok(details)
        })
        .await
}
