#!/usr/bin/env python3
"""
setup_kb.py
===========

Creates, or finds, the Amazon Bedrock Knowledge Base behind Ask's retrieval:

    s3://soakingarri-rag-corpus/pdfs/     PDFs + .metadata.json sidecars,
                                          written by rag_collector/pdf_downloader.py
      -> Knowledge Base soakingarri-ask-kb  parses, chunks, embeds (Titan v2, 1024 dims)
      -> S3 Vectors soakingarri-rag-vectors / ask-corpus

Idempotent: every resource is looked up by name and created only when missing,
so a re-run just prints the ids the API needs (BEDROCK_KB_ID). `--sync` starts
an ingestion job over the bucket and waits for it; ingestion is incremental, so
only new, changed or deleted PDFs are re-embedded.

    python setup_kb.py                 # create what is missing, print the ids
    python setup_kb.py --sync          # ... then ingest the bucket

Needs a boto3 recent enough to know S3 Vectors (the collector's venv has one)
and the `soakingarri` AWS profile, or credentials in the environment.

Fixed at creation - changing any of these means a new index or data source and
a full re-ingest: the dimensions and distance metric, the non-filterable
metadata keys, and the chunking.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

ACCOUNT = "431856071890"
REGION = "us-east-1"

CORPUS_BUCKET = "soakingarri-rag-corpus"
CORPUS_PREFIX = "pdfs/"
VECTOR_BUCKET = "soakingarri-rag-vectors"
INDEX = "ask-corpus"
KB_NAME = "soakingarri-ask-kb"
DATA_SOURCE = "corpus-pdfs"
ROLE = "soakingarri-kb"
BOUNDARY = f"arn:aws:iam::{ACCOUNT}:policy/soakingarri-workload-boundary"

EMBED_MODEL = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
DIMENSIONS = 1024   # matches EMBEDDING_DIM in the API

# S3 Vectors allows 2 KB of filterable metadata per vector. The chunk text and
# the free-text sidecar fields would overflow it, so they are stored but not
# filterable; the short fields (host, concept, open_access, author_is_african)
# stay filterable.
NON_FILTERABLE = [
    "AMAZON_BEDROCK_TEXT",
    "AMAZON_BEDROCK_METADATA",
    "title",
    "author",
    "source_url",
]

# ~500 tokens is a few paragraphs of a paper: enough for the tutor to quote
# with context, small enough that five chunks fit a prompt comfortably.
CHUNKING = {
    "chunkingStrategy": "FIXED_SIZE",
    "fixedSizeChunkingConfiguration": {"maxTokens": 500, "overlapPercentage": 15},
}


def _code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


# -- vector store ------------------------------------------------------------ #


def ensure_index(s3v) -> str:
    try:
        s3v.get_vector_bucket(vectorBucketName=VECTOR_BUCKET)
        print(f"vector bucket  {VECTOR_BUCKET} (exists)")
    except ClientError as exc:
        if _code(exc) != "NotFoundException":
            raise
        s3v.create_vector_bucket(vectorBucketName=VECTOR_BUCKET)
        print(f"vector bucket  {VECTOR_BUCKET} (created)")

    try:
        index = s3v.get_index(vectorBucketName=VECTOR_BUCKET, indexName=INDEX)["index"]
        print(f"vector index   {INDEX} (exists)")
    except ClientError as exc:
        if _code(exc) != "NotFoundException":
            raise
        s3v.create_index(
            vectorBucketName=VECTOR_BUCKET,
            indexName=INDEX,
            dataType="float32",
            dimension=DIMENSIONS,
            distanceMetric="cosine",
            metadataConfiguration={"nonFilterableMetadataKeys": NON_FILTERABLE},
        )
        index = s3v.get_index(vectorBucketName=VECTOR_BUCKET, indexName=INDEX)["index"]
        print(f"vector index   {INDEX} (created)")

    if index.get("dimension") != DIMENSIONS:
        sys.exit(f"index {INDEX} has dimension {index.get('dimension')}, expected {DIMENSIONS}")
    return index["indexArn"]


# -- service role ------------------------------------------------------------ #


def ensure_role(iam, index_arn: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": ACCOUNT},
                "ArnLike": {
                    "aws:SourceArn": f"arn:aws:bedrock:{REGION}:{ACCOUNT}:knowledge-base/*"
                },
            },
        }],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "Embed", "Effect": "Allow",
             "Action": "bedrock:InvokeModel", "Resource": EMBED_MODEL},
            {"Sid": "ListCorpus", "Effect": "Allow",
             "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{CORPUS_BUCKET}"},
            {"Sid": "ReadCorpus", "Effect": "Allow",
             "Action": "s3:GetObject",
             "Resource": f"arn:aws:s3:::{CORPUS_BUCKET}/{CORPUS_PREFIX}*"},
            {"Sid": "Vectors", "Effect": "Allow",
             "Action": [
                 "s3vectors:GetIndex", "s3vectors:PutVectors", "s3vectors:GetVectors",
                 "s3vectors:DeleteVectors", "s3vectors:QueryVectors",
             ],
             "Resource": index_arn},
        ],
    }

    created = False
    try:
        arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
        print(f"role           {ROLE} (exists)")
    except ClientError as exc:
        if _code(exc) != "NoSuchEntity":
            raise
        arn = iam.create_role(
            RoleName=ROLE,
            AssumeRolePolicyDocument=json.dumps(trust),
            PermissionsBoundary=BOUNDARY,
            Description="Bedrock Knowledge Base soakingarri-ask-kb: read corpus, embed, write vectors",
        )["Role"]["Arn"]
        created = True
        print(f"role           {ROLE} (created)")
    # Always re-put, so edits to the policy above take effect on a re-run.
    iam.put_role_policy(RoleName=ROLE, PolicyName="knowledge-base", PolicyDocument=json.dumps(policy))
    if created:
        time.sleep(15)   # a new role takes a few seconds to be assumable
    return arn


# -- knowledge base ---------------------------------------------------------- #


def ensure_kb(agent, role_arn: str, index_arn: str) -> str:
    for page in agent.get_paginator("list_knowledge_bases").paginate():
        for kb in page["knowledgeBaseSummaries"]:
            if kb["name"] == KB_NAME:
                print(f"knowledge base {KB_NAME} {kb['knowledgeBaseId']} (exists)")
                return kb["knowledgeBaseId"]

    for attempt in range(6):   # IAM propagation can lag behind create_role
        try:
            kb = agent.create_knowledge_base(
                name=KB_NAME,
                description="Ask SoakinGarri corpus: PDFs by African authors",
                roleArn=role_arn,
                knowledgeBaseConfiguration={
                    "type": "VECTOR",
                    "vectorKnowledgeBaseConfiguration": {
                        "embeddingModelArn": EMBED_MODEL,
                        "embeddingModelConfiguration": {
                            "bedrockEmbeddingModelConfiguration": {
                                "dimensions": DIMENSIONS,
                                "embeddingDataType": "FLOAT32",
                            }
                        },
                    },
                },
                storageConfiguration={
                    "type": "S3_VECTORS",
                    "s3VectorsConfiguration": {"indexArn": index_arn},
                },
            )["knowledgeBase"]
            break
        except ClientError as exc:
            if _code(exc) != "ValidationException" or attempt == 5:
                raise
            print(f"  waiting for the role to propagate ({exc.response['Error']['Message'][:80]})")
            time.sleep(10)

    kb_id = kb["knowledgeBaseId"]
    while (status := agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"]) == "CREATING":
        time.sleep(5)
    if status != "ACTIVE":
        sys.exit(f"knowledge base {kb_id} ended in status {status}")
    print(f"knowledge base {KB_NAME} {kb_id} (created)")
    return kb_id


def ensure_data_source(agent, kb_id: str) -> str:
    for page in agent.get_paginator("list_data_sources").paginate(knowledgeBaseId=kb_id):
        for ds in page["dataSourceSummaries"]:
            if ds["name"] == DATA_SOURCE:
                print(f"data source    {DATA_SOURCE} {ds['dataSourceId']} (exists)")
                return ds["dataSourceId"]

    ds = agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=DATA_SOURCE,
        description=f"s3://{CORPUS_BUCKET}/{CORPUS_PREFIX} (sidecars carry the metadata)",
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{CORPUS_BUCKET}",
                "inclusionPrefixes": [CORPUS_PREFIX],
                "bucketOwnerAccountId": ACCOUNT,
            },
        },
        vectorIngestionConfiguration={"chunkingConfiguration": CHUNKING},
        dataDeletionPolicy="DELETE",
    )["dataSource"]
    print(f"data source    {DATA_SOURCE} {ds['dataSourceId']} (created)")
    return ds["dataSourceId"]


def sync(agent, kb_id: str, ds_id: str) -> int:
    job = agent.start_ingestion_job(
        knowledgeBaseId=kb_id, dataSourceId=ds_id, description="setup_kb.py --sync"
    )["ingestionJob"]
    job_id = job["ingestionJobId"]
    print(f"ingestion job  {job_id} started")
    while job["status"] in ("STARTING", "IN_PROGRESS", "STOPPING"):
        time.sleep(30)
        job = agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )["ingestionJob"]
        stats = job.get("statistics", {})
        print(
            f"  {job['status']:<11} scanned {stats.get('numberOfDocumentsScanned', 0)}, "
            f"indexed {stats.get('numberOfNewDocumentsIndexed', 0)}, "
            f"failed {stats.get('numberOfDocumentsFailed', 0)}"
        )
    print(json.dumps(job.get("statistics", {}), indent=1))
    for reason in job.get("failureReasons", []):
        print(f"  failure: {reason}")
    return 0 if job["status"] == "COMPLETE" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or show the Ask Knowledge Base.")
    parser.add_argument("--profile", default="soakingarri", help="AWS profile ('' for env creds)")
    parser.add_argument("--sync", action="store_true", help="Start an ingestion job and wait.")
    args = parser.parse_args(argv)

    session = boto3.Session(profile_name=args.profile or None, region_name=REGION)
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        sys.exit(f"credentials are not for account {ACCOUNT}")

    index_arn = ensure_index(session.client("s3vectors"))
    role_arn = ensure_role(session.client("iam"), index_arn)
    agent = session.client("bedrock-agent")
    kb_id = ensure_kb(agent, role_arn, index_arn)
    ds_id = ensure_data_source(agent, kb_id)
    print(f"\nBEDROCK_KB_ID={kb_id}\ndata source id {ds_id}")
    return sync(agent, kb_id, ds_id) if args.sync else 0


if __name__ == "__main__":
    sys.exit(main())
