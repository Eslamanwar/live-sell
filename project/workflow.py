"""Main workflow for the LiveShop agent."""
import asyncio
from typing import override

from temporalio import workflow

from agentex.lib import adk
from agentex.lib.core.temporal.types.workflow import SignalName
from agentex.lib.core.temporal.workflows.workflow import BaseWorkflow
from agentex.lib.environment_variables import EnvironmentVariables
from agentex.lib.sdk.state_machine.state import State
from agentex.lib.types.acp import CreateTaskParams, SendEventParams
from agentex.lib.utils.logging import make_logger
from agentex.types.text_content import TextContent
from project.state_machines.live_shop import (
    LiveShopData,
    LiveShopState,
    LiveShopStateMachine,
)
from project.workflows.live_shop.waiting_for_stream import WaitingForStreamWorkflow
from project.workflows.live_shop.ingesting_stream import IngestingStreamWorkflow
from project.workflows.live_shop.querying_inventory import QueryingInventoryWorkflow
from project.workflows.live_shop.displaying_product import DisplayingProductWorkflow
from project.workflows.live_shop.handling_chat import HandlingChatWorkflow
from project.workflows.live_shop.processing_purchase import ProcessingPurchaseWorkflow
from project.workflows.live_shop.terminal_states import CompletedWorkflow, FailedWorkflow

environment_variables = EnvironmentVariables.refresh()

if environment_variables.WORKFLOW_NAME is None:
    raise ValueError("Environment variable WORKFLOW_NAME is not set")

if environment_variables.AGENT_NAME is None:
    raise ValueError("Environment variable AGENT_NAME is not set")

logger = make_logger(__name__)


@workflow.defn(name=environment_variables.WORKFLOW_NAME)
class LiveShopWorkflow(BaseWorkflow):
    """
    LiveShop workflow for AI-powered live commerce.

    This workflow orchestrates:
    - Live video stream ingestion via Gemini Live API
    - Real-time product detection and inventory lookup
    - Viewer chat handling with AI-powered responses
    - One-click purchase flow with item reservation
    """

    def __init__(self):
        """Initialize the LiveShop workflow with state machine."""
        super().__init__(display_name=environment_variables.AGENT_NAME)

        # Initialize state machine with all workflow states
        self.state_machine = LiveShopStateMachine(
            initial_state=LiveShopState.WAITING_FOR_STREAM,
            states=[
                State(
                    name=LiveShopState.WAITING_FOR_STREAM,
                    workflow=WaitingForStreamWorkflow(),
                ),
                State(
                    name=LiveShopState.INGESTING_STREAM,
                    workflow=IngestingStreamWorkflow(),
                ),
                State(
                    name=LiveShopState.QUERYING_INVENTORY,
                    workflow=QueryingInventoryWorkflow(),
                ),
                State(
                    name=LiveShopState.DISPLAYING_PRODUCT,
                    workflow=DisplayingProductWorkflow(),
                ),
                State(
                    name=LiveShopState.HANDLING_CHAT,
                    workflow=HandlingChatWorkflow(),
                ),
                State(
                    name=LiveShopState.PROCESSING_PURCHASE,
                    workflow=ProcessingPurchaseWorkflow(),
                ),
                State(
                    name=LiveShopState.FAILED,
                    workflow=FailedWorkflow(),
                ),
                State(
                    name=LiveShopState.COMPLETED,
                    workflow=CompletedWorkflow(),
                ),
            ],
            state_machine_data=LiveShopData(),
            trace_transitions=True,
        )

    @workflow.signal(name="start_stream")
    async def on_start_stream(self, stream_url: str, host_id: str = "") -> None:
        """
        Handle stream start signal from the host.

        Args:
            stream_url: URL of the live stream
            host_id: Identifier of the host
        """
        state_data = self.state_machine.get_state_machine_data()
        state_data.stream_url = stream_url
        state_data.host_id = host_id
        state_data.stream_active = True
        state_data.waiting_for_stream = False

        logger.info(f"Stream started by host {host_id}: {stream_url}")

    @workflow.signal(name="end_stream")
    async def on_end_stream(self) -> None:
        """Handle stream end signal from the host."""
        state_data = self.state_machine.get_state_machine_data()
        state_data.stream_active = False

        logger.info("Stream ended by host")

    @override
    @workflow.signal(name=SignalName.RECEIVE_EVENT)
    async def on_task_event_send(self, params: SendEventParams) -> None:
        """
        Handle incoming viewer messages and trigger appropriate workflow actions.

        Args:
            params: Event parameters containing the viewer's message
        """
        state_data = self.state_machine.get_state_machine_data()
        task = params.task
        message = params.event.content

        # Extract message content
        message_content = ""
        if hasattr(message, "content"):
            content_val = getattr(message, "content", "")
            if isinstance(content_val, str):
                message_content = content_val.strip()

        logger.info(f"Received viewer message: {message_content[:100]}...")

        # Create span for tracing if not exists
        if not state_data.current_span:
            state_data.current_span = await adk.tracing.start_span(
                trace_id=task.id,
                name=f"Turn {state_data.current_turn}",
                input={
                    "task_id": task.id,
                    "message": message_content,
                },
            )

        # Update state data
        state_data.user_query = message_content
        state_data.current_query = message_content
        state_data.messages_received += 1
        state_data.current_turn += 1

        # Add to conversation history
        state_data.conversation_history.append({
            "role": "user",
            "content": message_content,
        })

        # Echo the viewer's message to the UI
        await adk.messages.create(
            task_id=task.id,
            content=TextContent(
                author="user",
                content=message_content,
            ),
            trace_id=task.id,
            parent_span_id=state_data.current_span.id if state_data.current_span else None,
        )

        # Check for special commands
        message_lower = message_content.lower()

        if message_lower in ["start", "go live", "start stream"]:
            # Host starting the stream
            if state_data.waiting_for_stream:
                state_data.stream_active = True
                state_data.waiting_for_stream = False
                state_data.session_id = task.id
                logger.info("Stream started via chat command")
                return

        if message_lower in ["stop", "end stream", "end"]:
            # Host ending the stream
            state_data.stream_active = False
            logger.info("Stream ended via chat command")
            return

        # Regular viewer message — wake up the workflow
        state_data.waiting_for_user_input = False

        # Get current state and handle transitions
        current_state = self.state_machine.get_current_state()

        if current_state == LiveShopState.WAITING_FOR_STREAM:
            # If we're still waiting for stream, start it
            state_data.stream_active = True
            state_data.waiting_for_stream = False
            state_data.session_id = task.id

        elif current_state in [LiveShopState.COMPLETED, LiveShopState.FAILED]:
            # Try to recover
            state_data.error_message = ""
            state_data.stream_active = True
            await self.state_machine.transition(LiveShopState.INGESTING_STREAM)

    @override
    @workflow.run
    async def on_task_create(self, params: CreateTaskParams) -> None:
        """
        Initialize and run the workflow when a task is created.

        Args:
            params: Task creation parameters
        """
        task = params.task

        # Set task ID in state machine
        self.state_machine.set_task_id(task.id)

        # Initialize state data
        state_data = self.state_machine.get_state_machine_data()
        state_data.task_id = task.id
        state_data.session_id = task.id

        logger.info(f"Starting LiveShop workflow for task: {task.id}")

        # Send welcome message
        await adk.messages.create(
            task_id=task.id,
            content=TextContent(
                author="agent",
                content=(
                    "🛍️ **Welcome to LiveShop!**\n\n"
                    "I'm your AI shopping assistant. Here's what I can do:\n\n"
                    "- 📸 **Auto-detect** products from the live stream\n"
                    "- 💬 **Answer questions** about products (colors, sizes, materials)\n"
                    "- 📦 **Check stock** in real-time\n"
                    "- 🛒 **Reserve items** for one-click purchase\n\n"
                    "The host can start streaming anytime. "
                    "Type **'start'** or send a message to begin!\n"
                ),
            ),
            trace_id=task.id,
        )

        # Mark that we're waiting for the stream to start
        state_data.waiting_for_stream = True

        try:
            # Run the state machine
            await self.state_machine.run()

        except asyncio.CancelledError as error:
            logger.warning(f"Task canceled by user: {task.id}")
            raise error

        except Exception as error:
            logger.error(f"Workflow error for task {task.id}: {str(error)}")

            try:
                await adk.messages.create(
                    task_id=task.id,
                    content=TextContent(
                        author="agent",
                        content=(
                            f"❌ An error occurred: {str(error)}\n\n"
                            f"Please try restarting the stream."
                        ),
                    ),
                    trace_id=task.id,
                )
            except Exception as msg_error:
                logger.error(f"Failed to send error message: {str(msg_error)}")

            state_data.error_message = str(error)
            await self.state_machine.transition(LiveShopState.FAILED)

            raise error