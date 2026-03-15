"""WebSocket server for real-time product card push to viewers.

This module handles:
1. Managing WebSocket connections from viewer clients
2. Broadcasting product card updates to all connected viewers
3. Handling per-viewer private messages (checkout URLs, etc.)
4. Forwarding viewer chat/buy messages to the Temporal workflow via ACP /send_event
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Set

import httpx
import websockets
from websockets.server import WebSocketServerProtocol

from agentex.lib.utils.logging import make_logger

logger = make_logger(__name__)


class LiveShopWebSocketServer:
    """
    WebSocket server for pushing real-time updates to viewers.

    Handles:
    - Product card updates (broadcast to all viewers in a session)
    - Stock updates (broadcast)
    - Private messages (checkout URLs, order confirmations)
    - Forwarding viewer chat/buy messages to Temporal workflow via ACP
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8001):
        """
        Initialize the WebSocket server.

        Args:
            host: Host to bind to
            port: Port to listen on
        """
        self.host = host
        self.port = port
        # Map of session_id -> set of connected WebSocket clients
        self._sessions: Dict[str, Set[WebSocketServerProtocol]] = {}
        # Map of viewer_id -> WebSocket client (for private messages)
        self._viewers: Dict[str, WebSocketServerProtocol] = {}
        # Map of session_id -> task_id (ACP task ID for the Temporal workflow)
        self._session_tasks: Dict[str, str] = {}
        self._server = None

        # ACP server URL for forwarding messages to the Temporal workflow
        self._acp_base_url = os.getenv("ACP_URL", "http://localhost:8000")
        self._http_client = httpx.AsyncClient(timeout=10.0)

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
        )
        logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await self._http_client.aclose()
        logger.info("WebSocket server stopped")

    def register_task(self, session_id: str, task_id: str) -> None:
        """
        Register the ACP task ID for a session so chat messages can be forwarded.

        Args:
            session_id: The live stream session ID
            task_id: The ACP/Temporal task ID
        """
        self._session_tasks[session_id] = task_id
        logger.info(f"Registered task {task_id} for session {session_id}")

    async def _handle_connection(
        self, websocket: WebSocketServerProtocol, path: str
    ) -> None:
        """
        Handle a new WebSocket connection.

        Expected connection paths:
        - /viewer/{session_id}/{viewer_id} — viewer joining a stream
        - /push — internal push endpoint for the agent

        Args:
            websocket: The WebSocket connection
            path: The connection path
        """
        try:
            if path.startswith("/viewer/"):
                await self._handle_viewer(websocket, path)
            elif path == "/push":
                await self._handle_push(websocket)
            else:
                await websocket.close(1008, "Invalid path")

        except websockets.exceptions.ConnectionClosed:
            logger.debug(f"Connection closed: {path}")
        except Exception as e:
            logger.error(f"WebSocket error on {path}: {e}")

    async def _handle_viewer(
        self, websocket: WebSocketServerProtocol, path: str
    ) -> None:
        """
        Handle a viewer connection.

        Args:
            websocket: The viewer's WebSocket connection
            path: Path in format /viewer/{session_id}/{viewer_id}
        """
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            await websocket.close(1008, "Invalid viewer path")
            return

        session_id = parts[1]
        viewer_id = parts[2]

        # Register the viewer
        if session_id not in self._sessions:
            self._sessions[session_id] = set()
        self._sessions[session_id].add(websocket)
        self._viewers[viewer_id] = websocket

        viewer_count = len(self._sessions[session_id])
        logger.info(
            f"Viewer {viewer_id} joined session {session_id} "
            f"(total viewers: {viewer_count})"
        )

        # Send welcome message
        await websocket.send(json.dumps({
            "type": "connected",
            "session_id": session_id,
            "viewer_id": viewer_id,
            "viewer_count": viewer_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

        try:
            # Keep connection alive and handle incoming messages
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")

                    if msg_type == "chat":
                        # Forward chat message to the Temporal workflow via ACP
                        content = data.get("content", "")
                        logger.info(
                            f"Chat from {viewer_id}: {content[:80]}"
                        )
                        await self._forward_to_acp(
                            session_id=session_id,
                            viewer_id=viewer_id,
                            content=content,
                        )

                    elif msg_type == "buy":
                        # Forward buy intent to the Temporal workflow via ACP
                        sku = data.get("sku", "")
                        color = data.get("color", "")
                        size = data.get("size", "")
                        logger.info(
                            f"Buy intent from {viewer_id}: {sku} ({color}/{size})"
                        )
                        buy_message = (
                            f"I want to buy {sku} in {color}, size {size}"
                        )
                        await self._forward_to_acp(
                            session_id=session_id,
                            viewer_id=viewer_id,
                            content=buy_message,
                        )

                    elif msg_type == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))

                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from viewer {viewer_id}")

        finally:
            # Cleanup on disconnect
            self._sessions.get(session_id, set()).discard(websocket)
            self._viewers.pop(viewer_id, None)

            remaining = len(self._sessions.get(session_id, set()))
            logger.info(
                f"Viewer {viewer_id} left session {session_id} "
                f"(remaining: {remaining})"
            )

            # Clean up empty sessions
            if session_id in self._sessions and not self._sessions[session_id]:
                del self._sessions[session_id]

    async def _forward_to_acp(
        self, session_id: str, viewer_id: str, content: str
    ) -> None:
        """
        Forward a viewer message to the Temporal workflow via ACP /tasks/{task_id}/send_event.

        This bridges the WebSocket ↔ Temporal gap:
            Viewer sends chat via WebSocket
                ↓
            WebSocket server calls ACP /send_event HTTP endpoint
                ↓
            ACP signals the Temporal workflow via workflow.on_task_event_send()
                ↓
            Workflow processes the message (HANDLING_CHAT or PROCESSING_PURCHASE)

        Args:
            session_id: The live stream session ID
            viewer_id: The viewer who sent the message
            content: The message content
        """
        # Look up the ACP task ID for this session
        task_id = self._session_tasks.get(session_id)

        if not task_id:
            # If no task is registered, use session_id as task_id (common pattern)
            task_id = session_id
            logger.debug(
                f"No registered task for session {session_id}, "
                f"using session_id as task_id"
            )

        try:
            # Call the ACP /tasks/{task_id}/send_event endpoint
            # This triggers workflow.on_task_event_send() in the Temporal workflow
            response = await self._http_client.post(
                f"{self._acp_base_url}/tasks/{task_id}/send_event",
                json={
                    "event": {
                        "content": {
                            "type": "text",
                            "content": content,
                            "author": viewer_id,
                        },
                    },
                },
                headers={"Content-Type": "application/json"},
            )

            if response.status_code in (200, 201, 202):
                logger.info(
                    f"Forwarded message from {viewer_id} to ACP task {task_id}"
                )
            else:
                logger.warning(
                    f"ACP /send_event returned {response.status_code}: "
                    f"{response.text[:200]}"
                )

        except httpx.ConnectError:
            logger.warning(
                f"Cannot reach ACP server at {self._acp_base_url} — "
                f"message from {viewer_id} not forwarded"
            )
        except Exception as e:
            logger.error(
                f"Error forwarding message to ACP: {e}"
            )

    async def _handle_push(self, websocket: WebSocketServerProtocol) -> None:
        """
        Handle internal push messages from the agent.

        Args:
            websocket: The push connection
        """
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")
                session_id = data.get("session_id", "")

                if msg_type == "product_card_update":
                    await self.broadcast_to_session(session_id, data)
                elif msg_type == "stock_update":
                    await self.broadcast_to_session(session_id, data)
                elif msg_type == "private_message":
                    viewer_id = data.get("viewer_id", "")
                    await self.send_to_viewer(viewer_id, data)
                elif msg_type == "register_task":
                    # Allow the agent to register a task_id for a session
                    task_id = data.get("task_id", "")
                    if session_id and task_id:
                        self.register_task(session_id, task_id)
                else:
                    logger.warning(f"Unknown push message type: {msg_type}")

            except json.JSONDecodeError:
                logger.warning("Invalid JSON in push message")

    async def broadcast_to_session(
        self, session_id: str, data: Dict[str, Any]
    ) -> None:
        """
        Broadcast a message to all viewers in a session.

        Args:
            session_id: The session to broadcast to
            data: The data to send
        """
        viewers = self._sessions.get(session_id, set())
        if not viewers:
            logger.debug(f"No viewers in session {session_id}")
            return

        message = json.dumps(data)
        disconnected = set()

        for ws in viewers:
            try:
                await ws.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(ws)

        # Clean up disconnected clients
        for ws in disconnected:
            viewers.discard(ws)

        logger.info(
            f"Broadcast to {len(viewers)} viewers in session {session_id}"
        )

    async def send_to_viewer(
        self, viewer_id: str, data: Dict[str, Any]
    ) -> None:
        """
        Send a private message to a specific viewer.

        Args:
            viewer_id: The viewer to send to
            data: The data to send
        """
        ws = self._viewers.get(viewer_id)
        if ws:
            try:
                await ws.send(json.dumps(data))
                logger.info(f"Private message sent to viewer {viewer_id}")
            except websockets.exceptions.ConnectionClosed:
                self._viewers.pop(viewer_id, None)
                logger.warning(f"Viewer {viewer_id} disconnected")
        else:
            logger.warning(f"Viewer {viewer_id} not found")

    def get_viewer_count(self, session_id: str) -> int:
        """Get the number of viewers in a session."""
        return len(self._sessions.get(session_id, set()))


async def run_websocket_server():
    """Run the WebSocket server as a standalone process."""
    host = os.getenv("WEBSOCKET_HOST", "0.0.0.0")
    port = int(os.getenv("WEBSOCKET_PORT", "8001"))

    server = LiveShopWebSocketServer(host=host, port=port)
    await server.start()

    # Keep running until cancelled
    try:
        await asyncio.Future()  # Run forever
    except asyncio.CancelledError:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(run_websocket_server())