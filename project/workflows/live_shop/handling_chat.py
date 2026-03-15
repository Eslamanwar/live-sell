"""Workflow for handling viewer chat questions."""
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


class HandlingChatWorkflow(StateWorkflow):
    """
    Workflow for the HANDLING_CHAT state.

    Processes viewer questions about the current product using LLM
    with access to inventory tools (check_stock, product details).
    """

    @override
    async def execute(
        self,
        state_machine: StateMachine,
        state_machine_data: Optional[LiveShopData] = None,
    ) -> str:
        """
        Handle a viewer's chat question.

        Args:
            state_machine: The state machine instance
            state_machine_data: Current state data

        Returns:
            Next state to transition to (DISPLAYING_PRODUCT)
        """
        if state_machine_data is None:
            return LiveShopState.FAILED

        viewer_question = state_machine_data.current_query
        product = state_machine_data.active_product

        logger.info(f"Handling viewer question: {viewer_question[:100]}...")

        try:
            # Build product context for the LLM
            product_info = "No product currently displayed."
            if product:
                # Build stock summary
                stock_summary = []
                for variant_key, qty in product.stock.items():
                    if qty > 0:
                        stock_summary.append(f"{variant_key}: {qty} left")

                product_info = (
                    f"Product: {product.name}\n"
                    f"SKU: {product.sku}\n"
                    f"Price: AED {product.base_price}\n"
                    f"Description: {product.description}\n"
                    f"Colors: {', '.join(product.colors)}\n"
                    f"Sizes: {', '.join(product.sizes)}\n"
                    f"Stock: {'; '.join(stock_summary) if stock_summary else 'Check availability'}\n"
                    f"Tags: {', '.join(product.tags)}"
                )

            # Build conversation for the LLM
            conversation = state_machine_data.conversation_history.copy()
            conversation.append({
                "role": "user",
                "content": viewer_question,
            })

            # Execute the answer_question activity
            result = await workflow.execute_activity(
                "answer_question",
                args=[
                    state_machine_data.task_id,
                    state_machine_data.task_id,  # trace_id
                    viewer_question,
                    product_info,
                    conversation,
                    state_machine_data.current_span.id if state_machine_data.current_span else None,
                ],
                start_to_close_timeout=workflow.timedelta(seconds=60),
            )

            answer = result.get("answer", "I'm not sure about that. Let me check with the host!")

            # Send the answer to the chat
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content=answer,
                ),
                trace_id=state_machine_data.task_id,
                parent_span_id=(
                    state_machine_data.current_span.id
                    if state_machine_data.current_span
                    else None
                ),
            )

            # Update conversation history
            state_machine_data.conversation_history.append({
                "role": "user",
                "content": viewer_question,
            })
            state_machine_data.conversation_history.append({
                "role": "assistant",
                "content": answer,
            })

            state_machine_data.total_questions_answered += 1
            state_machine_data.current_query = ""
            state_machine_data.waiting_for_user_input = True

            logger.info("Chat question answered, returning to product display")

            # Go back to displaying the product (waiting for next interaction)
            return LiveShopState.DISPLAYING_PRODUCT

        except Exception as e:
            logger.error(f"Error handling chat: {str(e)}")
            # Don't fail the whole workflow for a chat error
            await adk.messages.create(
                task_id=state_machine_data.task_id,
                content=TextContent(
                    author="agent",
                    content="Sorry, I had trouble processing that question. Could you try asking again?",
                ),
                trace_id=state_machine_data.task_id,
            )
            state_machine_data.waiting_for_user_input = True
            return LiveShopState.DISPLAYING_PRODUCT