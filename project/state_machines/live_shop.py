"""State machine for LiveShop workflow."""
from enum import Enum
from typing import Any, Dict, List, Optional, override

from pydantic import BaseModel

from agentex.lib.sdk.state_machine import StateMachine
from agentex.types.span import Span


class LiveShopState(str, Enum):
    """States for the LiveShop workflow."""

    WAITING_FOR_STREAM = "WAITING_FOR_STREAM"
    INGESTING_STREAM = "INGESTING_STREAM"
    DETECTING_PRODUCT = "DETECTING_PRODUCT"
    QUERYING_INVENTORY = "QUERYING_INVENTORY"
    DISPLAYING_PRODUCT = "DISPLAYING_PRODUCT"
    HANDLING_CHAT = "HANDLING_CHAT"
    PROCESSING_PURCHASE = "PROCESSING_PURCHASE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProductData(BaseModel):
    """Data model for a detected/active product."""

    sku: str = ""
    name: str = ""
    description: str = ""
    base_price: float = 0.0
    tags: List[str] = []
    colors: List[str] = []
    sizes: List[str] = []
    stock: Dict[str, int] = {}  # e.g. {"blue_M": 12, "red_L": 3}
    images: List[str] = []


class OrderData(BaseModel):
    """Data model for a purchase order."""

    order_id: str = ""
    session_id: str = ""
    viewer_id: str = ""
    sku: str = ""
    color: str = ""
    size: str = ""
    status: str = "RESERVED"  # RESERVED | PAID | EXPIRED | CANCELLED
    reserved_at: str = ""
    expires_at: str = ""
    checkout_url: str = ""


class LiveShopData(BaseModel):
    """Data model for LiveShop workflow state."""

    # Session info
    session_id: str = ""
    host_id: str = ""
    stream_url: str = ""
    stream_active: bool = False

    # Current product being showcased
    active_product: Optional[ProductData] = None
    visual_description: str = ""
    product_detection_confidence: float = 0.0

    # Viewer interaction
    user_query: str = ""
    current_query: str = ""
    conversation_history: List[Dict[str, Any]] = []
    current_turn: int = 0
    messages_received: int = 0

    # Orders
    active_orders: List[OrderData] = []
    completed_orders: List[OrderData] = []

    # Workflow state
    task_id: Optional[str] = None
    current_span: Optional[Span] = None
    error_message: str = ""
    waiting_for_user_input: bool = True
    waiting_for_stream: bool = True

    # Stream processing
    last_detection_timestamp: str = ""
    detection_interval_seconds: int = 5  # How often to run product detection
    products_detected_count: int = 0

    # Analytics
    viewer_count: int = 0
    total_questions_answered: int = 0
    total_items_reserved: int = 0
    total_items_sold: int = 0


class LiveShopStateMachine(StateMachine[LiveShopData]):
    """State machine for orchestrating the LiveShop workflow."""

    @override
    async def terminal_condition(self) -> bool:
        """Check if the state machine has reached a terminal state."""
        return self.get_current_state() in [
            LiveShopState.COMPLETED,
            LiveShopState.FAILED,
        ]