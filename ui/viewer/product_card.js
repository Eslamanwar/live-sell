/**
 * LiveShop Viewer — Product Card Controller
 *
 * Handles:
 * - Rendering the floating product card
 * - Color and size selection
 * - Stock badge updates
 * - Buy button state
 */

// Current product state
window.currentProduct = null;
window.selectedColor = null;
window.selectedSize = null;

// Color name to CSS color mapping
const COLOR_MAP = {
    'Blue': '#3b82f6',
    'Red': '#ef4444',
    'White': '#f5f5f5',
    'Black': '#1a1a1a',
    'Brown': '#92400e',
    'Tan': '#d2b48c',
    'Gold': '#fbbf24',
    'Silver': '#9ca3af',
    'Ivory': '#fffff0',
    'Blush': '#fbb6ce',
    'Navy': '#1e3a5f',
    'Pink': '#ec4899',
    'Tortoise': '#8b6914',
    'Rose Gold': '#b76e79',
};

/**
 * Update the product card with new product data.
 */
function updateProductCard(product) {
    if (!product || !product.sku) {
        hideProductCard();
        return;
    }

    window.currentProduct = product;
    window.selectedColor = product.colors ? product.colors[0] : null;
    window.selectedSize = product.sizes ? product.sizes[0] : null;

    const card = document.getElementById('productCard');

    // Update product name and price
    document.getElementById('productName').textContent = product.name || '—';
    document.getElementById('productPrice').textContent = `AED ${product.price || product.base_price || 0}`;

    // Render color dots
    renderColors(product.colors || []);

    // Render size buttons
    renderSizes(product.sizes || []);

    // Update stock badge
    updateStockBadge(product);

    // Show the card
    card.classList.add('visible');
}

/**
 * Hide the product card.
 */
function hideProductCard() {
    document.getElementById('productCard').classList.remove('visible');
    window.currentProduct = null;
}

/**
 * Render color selection dots.
 */
function renderColors(colors) {
    const container = document.getElementById('productColors');
    container.innerHTML = '';

    colors.forEach((color, index) => {
        const dot = document.createElement('div');
        dot.className = `color-dot${index === 0 ? ' selected' : ''}`;
        dot.style.backgroundColor = COLOR_MAP[color] || color.toLowerCase();
        dot.title = color;
        dot.onclick = () => selectColor(color, dot);
        container.appendChild(dot);
    });
}

/**
 * Render size selection buttons.
 */
function renderSizes(sizes) {
    const container = document.getElementById('productSizes');
    container.innerHTML = '';

    sizes.forEach((size, index) => {
        const btn = document.createElement('button');
        btn.className = `size-btn${index === 0 ? ' selected' : ''}`;
        btn.textContent = size;
        btn.onclick = () => selectSize(size, btn);
        container.appendChild(btn);
    });
}

/**
 * Handle color selection.
 */
function selectColor(color, element) {
    window.selectedColor = color;

    // Update UI
    document.querySelectorAll('.color-dot').forEach(dot => dot.classList.remove('selected'));
    element.classList.add('selected');

    // Update stock badge for new variant
    updateStockForVariant();
}

/**
 * Handle size selection.
 */
function selectSize(size, element) {
    window.selectedSize = size;

    // Update UI
    document.querySelectorAll('.size-btn').forEach(btn => btn.classList.remove('selected'));
    element.classList.add('selected');

    // Update stock badge for new variant
    updateStockForVariant();
}

/**
 * Update stock badge for the currently selected variant.
 */
function updateStockForVariant() {
    const product = window.currentProduct;
    if (!product || !product.stock) return;

    const color = window.selectedColor;
    const size = window.selectedSize;

    if (color && size) {
        const variantKey = `${color.toLowerCase()}_${size}`;
        const qty = product.stock[variantKey] || 0;

        const badge = document.getElementById('stockBadge');
        const buyBtn = document.getElementById('buyBtn');

        if (qty <= 0) {
            badge.textContent = '🔴 Out of stock';
            badge.className = 'stock-badge out-of-stock';
            buyBtn.disabled = true;
            buyBtn.textContent = 'OUT OF STOCK';
        } else if (qty <= 3) {
            badge.textContent = `🟡 Only ${qty} left in ${color}!`;
            badge.className = 'stock-badge low-stock';
            buyBtn.disabled = false;
            buyBtn.textContent = 'ADD TO CART';
        } else {
            badge.textContent = `🟢 In stock (${qty} available)`;
            badge.className = 'stock-badge in-stock';
            buyBtn.disabled = false;
            buyBtn.textContent = 'ADD TO CART';
        }
    }
}

/**
 * Update the stock badge from a product update.
 */
function updateStockBadge(product) {
    if (!product || !product.stock) return;

    // Calculate total stock
    const totalStock = Object.values(product.stock).reduce((sum, qty) => sum + qty, 0);

    const badge = document.getElementById('stockBadge');
    const buyBtn = document.getElementById('buyBtn');

    if (totalStock <= 0) {
        badge.textContent = '🔴 Out of stock';
        badge.className = 'stock-badge out-of-stock';
        buyBtn.disabled = true;
        buyBtn.textContent = 'OUT OF STOCK';
    } else if (totalStock <= 5) {
        badge.textContent = `🟡 Only ${totalStock} left!`;
        badge.className = 'stock-badge low-stock';
        buyBtn.disabled = false;
        buyBtn.textContent = 'ADD TO CART';
    } else {
        badge.textContent = `🟢 In stock`;
        badge.className = 'stock-badge in-stock';
        buyBtn.disabled = false;
        buyBtn.textContent = 'ADD TO CART';
    }

    // If we have a selected variant, show specific stock
    if (window.selectedColor && window.selectedSize) {
        updateStockForVariant();
    }
}