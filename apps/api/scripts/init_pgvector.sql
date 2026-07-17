-- Enables the pgvector extension. Mirrors the Aurora PostgreSQL bootstrap
-- (`CREATE EXTENSION vector`) executed via the RDS parameter/init process.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
