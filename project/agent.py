"""Google ADK Agent definition for LiveShop.

Uses Google Agent Development Kit (ADK) for:
- Tool registration and autonomous calling
- Gemini model integration
- Session management
- Streaming responses

All tools use REAL Firestore queries — no mocks, no demo data.
"""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.adk import Agent
from google.adk.tools import FunctionTool
from google.genai import types

from agentex.lib.utils.logging import make_logger
from project.prompts.system_prompt import LIVE_SHOP_SYSTEM_PROMPT

logger = make_logger(__name__)


# ---------------------------------------------------------------------------
# Firestore helper — lazy singleton
# ---------------------------------------------------------------------------

_firestore_db = None


def _get_firestore_db():
    """Get or create the Firestore client (sync version for ADK tools)."""
    global _firestore_db
    if _firestore_db is None:
        from google.cloud import firestore

        project_id = os.getenv("PROJECT_ID", "")
        creds_json = os.getenv("FIRESTORE_CREDS", "")

        if creds_json:
            import json as _json
            import tempfile

            # Write creds to a temp file for the client
            creds_data = _json.loads(creds_json)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                _json.dump(creds_data, f)
                creds_path = f.name

            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                creds_path
            )
            _firestore_db = firestore.Client(
                project=project_id or creds_data.get("project_id"),
                credentials=credentials,
            )
        else:
            # Use Application Default Credentials (ADC) — works on GCP
            _firestore_db = firestore.Client(project=project_id or None)

        logger.info("Firestore client initialized")

    return _firestore_db


# ---------------------------------------------------------------------------
# ADK Tool definitions — these are called autonomously by Gemini via ADK
# All tools use REAL Firestore — no mocks.
# ---------------------------------------------------------------------------


def search_inventory(visual_description: str) -> dict:
    """
    Match a visual product description to a SKU in the Firestore inventory.
    Searches the /products collection using tag-based matching on product
    names, descriptions, and tags.

    Args:
        visual_description: Natural language description of the product,
            e.g. "blue floral wrap dress, midi length"

    Returns:
        Product details including SKU, name, price, colors, sizes, and stock levels.
        Returns {"found": false} if no match is found.
    """
    logger.info(f"[ADK Tool] search_inventory: {visual_description[:80]}...")

    try:
        db = _get_firestore_db()
        products_ref = db.collection("products")

        # Fetch all products and score by tag/name overlap
        desc_lower = visual_description.lower()
        desc_words = set(desc_lower.split())

        best_match = None
        best_score = 0

        for doc in products_ref.stream():
            product = doc.to_dict()
            product["sku"] = doc.id  # Document ID is the SKU

            score = 0

            # Score by tag overlap
            tags = [t.lower() for t in product.get("tags", [])]
            score += sum(2 for tag in tags if tag in desc_lower)

            # Score by name word overlap
            name_words = product.get("name", "").lower().split()
            score += sum(3 for word in name_words if word in desc_lower)

            # Score by description word overlap
            prod_desc = product.get("description", "").lower()
            prod_desc_words = set(prod_desc.split())
            score += len(desc_words & prod_desc_words)

            if score > best_score:
                best_score = score
                best_match = product

        if best_match and best_score > 0:
            logger.info(
                f"[ADK Tool] Found match: {best_match.get('name')} "
                f"(SKU: {best_match.get('sku')}, score: {best_score})"
            )
            return {"found": True, **best_match}

        logger.info("[ADK Tool] No matching product found in Firestore")
        return {"found": False, "message": "No matching product found in inventory."}

    except Exception as e:
        logger.error(f"[ADK Tool] Firestore search_inventory error: {e}")
        return {"found": False, "error": str(e)}


def check_stock(sku: str, color: str = None, size: str = None) -> dict:
    """
    Check real-time stock availability for a product variant from Firestore.

    Args:
        sku: Product SKU identifier, e.g. "DR-4421"
        color: Optional color to check, e.g. "Red"
        size: Optional size to check, e.g. "M" or "38"

    Returns:
        Stock availability with quantity, or total stock if no variant specified.
    """
    logger.info(f"[ADK Tool] check_stock: SKU={sku}, color={color}, size={size}")

    try:
        db = _get_firestore_db()
        doc = db.collection("products").document(sku).get()

        if not doc.exists:
            return {
                "available": False,
                "quantity": 0,
                "message": f"Product {sku} not found in inventory",
            }

        product = doc.to_dict()
        stock = product.get("stock", {})

        if color and size:
            variant_key = f"{color.lower()}_{size}"
            qty = stock.get(variant_key, 0)
            return {
                "available": qty > 0,
                "quantity": qty,
                "color": color,
                "size": size,
                "restock_date": None,
            }
        elif color:
            total = sum(
                v for k, v in stock.items() if k.lower().startswith(color.lower())
            )
            return {
                "available": total > 0,
                "quantity": total,
                "color": color,
                "size": "all sizes",
            }
        else:
            total = sum(stock.values())
            return {
                "available": total > 0,
                "quantity": total,
                "color": "all colors",
                "size": "all sizes",
            }

    except Exception as e:
        logger.error(f"[ADK Tool] Firestore check_stock error: {e}")
        return {"available": False, "quantity": 0, "error": str(e)}


def reserve_item(sku: str, color: str, size: str, viewer_id: str) -> dict:
    """
    Reserve a product variant for a viewer with a 10-minute hold.
    Uses a Firestore transaction to atomically decrement stock and create
    an order document.

    Args:
        sku: Product SKU to reserve, e.g. "DR-4421"
        color: Color variant to reserve, e.g. "Blue"
        size: Size variant to reserve, e.g. "M"
        viewer_id: Identifier of the viewer making the purchase

    Returns:
        Order confirmation with order_id, checkout_url, and expiration time.
    """
    logger.info(f"[ADK Tool] reserve_item: {sku} ({color}/{size}) for {viewer_id}")

    try:
        db = _get_firestore_db()
        from google.cloud import firestore

        variant_key = f"{color.lower()}_{size}"
        product_ref = db.collection("products").document(sku)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=10)
        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

        @firestore.transactional
        def reserve_in_transaction(transaction):
            # Read current stock
            doc = product_ref.get(transaction=transaction)
            if not doc.exists:
                return {"error": f"Product {sku} not found"}

            product = doc.to_dict()
            stock = product.get("stock", {})
            current_qty = stock.get(variant_key, 0)

            if current_qty <= 0:
                return {
                    "error": f"{color} {size} is out of stock",
                    "available": False,
                    "quantity": 0,
                }

            # Decrement stock atomically
            stock[variant_key] = current_qty - 1
            transaction.update(product_ref, {"stock": stock})

            # Create order document
            order_data = {
                "order_id": order_id,
                "sku": sku,
                "product_name": product.get("name", ""),
                "color": color,
                "size": size,
                "viewer_id": viewer_id,
                "status": "RESERVED",
                "price": product.get("base_price", 0),
                "reserved_at": now,
                "expires_at": expires_at,
                "checkout_url": f"/checkout/{order_id}",
            }
            transaction.set(
                db.collection("orders").document(order_id), order_data
            )

            return order_data

        transaction = db.transaction()
        result = reserve_in_transaction(transaction)

        if "error" in result and "order_id" not in result:
            return result

        logger.info(f"[ADK Tool] Reserved {sku} ({color}/{size}): {order_id}")
        return {
            "order_id": order_id,
            "checkout_url": f"/checkout/{order_id}",
            "expires_at": expires_at.isoformat(),
            "reserved_at": now.isoformat(),
            "remaining_stock": result.get("quantity", "unknown"),
            "message": (
                f"Reserved {color} {size} for 10 minutes. "
                f"Complete checkout at /checkout/{order_id}"
            ),
        }

    except Exception as e:
        logger.error(f"[ADK Tool] Firestore reserve_item error: {e}")
        return {"order_id": "", "checkout_url": "", "error": str(e)}


# ---------------------------------------------------------------------------
# ADK Agent factory
# ---------------------------------------------------------------------------


def create_live_shop_agent() -> Agent:
    """
    Create the LiveShop ADK agent with Gemini model and tools.

    Uses gemini-2.0-flash for text Q&A + tool calling (NOT the Live model).
    The Live model (gemini-2.0-flash-live) is only used in stream/ingest.py
    for real-time video streaming sessions.

    Returns:
        Configured ADK Agent instance
    """
    # ADK agent uses standard Gemini model for text Q&A + tool calling
    # NOT gemini-2.0-flash-live (that's only for streaming video sessions)
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    agent = Agent(
        name="live_shop_agent",
        model=gemini_model,
        description="AI-powered live commerce assistant for real-time shopping streams",
        instruction=LIVE_SHOP_SYSTEM_PROMPT.format(current_date=current_date),
        tools=[
            search_inventory,
            check_stock,
            reserve_item,
        ],
    )

    logger.info(f"Created LiveShop ADK agent with model: {gemini_model}")
    return agent


# Singleton agent instance
_agent_instance: Optional[Agent] = None


def get_live_shop_agent() -> Agent:
    """Get or create the singleton LiveShop ADK agent."""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = create_live_shop_agent()
    return _agent_instance