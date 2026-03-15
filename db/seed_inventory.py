"""Seed demo inventory data into Firestore for LiveShop.

Usage:
    python db/seed_inventory.py --project YOUR_PROJECT_ID

Or set PROJECT_ID environment variable:
    PROJECT_ID=your-project python db/seed_inventory.py
"""
import argparse
import asyncio
import os
import sys


# Demo product catalog
DEMO_PRODUCTS = [
    {
        "sku": "DR-4421",
        "name": "Floral Wrap Dress",
        "description": "Beautiful floral wrap dress in midi length. Made from 95% viscose and 5% elastane. Very light, breathable, and perfect for summer occasions.",
        "base_price": 89.0,
        "tags": ["dress", "floral", "wrap", "midi", "summer", "viscose", "light", "blue", "red", "white"],
        "variants": {
            "colors": ["Blue", "Red", "White"],
            "sizes": ["XS", "S", "M", "L"],
        },
        "stock": {
            "blue_XS": 5, "blue_S": 8, "blue_M": 12, "blue_L": 3,
            "red_XS": 2, "red_S": 3, "red_M": 0, "red_L": 1,
            "white_XS": 6, "white_S": 10, "white_M": 7, "white_L": 4,
        },
        "images": [
            "gs://live-shop-assets/products/DR-4421/blue_front.jpg",
            "gs://live-shop-assets/products/DR-4421/red_front.jpg",
            "gs://live-shop-assets/products/DR-4421/white_front.jpg",
        ],
    },
    {
        "sku": "BG-7782",
        "name": "Leather Crossbody Bag",
        "description": "Premium genuine leather crossbody bag with adjustable strap. Features multiple compartments, magnetic closure, and gold-tone hardware. Perfect for everyday use.",
        "base_price": 149.0,
        "tags": ["bag", "leather", "crossbody", "premium", "everyday", "black", "brown", "tan", "gold"],
        "variants": {
            "colors": ["Black", "Brown", "Tan"],
            "sizes": ["One Size"],
        },
        "stock": {
            "black_One Size": 15, "brown_One Size": 8, "tan_One Size": 3,
        },
        "images": [
            "gs://live-shop-assets/products/BG-7782/black_front.jpg",
            "gs://live-shop-assets/products/BG-7782/brown_front.jpg",
            "gs://live-shop-assets/products/BG-7782/tan_front.jpg",
        ],
    },
    {
        "sku": "SH-3310",
        "name": "Strappy Block Heel Sandals",
        "description": "Elegant strappy sandals with a comfortable 7cm block heel. Faux leather upper with cushioned insole. Perfect for summer evenings and special occasions.",
        "base_price": 69.0,
        "tags": ["shoes", "sandals", "strappy", "heel", "block", "summer", "elegant", "gold", "silver", "black"],
        "variants": {
            "colors": ["Gold", "Silver", "Black"],
            "sizes": ["36", "37", "38", "39", "40"],
        },
        "stock": {
            "gold_36": 4, "gold_37": 6, "gold_38": 8, "gold_39": 5, "gold_40": 2,
            "silver_36": 3, "silver_37": 5, "silver_38": 7, "silver_39": 4, "silver_40": 1,
            "black_36": 6, "black_37": 8, "black_38": 10, "black_39": 6, "black_40": 3,
        },
        "images": [
            "gs://live-shop-assets/products/SH-3310/gold_front.jpg",
            "gs://live-shop-assets/products/SH-3310/silver_front.jpg",
            "gs://live-shop-assets/products/SH-3310/black_front.jpg",
        ],
    },
    {
        "sku": "TP-5501",
        "name": "Silk Blend Blouse",
        "description": "Luxurious silk blend blouse with a relaxed fit. Features a subtle sheen, button-front closure, and rolled sleeves. Versatile for work or evening wear.",
        "base_price": 119.0,
        "tags": ["top", "blouse", "silk", "luxurious", "work", "evening", "ivory", "blush", "navy"],
        "variants": {
            "colors": ["Ivory", "Blush", "Navy"],
            "sizes": ["XS", "S", "M", "L", "XL"],
        },
        "stock": {
            "ivory_XS": 3, "ivory_S": 7, "ivory_M": 10, "ivory_L": 5, "ivory_XL": 2,
            "blush_XS": 4, "blush_S": 6, "blush_M": 8, "blush_L": 4, "blush_XL": 1,
            "navy_XS": 5, "navy_S": 9, "navy_M": 12, "navy_L": 6, "navy_XL": 3,
        },
        "images": [
            "gs://live-shop-assets/products/TP-5501/ivory_front.jpg",
            "gs://live-shop-assets/products/TP-5501/blush_front.jpg",
            "gs://live-shop-assets/products/TP-5501/navy_front.jpg",
        ],
    },
    {
        "sku": "JW-9903",
        "name": "Layered Gold Necklace Set",
        "description": "Set of 3 layered gold-plated necklaces. Includes a choker, pendant, and long chain. Hypoallergenic, tarnish-resistant. Can be worn together or separately.",
        "base_price": 45.0,
        "tags": ["jewelry", "necklace", "gold", "layered", "set", "choker", "pendant", "chain"],
        "variants": {
            "colors": ["Gold", "Silver", "Rose Gold"],
            "sizes": ["One Size"],
        },
        "stock": {
            "gold_One Size": 20, "silver_One Size": 15, "rose gold_One Size": 12,
        },
        "images": [
            "gs://live-shop-assets/products/JW-9903/gold_set.jpg",
            "gs://live-shop-assets/products/JW-9903/silver_set.jpg",
            "gs://live-shop-assets/products/JW-9903/rosegold_set.jpg",
        ],
    },
    {
        "sku": "AC-2204",
        "name": "Oversized Sunglasses",
        "description": "Trendy oversized cat-eye sunglasses with UV400 protection. Lightweight acetate frame with gradient lenses. Comes with a protective case.",
        "base_price": 35.0,
        "tags": ["accessories", "sunglasses", "oversized", "cat-eye", "uv", "trendy", "tortoise", "black", "pink"],
        "variants": {
            "colors": ["Tortoise", "Black", "Pink"],
            "sizes": ["One Size"],
        },
        "stock": {
            "tortoise_One Size": 25, "black_One Size": 30, "pink_One Size": 18,
        },
        "images": [
            "gs://live-shop-assets/products/AC-2204/tortoise.jpg",
            "gs://live-shop-assets/products/AC-2204/black.jpg",
            "gs://live-shop-assets/products/AC-2204/pink.jpg",
        ],
    },
]


async def seed_firestore(project_id: str) -> None:
    """
    Seed the Firestore database with product catalog data.
    Uses Application Default Credentials (ADC).

    Args:
        project_id: GCP project ID
    """
    from google.cloud import firestore

    # Uses ADC — run `gcloud auth application-default login` locally
    # or attach a service account when running on Cloud Run / GCE
    db = firestore.AsyncClient(project=project_id)

    print(f"Seeding Firestore in project: {project_id}")
    print(f"Products to seed: {len(DEMO_PRODUCTS)}")
    print("-" * 50)

    for product in DEMO_PRODUCTS:
        sku = product["sku"]
        doc_ref = db.collection("products").document(sku)

        # Flatten variants into top-level fields for ADK tool queries
        doc_data = {
            "sku": sku,
            "name": product["name"],
            "description": product["description"],
            "base_price": product["base_price"],
            "tags": product["tags"],
            "colors": product["variants"]["colors"],
            "sizes": product["variants"]["sizes"],
            "stock": product["stock"],
            "images": product.get("images", []),
        }

        # Check if product already exists
        existing = await doc_ref.get()
        if existing.exists:
            print(f"  ⚠️  {sku} ({product['name']}) already exists — updating")
        else:
            print(f"  ✅ {sku} ({product['name']}) — creating")

        await doc_ref.set(doc_data)

    print("-" * 50)
    print(f"✅ Seeded {len(DEMO_PRODUCTS)} products successfully!")
    print()
    print("Products:")
    for p in DEMO_PRODUCTS:
        total_stock = sum(p["stock"].values())
        print(f"  {p['sku']}: {p['name']} — AED {p['base_price']} ({total_stock} units)")


def main():
    """CLI entry point for seeding inventory."""
    parser = argparse.ArgumentParser(description="Seed LiveShop demo inventory into Firestore")
    parser.add_argument(
        "--project",
        type=str,
        default=os.getenv("PROJECT_ID", ""),
        help="GCP project ID (or set PROJECT_ID env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print products without writing to Firestore",
    )

    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — Products that would be seeded:")
        print("-" * 50)
        for p in DEMO_PRODUCTS:
            total_stock = sum(p["stock"].values())
            colors = p["variants"]["colors"]
            sizes = p["variants"]["sizes"]
            print(f"  {p['sku']}: {p['name']}")
            print(f"    Price: AED {p['base_price']}")
            print(f"    Colors: {', '.join(colors)}")
            print(f"    Sizes: {', '.join(sizes)}")
            print(f"    Total stock: {total_stock} units")
            print(f"    Tags: {', '.join(p['tags'])}")
            print()
        return

    if not args.project:
        print("ERROR: --project or PROJECT_ID env var is required")
        sys.exit(1)

    asyncio.run(seed_firestore(args.project))


if __name__ == "__main__":
    main()