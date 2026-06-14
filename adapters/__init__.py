import os

from adapters.base import DatabaseAdapter
from adapters.athena import AthenaAdapter


def get_adapter(config: dict) -> DatabaseAdapter:
    platform_cfg = config.get("platform", {})
    ptype = platform_cfg.get("type", "athena")

    if ptype == "athena":
        return AthenaAdapter(
            database=platform_cfg.get("database") or os.getenv("ATHENA_DATABASE"),
            output_s3=platform_cfg.get("output_s3") or os.getenv("ATHENA_OUTPUT_S3"),
            region=platform_cfg.get("region") or os.getenv("AWS_REGION", "eu-west-2"),
        )

    raise ValueError(f"Unsupported platform: {ptype!r}. Add an adapter in adapters/.")
