"""Firestore client for LiveShop inventory and order management.

Collections:
- /products/{sku} — Product catalog with variants and stock
- /sessions/{session_id} — Live stream sessions
- /orders/{order_id} — Purchase orders and reservations
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from agentex.lib.utils.logging import make_logger

logger = make_logger(__name__)


class FirestoreClient:
    """
    Async Firestore client for LiveShop data operations.

    Handles product inventory, session management, and order processing
    with atomic transactions for stock management.
    """

    def __init__(self, project_id: Optional[str] = None):
        """
        Initialize the Firestore client.

        Args:
            project_id: GCP project ID. If not provided, reads from env.
        """
        self.project_id = project_id or os.getenv("PROJECT_ID", "")
        self._db = None

    async def _get_db(self):
        """Lazy-initialize the Firestore client."""
        if self._db is None:
            from google.cloud import firestore

            self._db = firestore.AsyncClient(project=self.project_id)
        return self._db

    # -----------------------------------------------------------------------
    # Product Operations
    # -----------------------------------------------------------------------

    async def get_product(self, sku: str) -> Optional[Dict[str, Any]]:
        """
        Get a product by SKU.

        Args:
            sku: Product SKU

        Returns:
            Product data dict or None
        """
        db = await self._get_db()
        doc = await db.collection("products").document(sku).get()
        if doc.exists:
            return doc.to_dict()
        return None

    async def search_products_by_tags(
        self, tags: List[str], limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search products by matching tags.

        Args:
            tags: List of tags to match
            limit: Maximum results to return

        Returns:
            List of matching product dicts, sorted by relevance
        """
        db = await self._get_db()
        products_ref = db.collection("products")

        # Fetch all products and score by tag overlap
        results = []
        async for doc in products_ref.limit(50).stream():
            data = doc.to_dict()
            product_tags = [t.lower() for t in data.get("tags", [])]
            name_words = data.get("name", "").lower().split()

            score = sum(1 for tag in tags if tag.lower() in product_tags or tag.lower() in name_words)
            if score > 0:
                results.append((score, data))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r[1] for r in results[:limit]]

    async def get_stock(
        self, sku: str, color: Optional[str] = None, size: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get stock information for a product variant.

        Args:
            sku: Product SKU
            color: Optional color filter
            size: Optional size filter

        Returns:
            Stock information dict
        """
        product = await self.get_product(sku)
        if not product:
            return {"available": False, "quantity": 0}

        stock = product.get("stock", {})

        if color and size:
            variant_key = f"{color.lower()}_{size}"
            qty = stock.get(variant_key, 0)
            return {"available": qty > 0, "quantity": qty, "color": color, "size": size}
        elif color:
            total = sum(v for k, v in stock.items() if k.startswith(color.lower()))
            return {"available": total > 0, "quantity": total, "color": color, "size": "all"}
        else:
            total = sum(stock.values())
            return {"available": total > 0, "quantity": total, "color": "all", "size": "all"}

    async def decrement_stock(self, sku: str, color: str, size: str) -> bool:
        """
        Atomically decrement stock for a variant.

        Args:
            sku: Product SKU
            color: Color variant
            size: Size variant

        Returns:
            True if stock was decremented, False if out of stock
        """
        db = await self._get_db()
        from google.cloud import firestore

        variant_key = f"{color.lower()}_{size}"
        product_ref = db.collection("products").document(sku)

        @firestore.async_transactional
        async def decrement(transaction):
            doc = await product_ref.get(transaction=transaction)
            if not doc.exists:
                return False

            data = doc.to_dict()
            stock = data.get("stock", {})
            current_qty = stock.get(variant_key, 0)

            if current_qty <= 0:
                return False

            stock[variant_key] = current_qty - 1
            transaction.update(product_ref, {"stock": stock})
            return True

        transaction = db.transaction()
        return await decrement(transaction)

    # -----------------------------------------------------------------------
    # Session Operations
    # -----------------------------------------------------------------------

    async def create_session(
        self, session_id: str, host_id: str, stream_url: str
    ) -> Dict[str, Any]:
        """
        Create a new live stream session.

        Args:
            session_id: Unique session ID
            host_id: Host identifier
            stream_url: Stream URL

        Returns:
            Session data dict
        """
        db = await self._get_db()
        now = datetime.now(timezone.utc)

        session_data = {
            "session_id": session_id,
            "host_id": host_id,
            "stream_url": stream_url,
            "active_sku": "",
            "started_at": now,
            "status": "LIVE",
        }

        await db.collection("sessions").document(session_id).set(session_data)
        return session_data

    async def update_active_product(self, session_id: str, sku: str) -> None:
        """Update the currently showcased product for a session."""
        db = await self._get_db()
        await db.collection("sessions").document(session_id).update({
            "active_sku": sku,
        })

    async def end_session(self, session_id: str) -> None:
        """Mark a session as ended."""
        db = await self._get_db()
        await db.collection("sessions").document(session_id).update({
            "status": "ENDED",
            "ended_at": datetime.now(timezone.utc),
        })

    # -----------------------------------------------------------------------
    # Order Operations
    # -----------------------------------------------------------------------

    async def create_order(
        self,
        order_id: str,
        session_id: str,
        viewer_id: str,
        sku: str,
        color: str,
        size: str,
    ) -> Dict[str, Any]:
        """
        Create a reservation order.

        Args:
            order_id: Unique order ID
            session_id: Live session ID
            viewer_id: Viewer identifier
            sku: Product SKU
            color: Selected color
            size: Selected size

        Returns:
            Order data dict with checkout URL
        """
        db = await self._get_db()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=10)

        order_data = {
            "order_id": order_id,
            "session_id": session_id,
            "viewer_id": viewer_id,
            "sku": sku,
            "color": color,
            "size": size,
            "status": "RESERVED",
            "reserved_at": now,
            "expires_at": expires_at,
            "checkout_url": f"/checkout/{order_id}",
        }

        await db.collection("orders").document(order_id).set(order_data)
        return order_data

    async def update_order_status(self, order_id: str, status: str) -> None:
        """
        Update an order's status.

        Args:
            order_id: Order ID
            status: New status (RESERVED, PAID, EXPIRED, CANCELLED)
        """
        db = await self._get_db()
        await db.collection("orders").document(order_id).update({
            "status": status,
        })

    async def get_expired_orders(self) -> List[Dict[str, Any]]:
        """Get all expired reservation orders that need to be released."""
        db = await self._get_db()
        now = datetime.now(timezone.utc)

        expired = []
        query = (
            db.collection("orders")
            .where("status", "==", "RESERVED")
            .where("expires_at", "<", now)
        )

        async for doc in query.stream():
            expired.append(doc.to_dict())

        return expired

    async def release_expired_reservations(self) -> int:
        """
        Release all expired reservations and restore stock.

        Returns:
            Number of reservations released
        """
        expired_orders = await self.get_expired_orders()
        released = 0

        for order in expired_orders:
            try:
                # Restore stock
                sku = order.get("sku", "")
                color = order.get("color", "")
                size = order.get("size", "")

                if sku and color and size:
                    db = await self._get_db()
                    variant_key = f"{color.lower()}_{size}"
                    product_ref = db.collection("products").document(sku)

                    from google.cloud import firestore

                    @firestore.async_transactional
                    async def restore(transaction):
                        doc = await product_ref.get(transaction=transaction)
                        if doc.exists:
                            data = doc.to_dict()
                            stock = data.get("stock", {})
                            stock[variant_key] = stock.get(variant_key, 0) + 1
                            transaction.update(product_ref, {"stock": stock})

                    transaction = db.transaction()
                    await restore(transaction)

                # Mark order as expired
                await self.update_order_status(order["order_id"], "EXPIRED")
                released += 1

            except Exception as e:
                logger.error(f"Error releasing reservation {order.get('order_id')}: {e}")

        if released > 0:
            logger.info(f"Released {released} expired reservations")

        return released