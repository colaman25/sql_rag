#!/usr/bin/env python3
import os
import sys

import aws_cdk as cdk

from stacks.data_stack import DataStack
from stacks.ecr_stack import EcrStack
from stacks.ecs_stack import EcsStack
from stacks.network_stack import NetworkStack

app = cdk.App()

region = app.node.try_get_context("region") or os.getenv("CDK_DEFAULT_REGION", "eu-west-2")
env = cdk.Environment(account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=region)

image_tag = app.node.try_get_context("image_tag") or "latest"
allowed_cidr = app.node.try_get_context("allowed_cidr") or "0.0.0.0/0"

# `cdk bootstrap` (run without an explicit aws://account/region) and `cdk ls`
# synthesize this app just to discover stacks/environments -- they don't
# actually need these values. So fall back to placeholders instead of
# hard-failing, and only warn. Real values are required for `cdk deploy` of
# SqlRag-Data / SqlRag-Ecs (see infra/README.md).
athena_output_s3 = app.node.try_get_context("athena_output_s3") or "s3://REPLACE_ME_ATHENA_OUTPUT_BUCKET"
athena_database = app.node.try_get_context("athena_database") or "dev"
manifest_path = app.node.try_get_context("manifest_path") or "s3://REPLACE_ME_MANIFEST_BUCKET/manifest.json"

if "REPLACE_ME" in athena_output_s3 or "REPLACE_ME" in manifest_path:
    print(
        "WARNING: athena_output_s3 and/or manifest_path were not set via -c context flags; "
        "using placeholders. This is fine for `cdk bootstrap`/`cdk ls`, but you must pass real "
        "values via -c for `cdk deploy SqlRag-Data` and `cdk deploy SqlRag-Ecs` "
        "(see infra/README.md).",
        file=sys.stderr,
    )

ecr_stack = EcrStack(app, "SqlRag-Ecr", env=env)

network_stack = NetworkStack(app, "SqlRag-Network", allowed_cidr=allowed_cidr, env=env)

data_stack = DataStack(
    app,
    "SqlRag-Data",
    vpc=network_stack.vpc,
    sg_efs=network_stack.sg_efs,
    env=env,
)
data_stack.add_dependency(network_stack)

ecs_stack = EcsStack(
    app,
    "SqlRag-Ecs",
    vpc=network_stack.vpc,
    sg_streamlit=network_stack.sg_streamlit,
    sg_query_service=network_stack.sg_query_service,
    sg_retriever=network_stack.sg_retriever,
    sg_vector_builder=network_stack.sg_vector_builder,
    namespace=network_stack.namespace,
    repository_name="sql-rag",
    image_tag=image_tag,
    huggingface_secret=data_stack.huggingface_secret,
    openrouter_secret=data_stack.openrouter_secret,
    athena_secret=data_stack.athena_secret,
    session_table=data_stack.session_table,
    file_system=data_stack.file_system,
    access_point=data_stack.access_point,
    athena_output_s3=athena_output_s3,
    athena_database=athena_database,
    manifest_path=manifest_path,
    env=env,
)
ecs_stack.add_dependency(data_stack)

app.synth()
