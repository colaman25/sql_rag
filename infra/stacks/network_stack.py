from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_servicediscovery as servicediscovery
from constructs import Construct


class NetworkStack(Stack):
    """VPC and security groups for the sql-rag app.

    Traffic shape mirrors docker-compose's service dependency chain:
      internet -> streamlit-app (8501) -> query-service (8003) -> retriever (8000) -> EFS (2049)

    No NAT gateway: every task runs in the public subnet with a public IP and
    reaches the internet directly via the Internet Gateway (needed for
    HuggingFace/OpenRouter/Athena calls) instead of paying NAT's hourly +
    per-GB data processing charges. This is safe because the security groups
    below -- not subnet placement -- are what actually restrict access:
    retriever/query-service/vector-builder only accept inbound traffic from
    specific sibling security groups, never from the internet, regardless of
    having a public IP.
    """

    def __init__(self, scope: Construct, construct_id: str, *, allowed_cidr: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            vpc_name="sql-rag-vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
            ],
        )

        self.sg_streamlit = ec2.SecurityGroup(
            self,
            "StreamlitSg",
            vpc=self.vpc,
            description="sql-rag streamlit-app: public HTTP on 8501",
            allow_all_outbound=True,
        )
        self.sg_streamlit.add_ingress_rule(
            ec2.Peer.ipv4(allowed_cidr), ec2.Port.tcp(8501), "Streamlit UI"
        )

        self.sg_query_service = ec2.SecurityGroup(
            self,
            "QueryServiceSg",
            vpc=self.vpc,
            description="sql-rag query-service: internal API on 8003",
            allow_all_outbound=True,
        )

        self.sg_retriever = ec2.SecurityGroup(
            self,
            "RetrieverSg",
            vpc=self.vpc,
            description="sql-rag retriever: internal API on 8000",
            allow_all_outbound=True,
        )

        self.sg_vector_builder = ec2.SecurityGroup(
            self,
            "VectorBuilderSg",
            vpc=self.vpc,
            description="sql-rag vector-builder: one-off batch task",
            allow_all_outbound=True,
        )

        self.sg_efs = ec2.SecurityGroup(
            self,
            "EfsSg",
            vpc=self.vpc,
            description="sql-rag EFS: shared ChromaDB vectorstore",
            allow_all_outbound=False,
        )

        self.sg_query_service.add_ingress_rule(
            self.sg_streamlit, ec2.Port.tcp(8003), "streamlit-app to query-service"
        )
        self.sg_retriever.add_ingress_rule(
            self.sg_query_service, ec2.Port.tcp(8000), "query-service to retriever"
        )
        self.sg_efs.add_ingress_rule(self.sg_retriever, ec2.Port.tcp(2049), "retriever to EFS")
        self.sg_efs.add_ingress_rule(self.sg_vector_builder, ec2.Port.tcp(2049), "vector-builder to EFS")

        # Created here (deployed well before SqlRag-Ecs, in its own `cdk deploy`
        # step) rather than inline in the ECS stack. Cloud Map/Route53 need real
        # wall-clock time to finish propagating a new PrivateDnsNamespace after
        # CloudFormation reports it CREATE_COMPLETE -- creating it in the same
        # deployment as the first Service Connect service that uses it races
        # that propagation and intermittently fails.
        self.namespace = servicediscovery.PrivateDnsNamespace(
            self,
            "ServiceConnectNamespace",
            name="sqlrag.internal",
            vpc=self.vpc,
            description="Service Connect namespace for sql-rag ECS services",
        )
