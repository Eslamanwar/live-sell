"""Terminal state workflows for FAILED and COMPLETED states."""
from typing import Optional, override

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


class FailedWorkflow(StateWorkflow):
    """Workflow for the FAILED terminal state."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[LiveShopData] = None,
    ) -> str:
        """
        Handle failed state — this is a terminal state.

        Args:
            state_machine: The state machine instance
            state_machine_data: Current state data

        Returns:
            Stays in FAILED state (terminal)
        """
        if state_machine_data:
            logger.error(f"LiveShop workflow failed: {state_machine_data.error_message}")

            if state_machine_data.task_id:
                try:
                    await adk.messages.create(
                        task_id=state_machine_data.task_id,
                        content=TextContent(
                            author="agent",
                            content=(
                                f"❌ The live shop session encountered an error: "
                                f"{state_machine_data.error_message}\n\n"
                                f"Please try restarting the stream."
                            ),
                        ),
                        trace_id=state_machine_data.task_id,
                    )
                except Exception as e:
                    logger.error(f"Failed to send error message: {e}")

        return LiveShopState.FAILED


class CompletedWorkflow(StateWorkflow):
    """Workflow for the COMPLETED terminal state."""

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[LiveShopData] = None,
    ) -> str:
        """
        Handle completed state — this is a terminal state.

        Args:
            state_machine: The state machine instance
            state_machine_data: Current state data

        Returns:
            Stays in COMPLETED state (terminal)
        """
        if state_machine_data and state_machine_data.task_id:
            # Send session summary
            summary = (
                f"📊 **Live Shop Session Summary**\n\n"
                f"Products showcased: {state_machine_data.products_detected_count}\n"
                f"Questions answered: {state_machine_data.total_questions_answered}\n"
                f"Items reserved: {state_machine_data.total_items_reserved}\n"
                f"Items sold: {state_machine_data.total_items_sold}\n\n"
                f"Thanks for watching! 🎉"
            )

            try:
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=summary,
                    ),
                    trace_id=state_machine_data.task_id,
                )
            except Exception as e:
                logger.error(f"Failed to send completion summary: {e}")

        logger.info("LiveShop workflow completed successfully")
        return LiveShopState.COMPLETED