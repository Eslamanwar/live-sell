"""Workflow for displaying the active product and waiting for viewer interactions."""
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


class DisplayingProductWorkflow(StateWorkflow):
    """
    Workflow for the DISPLAYING_PRODUCT state.

    The product card is shown to viewers. The workflow waits for:
    - Viewer chat messages (→ HANDLING_CHAT)
    - Buy button clicks (→ PROCESSING_PURCHASE)
    - New product detection from stream (→ DETECTING_PRODUCT)
    - Stream end signal (→ COMPLETED)
    """

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[LiveShopData] = None,
    ) -> str:
        """
        Display product and wait for viewer interaction.

        Args:
            state_machine: The state machine instance
            state_machine_data: Current state data

        Returns:
            Next state to transition to
        """
        if state_machine_data is None:
            return LiveShopState.FAILED

        product = state_machine_data.active_product
        if not product:
            logger.warning("No active product to display")
            return LiveShopState.INGESTING_STREAM

        logger.info(f"Displaying product: {product.name} (SKU: {product.sku})")

        # Push product card data via WebSocket to all connected viewers
        try:
            await workflow.execute_activity(
                "push_product_card",
                args=[
                    state_machine_data.session_id,
                    {
                        "sku": product.sku,
                        "name": product.name,
                        "description": product.description,
                        "price": product.base_price,
                        "colors": product.colors,
                        "sizes": product.sizes,
                        "stock": product.stock,
                        "images": product.images,
                    },
                ],
                start_to_close_timeout=workflow.timedelta(seconds=10),
            )
        except Exception as e:
            logger.warning(f"Failed to push product card via WebSocket: {e}")

        # Mark that we're waiting for user input (chat or buy)
        state_machine_data.waiting_for_user_input = True

        # Wait for a viewer interaction or stream event
        await workflow.wait_condition(
            lambda: not state_machine_data.waiting_for_user_input
                    or not state_machine_data.stream_active
        )

        # Check what triggered the wake-up
        if not state_machine_data.stream_active:
            logger.info("Stream ended while displaying product")
            return LiveShopState.COMPLETED

        # A viewer message was received — handle it
        if state_machine_data.current_query:
            query_lower = state_machine_data.current_query.lower()

            # Check if it's a purchase intent
            if any(word in query_lower for word in ["buy", "purchase", "add to cart", "checkout", "order"]):
                return LiveShopState.PROCESSING_PURCHASE
            else:
                return LiveShopState.HANDLING_CHAT

        # Default: go back to ingesting stream for new product detection
        return LiveShopState.INGESTING_STREAM