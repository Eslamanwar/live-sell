"""Workflow for querying inventory based on detected product."""
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
    ProductData,
)

logger = make_logger(__name__)


class QueryingInventoryWorkflow(StateWorkflow):
    """
    Workflow for the QUERYING_INVENTORY state.

    Takes the visual description from product detection and queries
    Firestore inventory to find the matching product with full details.
    """

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[LiveShopData] = None,
    ) -> str:
        """
        Query inventory for the detected product.

        Args:
            state_machine: The state machine instance
            state_machine_data: Current state data

        Returns:
            Next state to transition to (DISPLAYING_PRODUCT)
        """
        if state_machine_data is None:
            return LiveShopState.FAILED

        logger.info(
            f"Querying inventory for: {state_machine_data.visual_description[:80]}..."
        )

        try:
            # Execute search_inventory activity
            result = await workflow.execute_activity(
                "search_inventory",
                args=[
                    state_machine_data.task_id,
                    state_machine_data.visual_description,
                ],
                start_to_close_timeout=workflow.timedelta(seconds=30),
            )

            if result.get("found", False):
                # Build product data from inventory result
                product = ProductData(
                    sku=result.get("sku", ""),
                    name=result.get("name", ""),
                    description=result.get("description", ""),
                    base_price=result.get("base_price", 0.0),
                    tags=result.get("tags", []),
                    colors=result.get("colors", []),
                    sizes=result.get("sizes", []),
                    stock=result.get("stock", {}),
                    images=result.get("images", []),
                )
                state_machine_data.active_product = product

                logger.info(
                    f"Product found: {product.name} (SKU: {product.sku}) "
                    f"- AED {product.base_price}"
                )

                # Notify the UI about the new product
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"🛍️ **{product.name}** — AED {product.base_price}\n"
                            f"Colors: {', '.join(product.colors)}\n"
                            f"Sizes: {', '.join(product.sizes)}\n"
                            f"{'🟢 In stock' if any(v > 0 for v in product.stock.values()) else '🔴 Out of stock'}"
                        ),
                    ),
                    trace_id=state_machine_data.task_id,
                    parent_span_id=(
                        state_machine_data.current_span.id
                        if state_machine_data.current_span
                        else None
                    ),
                )

                return LiveShopState.DISPLAYING_PRODUCT
            else:
                logger.warning(
                    f"No matching product found for: {state_machine_data.visual_description[:80]}"
                )

                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content="🔍 I detected a product but couldn't find an exact match in our inventory. The host may need to manually select the product.",
                    ),
                    trace_id=state_machine_data.task_id,
                )

                # Go back to ingesting stream
                return LiveShopState.INGESTING_STREAM

        except Exception as e:
            logger.error(f"Error querying inventory: {str(e)}")
            state_machine_data.error_message = f"Inventory query failed: {str(e)}"
            return LiveShopState.FAILED