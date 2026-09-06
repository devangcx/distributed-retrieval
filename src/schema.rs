// @generated automatically by Diesel CLI.

pub mod sql_types {
    #[derive(diesel::sql_types::SqlType)]
    #[diesel(postgres_type(name = "credit_type"))]
    pub struct CreditType;

    #[derive(diesel::sql_types::SqlType)]
    #[diesel(postgres_type(name = "industry_type"))]
    pub struct IndustryType;

    #[derive(diesel::sql_types::SqlType)]
    #[diesel(postgres_type(name = "ingestion_status"))]
    pub struct IngestionStatus;
}

diesel::table! {
    countries (country_code) {
        #[max_length = 2]
        country_code -> Varchar,
        name -> Text,
    }
}

diesel::table! {
    genres (genre_id) {
        genre_id -> Int4,
        name -> Text,
    }
}

diesel::table! {
    use diesel::sql_types::*;
    use super::sql_types::IngestionStatus;

    ingestion_runs (run_id) {
        run_id -> Uuid,
        source_name -> Text,
        schema_version -> Text,
        input_checksum -> Text,
        source_record_count -> Int4,
        shard_record_count -> Int4,
        layout_strategy -> Text,
        shard_id -> Text,
        status -> IngestionStatus,
        started_at -> Timestamptz,
        completed_at -> Nullable<Timestamptz>,
        details -> Jsonb,
    }
}

diesel::table! {
    movie_countries (movie_id, country_code) {
        movie_id -> Int8,
        #[max_length = 2]
        country_code -> Varchar,
    }
}

diesel::table! {
    use diesel::sql_types::*;
    use super::sql_types::CreditType;

    movie_credits (credit_id) {
        credit_id -> Text,
        movie_id -> Int8,
        person_id -> Int8,
        credit_type -> CreditType,
        character_name -> Nullable<Text>,
        cast_order -> Nullable<Int4>,
    }
}

diesel::table! {
    movie_genres (movie_id, genre_id) {
        movie_id -> Int8,
        genre_id -> Int4,
    }
}

diesel::table! {
    use diesel::sql_types::*;
    use super::sql_types::IndustryType;

    movies (movie_id) {
        movie_id -> Int8,
        title -> Text,
        original_title -> Nullable<Text>,
        #[max_length = 2]
        original_language -> Nullable<Varchar>,
        release_date -> Nullable<Date>,
        overview -> Text,
        runtime_minutes -> Nullable<Int4>,
        vote_average -> Nullable<Numeric>,
        vote_count -> Int4,
        industry -> IndustryType,
    }
}

diesel::table! {
    people (person_id) {
        person_id -> Int8,
        name -> Text,
    }
}

diesel::joinable!(movie_countries -> countries (country_code));
diesel::joinable!(movie_countries -> movies (movie_id));
diesel::joinable!(movie_credits -> movies (movie_id));
diesel::joinable!(movie_credits -> people (person_id));
diesel::joinable!(movie_genres -> genres (genre_id));
diesel::joinable!(movie_genres -> movies (movie_id));

diesel::allow_tables_to_appear_in_same_query!(
    countries,
    genres,
    ingestion_runs,
    movie_countries,
    movie_credits,
    movie_genres,
    movies,
    people,
);
