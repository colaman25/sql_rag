from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from constructs import Construct


class EcrStack(Stack):
    """Creates the `sql-rag` ECR repository.

    If a repository named `sql-rag` already exists in this account/region
    (created outside of CDK), this stack's first deploy will fail with
    "repository already exists". In that case, adopt the existing repository
    into CDK instead of deploying normally:

        cdk import SqlRag-Ecr

    and answer the prompts with the existing repository's name. After that,
    `cdk deploy SqlRag-Ecr` will manage it going forward.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.repository = ecr.Repository(
            self,
            "SqlRagRepository",
            repository_name="sql-rag",
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Expire untagged images after 14 days",
                    tag_status=ecr.TagStatus.UNTAGGED,
                    max_image_age=Duration.days(14),
                ),
            ],
        )
