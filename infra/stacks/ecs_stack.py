from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_efs as efs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_servicediscovery as servicediscovery
from constructs import Construct


class EcsStack(Stack):
    """ECS cluster, task definitions, and services for the sql-rag app.

    One Docker image (built from the repo's Dockerfile) is reused across all
    four task definitions, differentiated only by the container command --
    mirroring how docker-compose.yml runs the same image as four services.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.Vpc,
        sg_streamlit: ec2.SecurityGroup,
        sg_query_service: ec2.SecurityGroup,
        sg_retriever: ec2.SecurityGroup,
        sg_vector_builder: ec2.SecurityGroup,
        namespace: servicediscovery.PrivateDnsNamespace,
        repository_name: str,
        image_tag: str,
        huggingface_secret: secretsmanager.Secret,
        openrouter_secret: secretsmanager.Secret,
        athena_secret: secretsmanager.Secret,
        session_table: dynamodb.Table,
        file_system: efs.FileSystem,
        access_point: efs.AccessPoint,
        athena_output_s3: str,
        athena_database: str,
        manifest_path: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        repository = ecr.Repository.from_repository_name(self, "SqlRagRepo", repository_name)
        image = ecs.ContainerImage.from_ecr_repository(repository, image_tag)

        cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name="sql-rag-cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        # --- IAM ---------------------------------------------------------
        # Execution role: used by the ECS agent to pull the image and inject secrets.
        execution_role = iam.Role(
            self,
            "ExecutionRole",
            role_name="sql-rag-task-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        for secret in (huggingface_secret, openrouter_secret, athena_secret):
            secret.grant_read(execution_role)

        # Task roles: used by application code at runtime via the AWS SDK.
        retriever_task_role = iam.Role(
            self,
            "RetrieverTaskRole",
            role_name="sql-rag-retriever-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        query_task_role = iam.Role(
            self,
            "QueryServiceTaskRole",
            role_name="sql-rag-query-service-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        streamlit_task_role = iam.Role(
            self,
            "StreamlitTaskRole",
            role_name="sql-rag-streamlit-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        vector_builder_task_role = iam.Role(
            self,
            "VectorBuilderTaskRole",
            role_name="sql-rag-vector-builder-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # query-service uses the default boto3 session (no static creds) for
        # DynamoDB, so it relies on this task role instead.
        session_table.grant_read_write_data(query_task_role)

        # Allow `aws ecs execute-command` debug shells into any container.
        exec_policy = iam.PolicyStatement(
            actions=[
                "ssmmessages:CreateControlChannel",
                "ssmmessages:CreateDataChannel",
                "ssmmessages:OpenControlChannel",
                "ssmmessages:OpenDataChannel",
            ],
            resources=["*"],
        )
        for role in (retriever_task_role, query_task_role, streamlit_task_role, vector_builder_task_role):
            role.add_to_policy(exec_policy)

        # --- Shared helpers ------------------------------------------------
        def vectorstore_volume() -> ecs.Volume:
            return ecs.Volume(
                name="vectorstore",
                efs_volume_configuration=ecs.EfsVolumeConfiguration(
                    file_system_id=file_system.file_system_id,
                    transit_encryption="ENABLED",
                    authorization_config=ecs.AuthorizationConfig(
                        access_point_id=access_point.access_point_id
                    ),
                ),
            )

        def log_driver(name: str) -> ecs.LogDriver:
            group = logs.LogGroup(
                self,
                f"{name.title().replace('-', '')}LogGroup",
                log_group_name=f"/ecs/sql-rag/{name}",
                retention=logs.RetentionDays.TWO_WEEKS,
                removal_policy=RemovalPolicy.DESTROY,
            )
            return ecs.LogDriver.aws_logs(stream_prefix=name, log_group=group)

        circuit_breaker = ecs.DeploymentCircuitBreaker(rollback=True)

        # --- Retriever service (port 8000) ---------------------------------
        retriever_task = ecs.FargateTaskDefinition(
            self,
            "RetrieverTaskDef",
            family="sql-rag-retriever",
            cpu=2048,
            memory_limit_mib=4096,
            execution_role=execution_role,
            task_role=retriever_task_role,
            volumes=[vectorstore_volume()],
        )
        retriever_container = retriever_task.add_container(
            "retriever",
            image=image,
            command=["uvicorn", "retriever_api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"],
            logging=log_driver("retriever"),
            secrets={
                "HF_TOKEN": ecs.Secret.from_secrets_manager(huggingface_secret, "HF_TOKEN"),
                "HUGGINGFACEHUB_API_TOKEN": ecs.Secret.from_secrets_manager(huggingface_secret, "HF_TOKEN"),
                "OPENROUTER_API_KEY": ecs.Secret.from_secrets_manager(openrouter_secret, "OPENROUTER_API_KEY"),
            },
            # No container health check: retriever_api.py's own /health endpoint
            # deliberately returns 503 until the vectorstore has data (see
            # retriever_api.py), which is *always* true on a fresh deploy since
            # the one-off vector-builder task hasn't run yet. An ECS-level
            # health check here would never pass on first deploy, and with the
            # deployment circuit breaker enabled below, that permanently fails
            # every fresh `cdk deploy` of this stack. Service Connect's Envoy
            # sidecar already retries connections regardless of container
            # health, so this isn't needed for routing -- only /health as an
            # app-level diagnostic endpoint is lost, which is fine.
        )
        retriever_container.add_port_mappings(ecs.PortMapping(container_port=8000, name="retriever"))
        retriever_container.add_mount_points(
            ecs.MountPoint(container_path="/app/vectorstore", source_volume="vectorstore", read_only=False)
        )

        retriever_service = ecs.FargateService(
            self,
            "RetrieverService",
            service_name="sql-rag-retriever",
            cluster=cluster,
            task_definition=retriever_task,
            desired_count=1,
            min_healthy_percent=100,
            max_healthy_percent=200,
            security_groups=[sg_retriever],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            assign_public_ip=True,
            platform_version=ecs.FargatePlatformVersion.LATEST,
            circuit_breaker=circuit_breaker,
            enable_execute_command=True,
            service_connect_configuration=ecs.ServiceConnectProps(
                namespace=namespace.namespace_arn,
                services=[
                    ecs.ServiceConnectService(port_mapping_name="retriever", dns_name="retriever", port=8000)
                ],
            ),
        )

        # --- Query service (port 8003) --------------------------------------
        query_task = ecs.FargateTaskDefinition(
            self,
            "QueryServiceTaskDef",
            family="sql-rag-query-service",
            cpu=512,
            memory_limit_mib=1024,
            execution_role=execution_role,
            task_role=query_task_role,
        )
        query_container = query_task.add_container(
            "query-service",
            image=image,
            command=["uvicorn", "query_api:app", "--host", "0.0.0.0", "--port", "8003"],
            logging=log_driver("query-service"),
            environment={
                "AWS_REGION": self.region,
                "ATHENA_OUTPUT_S3": athena_output_s3,
                "ATHENA_DATABASE": athena_database,
                "RETRIEVER_URL": "http://retriever:8000/retrieve",
                "SESSION_STORE": "dynamodb",
                "DYNAMODB_SESSION_TABLE": session_table.table_name,
            },
            secrets={
                "OPENROUTER_API_KEY": ecs.Secret.from_secrets_manager(openrouter_secret, "OPENROUTER_API_KEY"),
                "ATHENA_AWS_ACCESS_KEY_ID": ecs.Secret.from_secrets_manager(
                    athena_secret, "ATHENA_AWS_ACCESS_KEY_ID"
                ),
                "ATHENA_AWS_SECRET_ACCESS_KEY": ecs.Secret.from_secrets_manager(
                    athena_secret, "ATHENA_AWS_SECRET_ACCESS_KEY"
                ),
            },
        )
        query_container.add_port_mappings(ecs.PortMapping(container_port=8003, name="query-service"))

        query_service = ecs.FargateService(
            self,
            "QueryService",
            service_name="sql-rag-query-service",
            cluster=cluster,
            task_definition=query_task,
            desired_count=1,
            min_healthy_percent=100,
            max_healthy_percent=200,
            security_groups=[sg_query_service],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            assign_public_ip=True,
            platform_version=ecs.FargatePlatformVersion.LATEST,
            circuit_breaker=circuit_breaker,
            enable_execute_command=True,
            service_connect_configuration=ecs.ServiceConnectProps(
                namespace=namespace.namespace_arn,
                services=[
                    ecs.ServiceConnectService(
                        port_mapping_name="query-service", dns_name="query-service", port=8003
                    )
                ],
            ),
        )
        query_service.node.add_dependency(retriever_service)
        
        # --- Streamlit frontend (port 8501, public IP) ----------------------
        streamlit_task = ecs.FargateTaskDefinition(
            self,
            "StreamlitTaskDef",
            family="sql-rag-streamlit",
            cpu=512,
            memory_limit_mib=1024,
            execution_role=execution_role,
            task_role=streamlit_task_role,
        )
        streamlit_container = streamlit_task.add_container(
            "streamlit-app",
            image=image,
            command=["streamlit", "run", "frontend.py", "--server.port=8501", "--server.address=0.0.0.0"],
            logging=log_driver("streamlit"),
            environment={"QUERY_SERVICE_URL": "http://query-service:8003"},
        )
        streamlit_container.add_port_mappings(ecs.PortMapping(container_port=8501, name="streamlit"))

        streamlit_service = ecs.FargateService(
            self,
            "StreamlitService",
            service_name="sql-rag-streamlit",
            cluster=cluster,
            task_definition=streamlit_task,
            desired_count=1,
            min_healthy_percent=100,
            max_healthy_percent=200,
            security_groups=[sg_streamlit],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            assign_public_ip=True,
            platform_version=ecs.FargatePlatformVersion.LATEST,
            circuit_breaker=circuit_breaker,
            enable_execute_command=True,
            # Client-only Service Connect: no `services=[]`, just joins the mesh
            # so it can resolve "query-service" by DNS name.
            service_connect_configuration=ecs.ServiceConnectProps(namespace=namespace.namespace_arn),
        )
        streamlit_service.node.add_dependency(query_service)
        
        # --- Vector builder (one-off batch task, not a service) -------------
        vector_builder_task = ecs.FargateTaskDefinition(
            self,
            "VectorBuilderTaskDef",
            family="sql-rag-vector-builder",
            cpu=2048,
            memory_limit_mib=4096,
            execution_role=execution_role,
            task_role=vector_builder_task_role,
            volumes=[vectorstore_volume()],
        )
        vector_builder_container = vector_builder_task.add_container(
            "vector-builder",
            image=image,
            command=["python", "generate_database_knowledge.py"],
            logging=log_driver("vector-builder"),
            environment={
                "AWS_REGION": self.region,
                "ATHENA_OUTPUT_S3": athena_output_s3,
                "ATHENA_DATABASE": athena_database,
                "MANIFEST_PATH": manifest_path,
            },
            secrets={
                "HF_TOKEN": ecs.Secret.from_secrets_manager(huggingface_secret, "HF_TOKEN"),
                "HUGGINGFACEHUB_API_TOKEN": ecs.Secret.from_secrets_manager(huggingface_secret, "HF_TOKEN"),
                "ATHENA_AWS_ACCESS_KEY_ID": ecs.Secret.from_secrets_manager(
                    athena_secret, "ATHENA_AWS_ACCESS_KEY_ID"
                ),
                "ATHENA_AWS_SECRET_ACCESS_KEY": ecs.Secret.from_secrets_manager(
                    athena_secret, "ATHENA_AWS_SECRET_ACCESS_KEY"
                ),
            },
        )
        vector_builder_container.add_mount_points(
            ecs.MountPoint(container_path="/app/vectorstore", source_volume="vectorstore", read_only=False)
        )

        # --- Outputs, consumed by the helper scripts ------------------------
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "VectorBuilderTaskFamily", value=vector_builder_task.family)
        CfnOutput(
            self,
            "PublicSubnetIds",
            value=",".join(s.subnet_id for s in vpc.select_subnets(
                subnet_type=ec2.SubnetType.PUBLIC
            ).subnets),
        )
        CfnOutput(self, "VectorBuilderSecurityGroupId", value=sg_vector_builder.security_group_id)
