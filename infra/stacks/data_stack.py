from aws_cdk import RemovalPolicy, SecretValue, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_efs as efs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class DataStack(Stack):
    """Secrets Manager secrets, the DynamoDB session table, and the EFS
    filesystem used to share the ChromaDB vectorstore between the
    vector-builder batch task and the retriever service.

    Secrets are created with a placeholder value only — CDK code must never
    contain real credentials. Populate the real values after deploy with
    scripts/push_secrets.ps1 (or .sh), which reads your local .env file and
    calls `aws secretsmanager put-secret-value`.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.Vpc,
        sg_efs: ec2.SecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        placeholder = SecretValue.unsafe_plain_text("REPLACE_ME_VIA_push_secrets_script")

        self.huggingface_secret = secretsmanager.Secret(
            self,
            "HuggingFaceSecret",
            secret_name="sql-rag/huggingface",
            description="HuggingFace Hub token used for embedding model access",
            secret_object_value={"HF_TOKEN": placeholder},
        )

        self.openrouter_secret = secretsmanager.Secret(
            self,
            "OpenRouterSecret",
            secret_name="sql-rag/openrouter",
            description="OpenRouter API key used for LLM access",
            secret_object_value={"OPENROUTER_API_KEY": placeholder},
        )

        self.athena_secret = secretsmanager.Secret(
            self,
            "AthenaSecret",
            secret_name="sql-rag/athena-credentials",
            description=(
                "Cross-account AWS credentials used by the Athena adapter to run "
                "queries and to read the dbt manifest.json from S3"
            ),
            secret_object_value={
                "ATHENA_AWS_ACCESS_KEY_ID": placeholder,
                "ATHENA_AWS_SECRET_ACCESS_KEY": placeholder,
            },
        )

        # Session history cache — ephemeral/regenerable, safe to destroy with the stack.
        self.session_table = dynamodb.Table(
            self,
            "SessionTable",
            table_name="rag-sessions",
            partition_key=dynamodb.Attribute(name="session_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Vectorstore data is expensive to rebuild (requires re-running the indexer
        # against the full dbt manifest), so retain it independently of the stack.
        self.file_system = efs.FileSystem(
            self,
            "VectorstoreFs",
            vpc=vpc,
            security_group=sg_efs,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            encrypted=True,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.BURSTING,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.access_point = self.file_system.add_access_point(
            "VectorstoreAccessPoint",
            path="/vectorstore",
            create_acl=efs.Acl(owner_uid="0", owner_gid="0", permissions="755"),
            posix_user=efs.PosixUser(uid="0", gid="0"),
        )
