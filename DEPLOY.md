# LiveShop Agent — Deployment Guide

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Rilo (Kubernetes)                       │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  live-shop Pod                                        │  │
│  │  ├── ACP Server (FastAPI :8000)   ← Agentex tasks    │  │
│  │  ├── WebSocket Viewer Server (:8001) ← viewer chat   │  │
│  │  └── Frame Ingest Server (:8002)  ← host camera      │  │
│  └──────────────────────────────────────────────────────┘  │
│                        │ Temporal signals                    │
└────────────────────────┼────────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────────────┐
│         Google Cloud   │                                     │
│                        ▼                                     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Cloud Run — Temporal Worker                          │  │
│  │  ├── LiveShopWorkflow activities                      │  │
│  │  ├── Gemini Live API (stream ingestion)               │  │
│  │  └── Google ADK agent (tool calling)                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌──────────────┐  ┌────────────────┐  ┌───────────────┐  │
│  │  Firestore   │  │ Secret Manager │  │  Gemini API   │  │
│  │  (inventory) │  │  (credentials) │  │  (AI models)  │  │
│  └──────────────┘  └────────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. GCP Prerequisites

### Enable APIs
```bash
gcloud services enable \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com
```

### Create Service Account
```bash
gcloud iam service-accounts create live-shop-agent \
  --display-name="LiveShop Agent"

export SA="live-shop-agent@project-de684656-5ea1-44f2-ab7.iam.gserviceaccount.com"

# Grant required roles
gcloud projects add-iam-policy-binding project-de684656-5ea1-44f2-ab7 \
  --member="serviceAccount:$SA" \
  --role="roles/datastore.user"

gcloud projects add-iam-policy-binding project-de684656-5ea1-44f2-ab7 \
  --member="serviceAccount:$SA" \
  --role="roles/secretmanager.secretAccessor"
```

> **No JSON key needed.** The service account is attached directly to the Cloud Run service.
> Firestore uses Application Default Credentials (ADC) — no `GOOGLE_APPLICATION_CREDENTIALS` or key file required.

---

## 2. Firestore Setup

### Create Database
```bash
gcloud firestore databases create \
  --location=me-central1 \
  --type=firestore-native
```

### Seed Inventory
```bash
cd agents/live-shop
export PROJECT_ID=project-de684656-5ea1-44f2-ab7

# Authenticate with your user account (ADC) for local seeding
gcloud auth application-default login

python db/seed_inventory.py
```

This creates the following collections:
- `/products/{sku}` — product catalog with variants and stock
- `/sessions/{session_id}` — live stream sessions
- `/orders/{order_id}` — reservations and purchases

---

## 3. Secret Manager

Only the Gemini API key needs to be stored — Firestore auth is handled by the attached service account.

```bash
# Create secret
gcloud secrets create live-shop-secrets --replication-policy=automatic

# Add Gemini API key (only secret needed)
echo -n "YOUR_GEMINI_API_KEY" | \
  gcloud secrets versions add live-shop-secrets \
  --data-file=-
```

For Kubernetes (Rilo), create the secret:
```bash
kubectl create secret generic live-shop \
  --namespace=agentex \
  --from-literal=GEMINI_API_KEY=YOUR_GEMINI_API_KEY \
  --from-literal=AGENT_API_KEY=YOUR_AGENT_API_KEY \
  --from-literal=AGENTEX_BASE_URL=https://hub-api.rilo.dev
```

---

## 4. Build & Push Docker Image


gcloud auth configure-docker me-central1-docker.pkg.dev


gcloud artifacts repositories create rilo \
  --repository-format=docker \
  --location=me-central1 \
  --description="Docker repo for Cloud Run images"


docker push me-central1-docker.pkg.dev/project-de684656-5ea1-44f2-ab7/rilo/agents-live-shop:v0.1


```bash
# Set your registry
export REGISTRY=me-central1-docker.pkg.dev/project-de684656-5ea1-44f2-ab7/rilo/agents-live-shop
export TAG=v0.1

# Build from repo root (context includes agentex lib)
docker build \
  -f agents/live-shop/Dockerfile \
  -t $REGISTRY:$TAG \
  .

docker push $REGISTRY:$TAG
```

---

## 5. Expose Temporal gRPC via nginx Ingress

The `temporal-ingress` in [agentex/charts/templates/ingress.yaml](../../agentex/charts/templates/ingress.yaml) handles this with the `backend-protocol: GRPC` annotation — nginx terminates TLS and forwards as HTTP/2 gRPC to the backend.

Add a DNS A record:
```bash
kubectl get svc ingress-nginx-controller -n ingress-nginx
# EXTERNAL-IP → add DNS A record: temporal.rilo.dev → <EXTERNAL-IP>
```

The Temporal address for Cloud Run is: `temporal.rilo.dev:443`

> **Why 443 not 7233?** nginx terminates TLS on port 443 and proxies gRPC to `agentex-temporal-frontend:7233` internally. The Temporal Python SDK connects via `temporal.rilo.dev:443` with TLS enabled.

---

## 6. Deploy Temporal Worker to Cloud Run

The Temporal worker runs on Cloud Run for scalable, GCP-native execution.

```bash
gcloud run deploy live-shop-worker \
  --image=$REGISTRY:$TAG \
  --region=me-central1 \
  --service-account=$SA \
  --set-env-vars="SERVICE_TYPE=worker" \
  --set-env-vars="TEMPORAL_ADDRESS=temporal.rilo.dev:443" \
  --set-env-vars="TEMPORAL_TASK_QUEUE=live-shop-queue" \
  --set-env-vars="GEMINI_MODEL=gemini-2.0-flash" \
  --set-env-vars="GEMINI_LIVE_MODEL=gemini-2.0-flash-live" \
  --set-env-vars="PROJECT_ID=project-de684656-5ea1-44f2-ab7" \
  --set-env-vars="FIRESTORE_DATABASE=(default)" \
  --set-secrets="GEMINI_API_KEY=live-shop-secrets:latest" \
  --port=8080 \
  --min-instances=1 \
  --max-instances=1 \
  --memory=2Gi \
  --cpu=2 \
  --no-allow-unauthenticated \
  --command="python" \
  --args="project/run_worker.py"
```

> **Why Cloud Run for the worker?**
> Gemini Live API streams require persistent connections. Cloud Run's min-instances=1 keeps the worker warm, while max-instances handles load during popular streams.

---

## 6. Deploy ACP to Rilo (Kubernetes)

The ACP (Agent Control Plane) server runs on Rilo alongside other Agentex agents.

```bash
# From repo root
helm upgrade --install live-shop \
  agents/live-shop/chart/live-shop \
  --namespace agentex \
  --values agents/live-shop/chart/live-shop/values.qa.yaml \
  --set service.image.tag=$TAG
```

Verify the pod is running:
```bash
kubectl get pods -n agentex -l app=live-shop
kubectl logs -n agentex -l app=live-shop --tail=50
```

---

## 7. Agentex Integration

### Register the Agent

Once the ACP pod is running, register it with Agentex so it appears in the hub:

```bash
curl -X POST https://hub-api.rilo.dev/agents \
  -H "Authorization: Bearer YOUR_AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "live-shop",
    "description": "AI-powered live commerce agent",
    "acp_url": "http://live-shop.agentex.svc.cluster.local:8000",
    "task_queue": "live-shop-queue"
  }'
```

### Start a Live Session via Agentex

```bash
curl -X POST https://hub-api.rilo.dev/agents/live-shop/tasks \
  -H "Authorization: Bearer YOUR_AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "session_id": "stream-001",
      "host_name": "Sara",
      "stream_url": "ws://live-shop.dhhmena.com/ingest/stream-001"
    }
  }'
```

The agent is also accessible through the Agentex UI at:
```
https://hub.rilo.dev/ui?agent_name=live-shop
```

---

## 8. Viewer & Host UI

The viewer and host UIs are static files in `ui/`. Serve them from any static host or add them as a route in agentex-ui.

### Quick local test
```bash
# Serve viewer UI
python -m http.server 3000 --directory agents/live-shop/ui/viewer

# Open http://localhost:3000
# Set WebSocket URL to ws://localhost:8001
```

### In Production
Set the following in the viewer HTML before deploying:
```javascript
// ui/viewer/stream.js
const WS_HOST = "wss://live-shop.dhhmena.com";
const INGEST_WS  = `${WS_HOST}/ingest/${SESSION_ID}`;   // :8002 — host frames
const VIEWER_WS  = `${WS_HOST}/ws/${SESSION_ID}`;        // :8001 — product cards
```

---

## 9. Environment Variables Reference

| Variable | Where | Description |
|---|---|---|
| `GEMINI_API_KEY` | Worker + ACP | Google AI Studio API key |
| `GEMINI_MODEL` | Worker + ACP | `gemini-2.0-flash` (ADK tool calling) |
| `GEMINI_LIVE_MODEL` | Worker | `gemini-2.0-flash-live` (stream ingestion) |
| `PROJECT_ID` | Worker | GCP project ID |
| `FIRESTORE_DATABASE` | Worker | `(default)` |
| `TEMPORAL_ADDRESS` | Worker + ACP | `temporal.rilo.dev:443` |
| `TEMPORAL_TASK_QUEUE` | Worker + ACP | `live-shop-queue` |
| `AGENTEX_BASE_URL` | ACP | `https://hub-api.rilo.dev` |
| `AGENT_API_KEY` | ACP | Agentex registration key |
| `WEBSOCKET_PORT` | ACP | `8001` (viewer WebSocket) |
| `INGEST_PORT` | ACP | `8002` (host frame ingest) |

---

## 10. Verify Deployment

```bash
# 1. Check ACP health
curl https://live-shop.dhhmena.com/healthz

# 2. Check worker is polling Temporal
kubectl logs -n agentex -l app=live-shop-worker --tail=20
# Expected: "Starting Temporal worker on queue: live-shop-queue"

# 3. Start a demo session (uses mock inventory + mock Gemini)
curl -X POST https://live-shop.dhhmena.com/tasks \
  -d '{"session_id": "demo-001", "demo_mode": true}'

# 4. Open viewer UI and confirm product card appears
```
