"""Workflow for processing a viewer's purchase request."""
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
    OrderData,
)

logger = make_logger(__name__)


class ProcessingPurchaseWorkflow(StateWorkflow):
    """
    Workflow for the PROCESSING_PURCHASE state.

    Handles the purchase flow:
    1. Determine which variant (color + size) the viewer wants
    2. Check stock availability
    3. Reserve the item (10-minute hold)
    4. Return checkout URL to the viewer
    """

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[LiveShopData] = None,
    ) -> str:
        """
        Process a purchase request from a viewer.

        Args:
            state_machine: The state machine instance
            state_machine_data: Current state data

        Returns:
            Next state to transition to (DISPLAYING_PRODUCT)
        """
        if state_machine_data is None:
            return LiveShopState.FAILED

        product = state_machine_data.active_product
        if not product:
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content="No product is currently being showcased. Please wait for the host to show a product!",
                ),
                trace_id=state_machine_data.task_id,
            )
            state_machine_data.waiting_for_user_input = True
            return LiveShopState.DISPLAYING_PRODUCT

        viewer_message = state_machine_data.current_query
        logger.info(f"Processing purchase for {product.name}: {viewer_message}")

        try:
            # First, check stock for the requested variant
            stock_result = await workflow.execute_activity(
                "check_stock",
                args=[
                    state_machine_data.task_id,
                    product.sku,
                    None,  # color — will be parsed from message
                    None,  # size — will be parsed from message
                    viewer_message,  # raw message for intent parsing
                ],
                start_to_close_timeout=workflow.timedelta(seconds=15),
            )

            color = stock_result.get("color", product.colors[0] if product.colors else "")
            size = stock_result.get("size", product.sizes[0] if product.sizes else "")
            available = stock_result.get("available", False)
            quantity = stock_result.get("quantity", 0)

            if not available or quantity <= 0:
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"😔 Sorry, **{product.name}** in {color} / {size} is currently out of stock.\n"
                            f"Available variants: {', '.join(k for k, v in product.stock.items() if v > 0)}"
                        ),
                    ),
                    trace_id=state_machine_data.task_id,
                )
                state_machine_data.waiting_for_user_input = True
                return LiveShopState.DISPLAYING_PRODUCT

            # Reserve the item
            reserve_result = await workflow.execute_activity(
                "reserve_item",
                args=[
                    state_machine_data.task_id,
                    product.sku,
                    color,
                    size,
                    state_machine_data.task_id,  # viewer_id (using task_id as proxy)
                    state_machine_data.session_id,
                ],
                start_to_close_timeout=workflow.timedelta(seconds=15),
            )

            order_id = reserve_result.get("order_id", "")
            checkout_url = reserve_result.get("checkout_url", "")
            expires_at = reserve_result.get("expires_at", "")

            if order_id:
                # Track the order
                order = OrderData(
                    order_id=order_id,
                    session_id=state_machine_data.session_id,
                    viewer_id=state_machine_data.task_id,
                    sku=product.sku,
                    color=color,
                    size=size,
                    status="RESERVED",
                    expires_at=expires_at,
                    checkout_url=checkout_url,
                )
                state_machine_data.active_orders.append(order)
                state_machine_data.total_items_reserved += 1

                # Send checkout link to viewer
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"✅ **Reserved!** {product.name} ({color}, {size})\n"
                            f"💰 AED {product.base_price}\n"
                            f"⏰ Held for 10 minutes\n"
                            f"🔗 [Complete your purchase]({checkout_url})\n\n"
                            f"Order ID: {order_id}"
                        ),
                    ),
                    trace_id=state_machine_data.task_id,
                    parent_span_id=(
                        state_machine_data.current_span.id
                        if state_machine_data.current_span
                        else None
                    ),
                )

                logger.info(f"Item reserved: {order_id} for {product.name} ({color}/{size})")
            else:
                await adk.messages.create(
                    task_id=state_machine_data.task_id,
                    content=TextContent(
                        author="agent",
                        content="❌ Sorry, we couldn't reserve that item. Please try again!",
                    ),
                    trace_id=state_machine_data.task_id,
                )

            state_machine_data.current_query = ""
            state_machine_data.waiting_for_user_input = True

            # Return to product display
            return LiveShopState.DISPLAYING_PRODUCT

        except Exception as e:
            logger.error(f"Error processing purchase: {str(e)}")
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content="❌ Something went wrong with your purchase. Please try again!",
                ),
                trace_id=state_machine_data.task_id,
            )
            state_machine_data.waiting_for_user_input = True
            return LiveShopState.DISPLAYING_PRODUCT