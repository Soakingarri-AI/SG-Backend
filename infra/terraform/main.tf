# =============================================================================
# SoakinGarri AI — core AWS infrastructure (sketch)
# This is an intentionally trimmed skeleton showing the resource shape; wire up
# VPC/subnets, IAM, and ACM certificates for a full deployment.
# =============================================================================
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "root_domain" {
  type    = string
  default = "soakingarri.com"
}

# --- Aurora PostgreSQL (pgvector) ---
resource "aws_rds_cluster" "aurora" {
  cluster_identifier      = "soakingarri-aurora"
  engine                  = "aurora-postgresql"
  engine_version          = "16.1"
  database_name           = "soakingarri"
  master_username         = "soakingarri"
  manage_master_user_password = true
  storage_encrypted       = true
  # `CREATE EXTENSION vector` is run once via a bootstrap task / migration.
}

# --- ElastiCache Redis ---
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "soakingarri-redis"
  description          = "Rate limiting + cache"
  engine              = "redis"
  node_type           = "cache.t4g.micro"
  num_cache_clusters  = 2
  automatic_failover_enabled = true
}

# --- S3 asset bucket (served via CloudFront) ---
resource "aws_s3_bucket" "assets" {
  bucket = "soakingarri-assets"
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- ECR repositories ---
resource "aws_ecr_repository" "api" {
  name = "soakingarri-api"
}

resource "aws_ecr_repository" "web" {
  name = "soakingarri-web"
}

# --- ECS cluster (Fargate) ---
resource "aws_ecs_cluster" "main" {
  name = "soakingarri"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}
