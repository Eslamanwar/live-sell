/**
 * LiveShop Viewer — Stream & WebSocket Connection
 *
 * Handles:
 * - WebSocket connection to the LiveShop server
 * - Receiving product card updates
 * - Sending chat messages
 * - Stream video playback
 */

// Configuration
const WS_URL = window.location.protocol === 'https:'
    ? `wss://${window.location.hostname}:8001`
    : `ws://${window.location.hostname}:8001`;

const SESSION_ID = new URLSearchParams(window.location.search).get('session') || 'demo';
const VIEWER_ID = 'viewer_' + Math.random().toString(36).substr(2, 8);

let ws = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 10;

/**
 * Connect to the WebSocket server.
 */
function connectWebSocket() {
    const wsUrl = `${WS_URL}/viewer/${SESSION_ID}/${VIEWER_ID}`;
    console.log(`Connecting to WebSocket: ${wsUrl}`);

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connected');
        reconnectAttempts = 0;
        addChatMessage('system', 'Connected to LiveShop! 🛍️');
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
        }
    };

    ws.onclose = (event) => {
        console.log('WebSocket disconnected:', event.code, event.reason);

        if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
            console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttempts})`);
            setTimeout(connectWebSocket, delay);
        } else {
            addChatMessage('system', 'Connection lost. Please refresh the page.');
        }
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
}

/**
 * Handle incoming WebSocket messages.
 */
function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'connected':
            updateViewerCount(data.viewer_count);
            break;

        case 'product_card_update':
            updateProductCard(data.product);
            break;

        case 'stock_update':
            updateStockBadge(data.product);
            break;

        case 'private_message':
            handlePrivateMessage(data);
            break;

        case 'chat_response':
            addChatMessage('ai', data.content);
            break;

        case 'viewer_count':
            updateViewerCount(data.count);
            break;

        case 'stream_ended':
            handleStreamEnded();
            break;

        case 'pong':
            // Heartbeat response
            break;

        default:
            console.log('Unknown message type:', data.type);
    }
}

/**
 * Send a chat message.
 */
function sendMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();

    if (!message) return;

    // Display the message locally
    addChatMessage('user', message, VIEWER_ID);

    // Send via WebSocket
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'chat',
            content: message,
            viewer_id: VIEWER_ID,
            session_id: SESSION_ID,
        }));
    }

    input.value = '';
    input.focus();
}

/**
 * Handle the buy button click.
 */
function handleBuy() {
    const product = window.currentProduct;
    if (!product) return;

    const selectedColor = window.selectedColor || product.colors[0];
    const selectedSize = window.selectedSize || product.sizes[0];

    // Send buy intent via WebSocket
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'buy',
            sku: product.sku,
            color: selectedColor,
            size: selectedSize,
            viewer_id: VIEWER_ID,
            session_id: SESSION_ID,
        }));
    }

    addChatMessage('user', `I'd like to buy the ${product.name} in ${selectedColor}, size ${selectedSize}`, VIEWER_ID);

    // Disable button temporarily
    const btn = document.getElementById('buyBtn');
    btn.disabled = true;
    btn.textContent = 'RESERVING...';

    setTimeout(() => {
        btn.disabled = false;
        btn.textContent = 'ADD TO CART';
    }, 5000);
}

/**
 * Add a message to the chat.
 */
function addChatMessage(type, content, authorName) {
    const container = document.getElementById('chatMessages');

    const msgEl = document.createElement('div');
    msgEl.className = 'chat-message';

    let authorClass = '';
    let displayName = '';

    switch (type) {
        case 'user':
            authorClass = 'user';
            displayName = authorName || 'You';
            break;
        case 'ai':
            authorClass = 'ai';
            displayName = '🤖 LiveShop AI';
            break;
        case 'system':
            authorClass = '';
            displayName = '📢';
            break;
    }

    msgEl.innerHTML = `
        <span class="author ${authorClass}">${displayName}:</span>
        <span class="content">${escapeHtml(content)}</span>
    `;

    container.appendChild(msgEl);
    container.scrollTop = container.scrollHeight;
}

/**
 * Update the viewer count display.
 */
function updateViewerCount(count) {
    document.getElementById('viewerCount').textContent = `${count} watching`;
}

/**
 * Handle private messages (checkout URLs, etc.).
 */
function handlePrivateMessage(data) {
    if (data.checkout_url) {
        addChatMessage('ai', `✅ Item reserved! Complete your purchase: ${data.checkout_url}`);
    } else if (data.content) {
        addChatMessage('ai', data.content);
    }
}

/**
 * Handle stream ended event.
 */
function handleStreamEnded() {
    addChatMessage('system', 'The live stream has ended. Thanks for watching! 🎉');
    document.getElementById('streamPlaceholder').textContent = '📺 Stream ended';
    document.getElementById('streamPlaceholder').style.display = 'block';

    const productCard = document.getElementById('productCard');
    productCard.classList.remove('visible');
}

/**
 * Escape HTML to prevent XSS.
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Start heartbeat
setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
    }
}, 30000);

// Connect on page load
document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
});