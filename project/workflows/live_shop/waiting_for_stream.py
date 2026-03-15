"""Workflow for waiting for the host to start a live stream."""
from typing import Optional, override

from temporalio import workflow

from agentex.lib import adk
from agentex.lib.sdk.state_machine.state_machine import StateMachine
from agentex.lib.sdk.state_machine.state_workflow import StateWorkflow
from agentex.lib.utils.logging import make_logger
from agentex.types.text_content import TextContent
from project.state_machines.live_shop import (
    LiveShopData,
    LiveShopState,
)

logger = make_logger(__name__)


class WaitingForStreamWorkflow(StateWorkflow):
    """
    Workflow for the WAITING_FOR_STREAM state.

    Waits for the host to initiate a live stream session.
    Once a stream starts (signaled via user message or stream event),
    transitions to INGESTING_STREAM.
    """

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[LiveShopData] = None,
    ) -> str:
        """
        Wait for stream to begin.

        Args:
            state_machine: The state machine instance
            state_machine_data: Current state data

        Returns:
            Next state to transition to
        """
        if state_machine_data is None:
            return LiveShopState.FAILED

        logger.info("Waiting for host to start live stream...")

        # Wait for the stream to be activated (via signal from host)
        await workflow.wait_condition(
            lambda: not state_machine_data.waiting_for_stream
        )

        logger.info(
            f"Stream started for session: {state_machine_data.session_id}"
        )

        # Transition to ingesting the stream
        return LiveShopState.INGESTING_STREAM