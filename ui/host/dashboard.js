/**
 * LiveShop Host Dashboard — Stream Controls & Monitoring
 *
 * Architecture (hackathon-safe, no WebRTC):
 *   Host Browser
 *     ├── getUserMedia() → <video> element
 *     ├── canvas.toBlob() captures frame every 2s
 *     ├── Sends JPEG as binary WebSocket message to /ingest/{session_id}
 *     └── Cloud Run receives bytes → Gemini Live API session
 */

let isStreaming = false;
let mediaStream = null;
let frameInterval = null;
let ingestWs = null;
const SESSION_ID = 'session_' + Date.now();
const HOST_ID = 'host_' + Math.random().toString(36).substr(2, 8);

// Frame capture settings
const FRAME_INTERVAL_MS = 2000; // Capture a frame every 2 seconds
const FRAME_QUALITY = 0.7;      // JPEG quality (0-1)

// Ingest WebSocket endpoint (frame ingestion server on port 8002)
const INGEST_WS_URL = window.location.protocol === 'https:'
    ? `wss://${window.location.hostname}:8002`
    : `ws://${window.location.hostname}:8002`;

/**
 * Toggle the live stream on/off.
 */
async function toggleStream() {
    if (isStreaming) {
        endStream();
    } else {
        await startStream();
    }
}

/**
 * Start the live stream — request camera access and begin frame capture.
 */
async function startStream() {
    try {
        // 1. Request camera access
        mediaStream = await navigator.mediaDevices.getUserMedia({
            video: { width: 1280, height: 720, facingMode: 'environment' },
            audio: true,
        });

        // Show video preview locally
        const video = document.getElementById('cameraVideo');
        video.srcObject = mediaStream;
        video.style.display = 'block';
        document.getElementById('cameraPlaceholder').style.display = 'none';

        addDetectionLog('Camera acquired — connecting to frame ingestion server...');

        // 2. Connect to the frame ingestion WebSocket server
        const wsUrl = `${INGEST_WS_URL}/ingest/${SESSION_ID}`;
        ingestWs = new WebSocket(wsUrl);

        ingestWs.onopen = () => {
            addDetectionLog('Connected to frame ingestion server → Gemini Live API');
            console.log('WebSocket connected to ingest server:', wsUrl);

            // 3. Start capturing frames via canvas.toBlob()
            startFrameCapture(video);
        };

        ingestWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'detection') {
                    handleDetection(data);
                }
            } catch (e) {
                console.error('Failed to parse ingest response:', e);
            }
        };

        ingestWs.onclose = (event) => {
            console.log('Ingest WebSocket closed:', event.code, event.reason);
            if (isStreaming) {
                addDetectionLog('⚠️ Connection to ingest server lost — retrying...');
                // Auto-reconnect after 3 seconds
                setTimeout(() => {
                    if (isStreaming) {
                        const video = document.getElementById('cameraVideo');
                        ingestWs = new WebSocket(wsUrl);
                        ingestWs.onopen = () => {
                            addDetectionLog('Reconnected to ingest server');
                            startFrameCapture(video);
                        };
                    }
                }, 3000);
            }
        };

        ingestWs.onerror = (error) => {
            console.warn('Ingest WebSocket error (demo mode):', error);
            addDetectionLog('Demo mode — ingest server not available, using simulated detection');
        };

        // Update UI
        isStreaming = true;
        document.getElementById('goLiveBtn').style.display = 'none';
        document.getElementById('endStreamBtn').style.display = 'inline-block';
        document.getElementById('streamStatus').innerHTML = '🔴 <strong>LIVE</strong>';
        document.getElementById('streamStatus').style.color = '#ef4444';

        addDetectionLog('Stream started — AI product detection active');
        console.log('Stream started, session:', SESSION_ID);

    } catch (error) {
        console.error('Failed to start stream:', error);
        alert('Could not access camera. Please grant camera permissions.');
    }
}

/**
 * Start capturing video frames via canvas.toBlob() and sending over WebSocket.
 *
 * @param {HTMLVideoElement} video - The video element to capture from
 */
function startFrameCapture(video) {
    // Stop any existing capture interval
    if (frameInterval) {
        clearInterval(frameInterval);
    }

    // Create an offscreen canvas for frame capture
    const canvas = document.createElement('canvas');
    canvas.width = 640;  // Downscale for bandwidth
    canvas.height = 360;
    const ctx = canvas.getContext('2d');

    let frameCount = 0;

    frameInterval = setInterval(() => {
        if (!isStreaming || !ingestWs || ingestWs.readyState !== WebSocket.OPEN) {
            return;
        }

        // Draw the current video frame onto the canvas
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

        // Convert to JPEG blob and send as binary WebSocket message
        canvas.toBlob((blob) => {
            if (blob && ingestWs && ingestWs.readyState === WebSocket.OPEN) {
                frameCount++;
                ingestWs.send(blob);

                if (frameCount % 5 === 0) {
                    addDetectionLog(`Frame #${frameCount} sent (${(blob.size / 1024).toFixed(1)} KB)`);
                }
            }
        }, 'image/jpeg', FRAME_QUALITY);

    }, FRAME_INTERVAL_MS);

    addDetectionLog(`Frame capture started (every ${FRAME_INTERVAL_MS / 1000}s at ${canvas.width}x${canvas.height})`);
}

/**
 * Handle a product detection result from the ingest server.
 *
 * @param {Object} data - Detection result from Gemini Live API
 */
function handleDetection(data) {
    if (data.visual_description) {
        addDetectionLog(`🎯 Product detected: ${data.visual_description}`);

        // Update active product display
        const activeProduct = document.getElementById('activeProduct');
        activeProduct.innerHTML = `
            <div class="product-name">${data.visual_description}</div>
            <div class="product-sku">Confidence: ${((data.confidence || 0) * 100).toFixed(0)}%</div>
            <div class="product-price">🤖 Auto-detected by Gemini</div>
        `;

        // Update stats
        const statProducts = document.getElementById('statProducts');
        statProducts.textContent = parseInt(statProducts.textContent) + 1;
    } else {
        addDetectionLog('👀 No product detected in frame');
    }
}

/**
 * End the live stream.
 */
function endStream() {
    // Stop frame capture
    if (frameInterval) {
        clearInterval(frameInterval);
        frameInterval = null;
    }

    // Close ingest WebSocket
    if (ingestWs) {
        try {
            ingestWs.send(JSON.stringify({ type: 'stop' }));
        } catch (e) {
            // Ignore send errors on close
        }
        ingestWs.close();
        ingestWs = null;
    }

    // Stop camera
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }

    const video = document.getElementById('cameraVideo');
    video.srcObject = null;
    video.style.display = 'none';
    document.getElementById('cameraPlaceholder').style.display = 'block';
    document.getElementById('cameraPlaceholder').textContent = '📺 Stream ended';

    isStreaming = false;
    document.getElementById('goLiveBtn').style.display = 'inline-block';
    document.getElementById('endStreamBtn').style.display = 'none';
    document.getElementById('streamStatus').innerHTML = '⚪ Offline';
    document.getElementById('streamStatus').style.color = '#888';

    addDetectionLog('Stream ended');
}

/**
 * Manually pin a product (override auto-detection).
 */
function pinProduct() {
    const select = document.getElementById('manualProductSelect');
    if (select.value) {
        manualSelectProduct();
    } else {
        alert('Select a product from the dropdown first');
    }
}

/**
 * Set a product manually from the dropdown.
 */
function manualSelectProduct() {
    const select = document.getElementById('manualProductSelect');
    const sku = select.value;

    if (!sku) return;

    const productName = select.options[select.selectedIndex].text;

    // Update active product display
    const activeProduct = document.getElementById('activeProduct');
    activeProduct.innerHTML = `
        <div class="product-name">${productName.split(' — ')[1] || productName}</div>
        <div class="product-sku">SKU: ${sku}</div>
        <div class="product-price">📌 Manually pinned</div>
    `;

    // Update stats
    const statProducts = document.getElementById('statProducts');
    statProducts.textContent = parseInt(statProducts.textContent) + 1;

    addDetectionLog(`Product manually pinned: ${sku}`);
}

/**
 * Add an entry to the AI detection log.
 */
function addDetectionLog(message) {
    const log = document.getElementById('detectionLog');
    const now = new Date().toLocaleTimeString();

    const item = document.createElement('div');
    item.className = 'detection-item';
    item.innerHTML = `<span class="timestamp">${now}</span>${message}`;

    // Remove placeholder if present
    if (log.children.length === 1 && log.children[0].style.color === 'rgb(85, 85, 85)') {
        log.innerHTML = '';
    }

    log.insertBefore(item, log.firstChild);

    // Keep only last 50 entries
    while (log.children.length > 50) {
        log.removeChild(log.lastChild);
    }
}

/**
 * Add an order to the recent orders list.
 */
function addOrder(orderId, productName, color, size, status) {
    const list = document.getElementById('orderList');

    // Remove placeholder
    if (list.children.length === 1 && list.children[0].style.color) {
        list.innerHTML = '';
    }

    const item = document.createElement('div');
    item.className = 'order-item';
    item.innerHTML = `
        <div>
            <strong>${orderId}</strong><br>
            <span style="color: #888;">${productName} (${color}/${size})</span>
        </div>
        <span class="order-status ${status.toLowerCase()}">${status}</span>
    `;

    list.insertBefore(item, list.firstChild);

    // Keep only last 20 orders
    while (list.children.length > 20) {
        list.removeChild(list.lastChild);
    }

    // Update reservation count
    const count = document.getElementById('reservationCount');
    count.textContent = parseInt(count.textContent) + 1;

    const statReservations = document.getElementById('statReservations');
    statReservations.textContent = parseInt(statReservations.textContent) + 1;
}

/**
 * Update live stats from server.
 */
function updateStats(stats) {
    if (stats.viewers !== undefined) {
        document.getElementById('viewerCount').textContent = stats.viewers;
        document.getElementById('statViewers').textContent = stats.viewers;
    }
    if (stats.products !== undefined) {
        document.getElementById('statProducts').textContent = stats.products;
    }
    if (stats.reservations !== undefined) {
        document.getElementById('statReservations').textContent = stats.reservations;
    }
    if (stats.questions !== undefined) {
        document.getElementById('statQuestions').textContent = stats.questions;
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('LiveShop Host Dashboard loaded');
    console.log('Session ID:', SESSION_ID);
    console.log('Ingest server:', INGEST_WS_URL);
});