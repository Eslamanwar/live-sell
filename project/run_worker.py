"""Temporal worker for the LiveShop Agent."""
import asyncio
import os

from agentex.lib.core.temporal.activities import get_all_activities
from agentex.lib.core.temporal.workers.worker import AgentexWorker
from agentex.lib.environment_variables import EnvironmentVariables
from agentex.lib.utils.debug import setup_debug_if_enabled
from agentex.lib.utils.logging import make_logger
from project.activities import (
    answer_question,
    check_stock,
    ingest_and_detect_product,
    push_product_card,
    reserve_item,
    search_inventory,
)
from project.workflow import LiveShopWorkflow

environment_variables = EnvironmentVariables.refresh()

logger = make_logger(__name__)


async def _handle_health(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Minimal HTTP handler for Cloud Run health checks on $PORT."""
    await reader.read(1024)
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    await writer.drain()
    writer.close()


async def main():
    """Run the Temporal worker."""
    # Start health check server FIRST — Cloud Run requires $PORT to be bound
    # before the startup timeout, regardless of Temporal connectivity
    port = int(os.getenv("PORT", "8080"))
    health_server = await asyncio.start_server(_handle_health, "0.0.0.0", port)
    logger.info(f"Health check server listening on port {port}")

    setup_debug_if_enabled()

    task_queue_name = environment_variables.WORKFLOW_TASK_QUEUE
    if task_queue_name is None:
        raise ValueError("WORKFLOW_TASK_QUEUE is not set")

    worker = AgentexWorker(
        task_queue=task_queue_name,
    )

    all_activities = get_all_activities() + [
        ingest_and_detect_product,
        search_inventory,
        check_stock,
        reserve_item,
        answer_question,
        push_product_card,
    ]

    async with health_server:
        await worker.run(
            activities=all_activities,
            workflow=LiveShopWorkflow,
        )


if __name__ == "__main__":
    asyncio.run(main())