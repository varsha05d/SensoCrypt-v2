# SensoCrypt Voice Detection Service

Standalone AI-vs-human voice detection API, kept separate from the main SensoCrypt v2
backend because the model needs more RAM than a free-tier backend host can spare (see
`detector.py`'s docstring for the full reasoning and the model-selection tradeoffs).

Deployed to Google Cloud Run (`gcloud run deploy --source=.` from this directory), not
via this repo's own git history -- Cloud Run builds and deploys directly from source, so
there's no CI/CD hook here to keep in sync. Scale-to-zero (`min-instances=0`), so the
first request after idle time can take longer while a container cold-starts.

## API

`POST /detect`, multipart form field `file` (a 16-bit PCM WAV clip), header
`X-API-Key: <the VOICE_SERVICE_API_KEY value set as an env var on the Cloud Run service>`.

Response: `{"label": "human" | "ai_generated", "confidence": 0.0-1.0}`

`GET /health` — no auth required, for uptime checks.

## Redeploying

```
gcloud run deploy sensocrypt-voice-detection \
  --source=. \
  --project=<gcp-project-id> \
  --region=us-central1 \
  --memory=2Gi \
  --cpu=2 \
  --startup-probe="tcpSocket.port=8080,timeoutSeconds=10,periodSeconds=10,failureThreshold=90" \
  --set-env-vars="VOICE_SERVICE_API_KEY=<key>" \
  --allow-unauthenticated
```

The extended startup probe matters: the container itself starts in well under a second,
but the first pull of this image (torch + scipy + numpy, a couple GB) to a brand-new
Cloud Run node can take longer than the default ~4 minute probe budget. A failed first
deploy attempt that later shows the container actually running (check Cloud Logging for
"Uvicorn running on http://0.0.0.0:8080") just needs a plain redeploy -- the image is
already built and pushed, so the retry pulls faster.
