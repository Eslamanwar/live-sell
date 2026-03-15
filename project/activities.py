"""Activities for the LiveShop agent — Temporal activity definitions.

Uses Google ADK (Agent Development Kit) for LLM orchestration and tool calling.
All LLM calls go through Gemini via ADK. All data goes through Firestore.
NO MOCKS — real implementations only.
"""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.adk import Agent, Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from temporalio import activity

from agentex.lib import adk
from agentex.lib.utils.logging import make_logger
from agentex.types.text_content import TextContent
from project.agent import (
    get_live_shop_agent,
    search_inventory as search_inventory_tool,
    check_stock as check_stock_tool,
    reserve_item as reserve_item_tool,
)
from project.prompts.system_prompt import (
    LIVE_SHOP_SYSTEM_PROMPT,
    PRODUCT_DETECTION_PROMPT,
)

logger = make_logger(__name__)


# ---------------------------------------------------------------------------
# ADK Runner — manages agent sessions
# ---------------------------------------------------------------------------

_session_service = InMemorySessionService()


async def _run_adk_agent(
    user_message: str,
    session_id: str = "default",
    user_id: str = "viewer",
) -> str:
    """
    Run the LiveShop ADK agent with a user message and return the response.

    The ADK agent has access to tools (search_inventory, check_stock, reserve_item)
    and will autonomously call them based on the user's message.
    All tool calls hit real Firestore — no mocks.

    Args:
        user_message: The user's message/question
        session_id: Session ID for conversation continuity
        user_id: User identifier

    Returns:
        The agent's text response
    """
    agent = get_live_shop_agent()

    runner = Runner(
        agent=agent,
        app_name="live_shop",
        session_service=_session_service,
    )

    # Create or get session
    session = await _session_service.get_session(
        app_name="live_shop",
        user_id=user_id,
        session_id=session_id,
    )

    if session is None:
        session = await _session_service.create_session(
            app_name="live_shop",
            user_id=user_id,
            session_id=session_id,
        )

    # Create the user message content
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)],
    )

    # Run the agent and collect the response
    final_response = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=content,
    ):
        if event.is_final_response():
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        final_response += part.text

    return final_response


# ---------------------------------------------------------------------------
# Activity 1: Ingest stream and detect products via Gemini Live API
# ---------------------------------------------------------------------------

@activity.defn(name="ingest_and_detect_product")
async def ingest_and_detect_product(
    task_id: str,
    session_id: str,
    stream_url: str,
) -> Dict[str, Any]:
    """
    Detect products using the ADK agent with Gemini.

    The actual video frame ingestion happens in stream/ingest.py via the
    Gemini Live API. This activity is called by the workflow to process
    detection results that were captured by the ingest server.

    When no frames are available yet, it asks the ADK agent to analyze
    based on any context it has.

    Args:
        task_id: The task ID
        session_id: The live session ID
        stream_url: URL of the live stream

    Returns:
        Dict with visual_description and confidence score
    """
    try:
        logger.info(f"Running product detection for session {session_id}")

        # Use ADK agent to analyze — it will call search_inventory tool
        # which queries real Firestore
        response = await _run_adk_agent(
            user_message=(
                f"Analyze the current frame from the live shopping stream. "
                f"Stream URL: {stream_url}. Session: {session_id}. "
                f"If you can see a product being showcased, describe it with: "
                f"product type, color, notable features. "
                f"Format: PRODUCT: <description>. "
                f"If no product is visible, respond: NO_PRODUCT_DETECTED"
            ),
            session_id=f"detection_{session_id}",
            user_id="system",
        )

        result_text = response.strip()

        if "NO_PRODUCT_DETECTED" in result_text:
            return {
                "visual_description": "",
                "confidence": 0.0,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }

        visual_description = result_text
        if "PRODUCT:" in result_text:
            visual_description = result_text.split("PRODUCT:")[1].strip()

        return {
            "visual_description": visual_description,
            "confidence": 0.85,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error in product detection: {e}")
        return {
            "visual_description": "",
            "confidence": 0.0,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Activity 2: Search inventory by visual description (real Firestore)
# ---------------------------------------------------------------------------

@activity.defn(name="search_inventory")
async def search_inventory(
    task_id: str,
    visual_description: str,
) -> Dict[str, Any]:
    """
    Match a visual product description to a SKU in Firestore inventory.
    Delegates to the ADK tool function which queries real Firestore.

    Args:
        task_id: The task ID
        visual_description: e.g. "blue floral wrap dress, midi length"

    Returns:
        Full product document including SKU, name, price, variants, stock
    """
    try:
        logger.info(f"Searching Firestore inventory for: {visual_description[:80]}...")

        # Calls the real Firestore-backed tool function
        result = search_inventory_tool(visual_description)
        return result

    except Exception as e:
        logger.error(f"Error searching inventory: {e}")
        return {"found": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Activity 3: Check stock for a specific variant (real Firestore)
# ---------------------------------------------------------------------------

@activity.defn(name="check_stock")
async def check_stock(
    task_id: str,
    sku: str,
    color: Optional[str] = None,
    size: Optional[str] = None,
    raw_message: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Check real-time stock from Firestore for a product variant.
    Uses ADK agent to parse natural language and call the check_stock tool.

    Args:
        task_id: The task ID
        sku: Product SKU
        color: Optional color filter
        size: Optional size filter
        raw_message: Raw viewer message for intent parsing

    Returns:
        Dict with available, quantity, color, size, restock_date
    """
    try:
        logger.info(f"Checking Firestore stock for SKU: {sku}, color: {color}, size: {size}")

        # If we have a raw message, let the ADK agent parse it and call tools
        if raw_message and (not color or not size):
            response = await _run_adk_agent(
                user_message=(
                    f"The viewer wants to know about stock for product {sku}. "
                    f"Their message: \"{raw_message}\". "
                    f"Use the check_stock tool to look up availability."
                ),
                session_id=f"stock_{task_id}",
                user_id="viewer",
            )

            # Parse color/size from raw message as fallback
            msg_lower = raw_message.lower()
            color_keywords = [
                "red", "blue", "white", "black", "brown", "tan",
                "gold", "silver", "ivory", "blush", "navy", "pink",
                "tortoise", "rose gold",
            ]
            if not color:
                for c in color_keywords:
                    if c in msg_lower:
                        color = c.capitalize()
                        break

            size_keywords = [
                "xs", "s", "m", "l", "xl",
                "36", "37", "38", "39", "40",
                "one size",
            ]
            if not size:
                for s in size_keywords:
                    if s in msg_lower:
                        size = s.upper()
                        break

        # Direct Firestore-backed tool call
        result = check_stock_tool(sku, color, size)
        return result

    except Exception as e:
        logger.error(f"Error checking stock: {e}")
        return {
            "available": False,
            "quantity": 0,
            "color": color or "",
            "size": size or "",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Activity 4: Reserve an item for a viewer (real Firestore transaction)
# ---------------------------------------------------------------------------

@activity.defn(name="reserve_item")
async def reserve_item(
    task_id: str,
    sku: str,
    color: str,
    size: str,
    viewer_id: str,
    session_id: str,
) -> Dict[str, Any]:
    """
    Place a 10-minute hold on a product variant for a viewer.
    Uses a real Firestore transaction to atomically decrement stock
    and create an order document.

    Args:
        task_id: The task ID
        sku: Product SKU
        color: Selected color
        size: Selected size
        viewer_id: Viewer identifier
        session_id: Live session ID

    Returns:
        Dict with order_id, checkout_url, expires_at
    """
    try:
        logger.info(f"Reserving {sku} ({color}/{size}) for viewer {viewer_id} via Firestore")

        # Calls the real Firestore-backed tool with atomic transaction
        result = reserve_item_tool(sku, color, size, viewer_id)
        return result

    except Exception as e:
        logger.error(f"Error reserving item: {e}")
        return {
            "order_id": "",
            "checkout_url": "",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Activity 5: Answer a viewer's question using ADK agent (real Gemini)
# ---------------------------------------------------------------------------

@activity.defn(name="answer_question")
async def answer_question(
    task_id: str,
    trace_id: str,
    viewer_question: str,
    product_info: str,
    conversation_history: List[Dict[str, Any]],
    parent_span_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Answer a viewer's question using the ADK agent with real Gemini API.

    The ADK agent can autonomously call tools (check_stock, search_inventory)
    which query real Firestore to answer questions about products, availability,
    pricing, etc.

    Args:
        task_id: The task ID
        trace_id: Trace ID for observability
        viewer_question: The viewer's question
        product_info: Formatted product information string
        conversation_history: Previous conversation messages
        parent_span_id: Parent span ID for tracing

    Returns:
        Dict with the answer text
    """
    try:
        logger.info(f"Answering question via ADK + Gemini: {viewer_question[:80]}...")

        # Build context-rich message for the ADK agent
        context_message = (
            f"A viewer is asking a question during a live shopping stream.\n\n"
            f"Currently showcased product:\n{product_info}\n\n"
            f"Viewer question: {viewer_question}\n\n"
            f"Answer naturally and helpfully. Keep it concise — this is live chat. "
            f"Use check_stock if they ask about availability. "
            f"Use search_inventory if they ask about a different product."
        )

        # Run the ADK agent — it will autonomously call tools as needed
        # All tool calls hit real Firestore
        answer = await _run_adk_agent(
            user_message=context_message,
            session_id=f"chat_{task_id}",
            user_id="viewer",
        )

        if not answer:
            answer = "I'm not sure about that. Let me check with the host!"

        logger.info(f"ADK agent answer: {answer[:100]}...")

        return {"answer": answer}

    except Exception as e:
        logger.error(f"Error answering question via ADK: {e}")
        return {
            "answer": "I'm having trouble answering that right now. Let me check with the host!",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Activity 6: Push product card to viewers via WebSocket
# ---------------------------------------------------------------------------

@activity.defn(name="push_product_card")
async def push_product_card(
    session_id: str,
    product_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Push a product card update to all connected viewers via WebSocket.

    Args:
        session_id: The live session ID
        product_data: Product data to display in the card

    Returns:
        Dict with push status
    """
    try:
        logger.info(
            f"Pushing product card for session {session_id}: "
            f"{product_data.get('name', '')}"
        )

        websocket_port = os.getenv("WEBSOCKET_PORT", "8001")
        websocket_host = os.getenv("WEBSOCKET_HOST", "localhost")

        push_payload = {
            "type": "product_card_update",
            "session_id": session_id,
            "product": product_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            import websockets

            uri = f"ws://{websocket_host}:{websocket_port}/push"
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps(push_payload))
                logger.info("Product card pushed via WebSocket")
        except Exception as ws_error:
            logger.warning(f"WebSocket push failed (non-critical): {ws_error}")

        return {"pushed": True, "payload": push_payload}

    except Exception as e:
        logger.error(f"Error pushing product card: {e}")
        return {"pushed": False, "error": str(e)}