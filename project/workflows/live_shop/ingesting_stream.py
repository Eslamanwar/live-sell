"""Workflow for ingesting the live video stream and detecting products."""
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


class IngestingStreamWorkflow(StateWorkflow):
    """
    Workflow for the INGESTING_STREAM state.

    Continuously processes the video stream via Gemini Live API,
    detecting products as the host showcases them. When a product
    is detected, transitions to DETECTING_PRODUCT.
    """

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[LiveShopData] = None,
    ) -> str:
        """
        Ingest the live stream and detect products.

        Args:
            state_machine: The state machine instance
            state_machine_data: Current state data

        Returns:
            Next state to transition to
        """
        if state_machine_data is None:
            return LiveShopState.FAILED

        logger.info("Starting stream ingestion via Gemini Live API...")

        try:
            # Execute the stream ingestion activity which connects to Gemini Live API
            # and analyzes video frames for product detection
            result = await workflow.execute_activity(
                "ingest_and_detect_product",
                args=[
                    state_machine_data.task_id,
                    state_machine_data.session_id,
                    state_machine_data.stream_url,
                ],
                start_to_close_timeout=workflow.timedelta(seconds=60),
            )

            visual_description = result.get("visual_description", "")
            confidence = result.get("confidence", 0.0)

            if visual_description and visual_description != "NO_PRODUCT_DETECTED":
                # Product detected in the stream
                state_machine_data.visual_description = visual_description
                state_machine_data.product_detection_confidence = confidence
                state_machine_data.products_detected_count += 1

                logger.info(
                    f"Product detected: {visual_description[:80]}... "
                    f"(confidence: {confidence:.2f})"
                )

                # Transition to querying inventory for the detected product
                return LiveShopState.QUERYING_INVENTORY
            else:
                # No product detected, check if we should keep watching or handle chat
                if not state_machine_data.waiting_for_user_input:
                    # A viewer sent a message while we were scanning
                    return LiveShopState.HANDLING_CHAT

                # Continue ingesting — stay in this state
                # (The state machine will re-execute this state)
                return LiveShopState.INGESTING_STREAM

        except Exception as e:
            logger.error(f"Error during stream ingestion: {str(e)}")
            state_machine_data.error_message = f"Stream ingestion failed: {str(e)}"
            return LiveShopState.FAILED