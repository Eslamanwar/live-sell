"""Frame Ingestion → Gemini Live API bridge.

Architecture (hackathon-safe, no aiortc needed):
    Host Browser
      ├── canvas.toBlob() captures frame every 2s
      ├── Sends JPEG as binary WebSocket message to /ingest/{session_id}
      └── Cloud Run receives bytes → Gemini Live API session

Uses the NEW google.genai SDK (not google.generativeai) for the Live API:
    from google import genai
    client = genai.Client(api_key=...)
    async with client.aio.live.connect(model="gemini-2.0-flash-live", ...) as session:
        await session.send_realtime_input(video=frame_bytes)

NO MOCKS — requires GEMINI_API_KEY to function.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from agentex.lib.utils.logging import make_logger

logger = make_logger(__name__)


class GeminiLiveStreamProcessor:
    """
    Processes video frames from the host's browser via Gemini 2.0 Flash Live API.

    Flow:
    1. Host browser captures frames via canvas.toBlob() every 2 seconds
    2. Frames sent as binary WebSocket messages to /ingest/{session_id}
    3. This processor receives JPEG bytes
    4. Sends frames to Gemini Live API via google.genai streaming session
    5. Parses Gemini's response for product detection
    6. Calls on_product_detected callback with results

    This avoids WebRTC server-side complexity entirely — just WebSocket + canvas.
    """

    def __init__(
        self,
        session_id: str,
        gemini_api_key: str,
        model: str = "gemini-2.0-flash-live",
        on_product_detected: Optional[Callable] = None,
    ):
        """
        Initialize the stream processor.

        Args:
            session_id: Unique session identifier
            gemini_api_key: API key for Gemini (REQUIRED)
            model: Must be "gemini-2.0-flash-live" for Live API
            on_product_detected: Callback when a product is detected
        """
        self.session_id = session_id
        self.gemini_api_key = gemini_api_key
        self.model = model
        self.on_product_detected = on_product_detected
        self.is_running = False
        self._last_detection: Optional[Dict[str, Any]] = None
        self._gemini_session = None
        self._client = None

    async def start(self) -> None:
        """Start the Gemini Live API session."""
        self.is_running = True

        if not self.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is required. Set it as an environment variable."
            )

        try:
            from google import genai
            from google.genai import types as genai_types

            self._client = genai.Client(api_key=self.gemini_api_key)

            # Configure the Live API session for video analysis
            live_config = genai_types.LiveConnectConfig(
                response_modalities=["TEXT"],
                system_instruction=(
                    "You are a product detection system for a live shopping stream. "
                    "When you see a product being held up or showcased by the host, "
                    "describe it concisely: product type, color, notable features. "
                    "Format: PRODUCT: <description>. "
                    "If no product is clearly visible, respond: NO_PRODUCT_DETECTED"
                ),
            )

            # Connect to Gemini Live API
            self._gemini_session = await self._client.aio.live.connect(
                model=self.model,
                config=live_config,
            )

            logger.info(
                f"Gemini Live session connected for session {self.session_id} "
                f"(model: {self.model})"
            )

        except ImportError:
            raise ImportError(
                "google-genai not installed. Install with: pip install google-genai"
            )

    async def process_frame(self, frame_bytes: bytes) -> Optional[Dict[str, Any]]:
        """
        Send a video frame to Gemini Live API and get product detection result.

        Args:
            frame_bytes: JPEG-encoded video frame from the host's browser

        Returns:
            Detection result dict or None
        """
        if not self._gemini_session:
            raise RuntimeError(
                "Gemini Live session not connected. Call start() first."
            )

        try:
            from google.genai import types as genai_types

            # Send the frame to Gemini Live API as realtime video input
            await self._gemini_session.send_realtime_input(
                video=genai_types.Blob(data=frame_bytes, mime_type="image/jpeg")
            )

            # Collect the response
            result_text = ""
            async for response in self._gemini_session.receive():
                if response.text:
                    result_text += response.text
                # Break after first complete response
                if response.server_content and response.server_content.turn_complete:
                    break

            if not result_text:
                return None

            result_text = result_text.strip()

            if "NO_PRODUCT_DETECTED" in result_text:
                return {
                    "visual_description": "",
                    "confidence": 0.0,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }

            # Extract product description
            visual_description = result_text
            if "PRODUCT:" in result_text:
                visual_description = result_text.split("PRODUCT:")[1].strip()

            detection = {
                "visual_description": visual_description,
                "confidence": 0.85,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }

            # Check if this is a new product
            if self._is_new_product(detection):
                self._last_detection = detection
                logger.info(f"New product detected: {visual_description[:60]}...")

                if self.on_product_detected:
                    await self.on_product_detected(detection)

            return detection

        except Exception as e:
            logger.error(f"Gemini Live frame analysis error: {e}")
            return None

    async def stop(self) -> None:
        """Close the Gemini Live session."""
        self.is_running = False

        if self._gemini_session:
            try:
                await self._gemini_session.close()
            except Exception:
                pass
            self._gemini_session = None

        logger.info(f"Stream processor stopped for session {self.session_id}")

    def _is_new_product(self, detection: Dict[str, Any]) -> bool:
        """Check if detection represents a new/different product."""
        if not self._last_detection:
            return True

        current_desc = detection.get("visual_description", "").lower()
        last_desc = self._last_detection.get("visual_description", "").lower()

        current_words = set(current_desc.split())
        last_words = set(last_desc.split())

        if not current_words or not last_words:
            return True

        overlap = len(current_words & last_words) / max(
            len(current_words), len(last_words)
        )
        return overlap < 0.5


# ---------------------------------------------------------------------------
# WebSocket server for receiving frames from host browser
# ---------------------------------------------------------------------------

# Active processors per session
_processors: Dict[str, GeminiLiveStreamProcessor] = {}


async def handle_ingest_connection(
    websocket: WebSocketServerProtocol, path: str
) -> None:
    """
    Handle incoming frame data from the host's browser.

    Expected path: /ingest/{session_id}
    Messages: binary JPEG frames from canvas.toBlob()

    Args:
        websocket: The WebSocket connection from the host
        path: Connection path containing session ID
    """
    parts = path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "ingest":
        await websocket.close(1008, "Invalid path. Use /ingest/{session_id}")
        return

    session_id = parts[1]
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    gemini_live_model = os.getenv("GEMINI_LIVE_MODEL", "gemini-2.0-flash-live")

    if not gemini_api_key:
        logger.error("GEMINI_API_KEY not set — cannot process frames")
        await websocket.close(
            1008, "Server misconfigured: GEMINI_API_KEY not set"
        )
        return

    logger.info(f"Host connected for frame ingestion: session {session_id}")

    # Create and start a Gemini Live processor for this session
    processor = GeminiLiveStreamProcessor(
        session_id=session_id,
        gemini_api_key=gemini_api_key,
        model=gemini_live_model,
    )

    try:
        await processor.start()
    except Exception as e:
        logger.error(f"Failed to start Gemini Live session: {e}")
        await websocket.close(1011, f"Gemini Live connection failed: {e}")
        return

    _processors[session_id] = processor
    frame_count = 0

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                # Binary message = JPEG frame from canvas.toBlob()
                frame_count += 1
                logger.debug(
                    f"Frame #{frame_count} received for session {session_id} "
                    f"({len(message)} bytes)"
                )

                # Process the frame through Gemini Live
                detection = await processor.process_frame(message)

                # Send detection result back to host for the dashboard log
                if detection:
                    await websocket.send(json.dumps({
                        "type": "detection",
                        "frame": frame_count,
                        **detection,
                    }))

            elif isinstance(message, str):
                # Text message = control commands
                try:
                    data = json.loads(message)
                    if data.get("type") == "stop":
                        logger.info(
                            f"Host stopped stream for session {session_id}"
                        )
                        break
                except json.JSONDecodeError:
                    pass

    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Host disconnected from session {session_id}")
    finally:
        await processor.stop()
        _processors.pop(session_id, None)
        logger.info(
            f"Frame ingestion ended for session {session_id} "
            f"(total frames: {frame_count})"
        )


async def run_ingest_server(host: str = "0.0.0.0", port: int = 8002) -> None:
    """
    Run the frame ingestion WebSocket server.

    This is separate from the viewer WebSocket server (:8001).
    Host browsers connect here to send camera frames.

    Args:
        host: Host to bind to
        port: Port to listen on (default 8002)
    """
    server = await websockets.serve(handle_ingest_connection, host, port)
    logger.info(f"Frame ingestion server started on ws://{host}:{port}")
    logger.info("Host browsers connect to: ws://<host>:8002/ingest/<session_id>")

    try:
        await asyncio.Future()  # Run forever
    except asyncio.CancelledError:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(run_ingest_server())