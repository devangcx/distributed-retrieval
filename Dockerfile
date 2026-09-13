# syntax=docker/dockerfile:1

FROM rust:1.97-slim-bookworm AS builder

WORKDIR /app

COPY Cargo.toml Cargo.lock ./
COPY src ./src

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/app/target \
    cargo build --locked --release \
    && cp /app/target/release/distributed-retrieval /app/distributed-retrieval

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder \
    /app/distributed-retrieval \
    /usr/local/bin/distributed-retrieval

EXPOSE 3000

CMD ["distributed-retrieval"]
