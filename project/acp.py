"""ACP server configuration for the LiveShop Agent."""
import os

from agentex.lib.sdk.fastacp.fastacp import FastACP
from agentex.lib.types.fastacp import TemporalACPConfig

# Create the ACP server
acp = FastACP.create(
    acp_type="async",
    config=TemporalACPConfig(
        type="temporal",
        temporal_address=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
    ),
)

# Temporal-based ACP automatically registers handlers:
# @acp.on_task_create → handled by workflow's @workflow.run method
# @acp.on_task_event_send → handled by workflow's @workflow.signal(name=SignalName.RECEIVE_MESSAGE)
# @acp.on_task_cancel → automatically handled by temporal client