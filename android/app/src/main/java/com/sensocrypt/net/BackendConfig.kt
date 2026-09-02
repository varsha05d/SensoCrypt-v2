package com.sensocrypt.net

/**
 * Backend location. Swap this one block when moving between a local dev backend and the
 * deployed one -- everything else (AuthApi, SessionApi, SignalSocket, TelemetrySocket)
 * builds its URLs from these two constants alone.
 *
 * Local dev (same WiFi as the Mac running the backend): use the Mac's LAN IP, not
 * 127.0.0.1, so a phone can reach it (check with `ipconfig getifaddr en0`), and http/ws
 * since there's no TLS locally. This also needs a cleartext exception in
 * res/xml/network_security_config.xml for that IP.
 *
 * Deployed (Render, or any real host): use its HTTPS domain and https/wss -- no
 * network_security_config cleartext exception needed, and it works from any network, not
 * just the same LAN.
 */
// voice-detection branch's own Render service -- separate from main's
// sensocrypt-v2-backend.onrender.com, so this in-progress feature never touches the
// working production backend. See render.yaml's sensocrypt-v2-backend-voicedetect.
// Render appended its own disambiguating suffix to the hostname (the plain name was
// unavailable) -- this is the actual assigned URL, not the predictable one.
const val BACKEND_HOST = "sensocrypt-v2-backend-voicedetect-t1lg.onrender.com"
const val BACKEND_HTTP_SCHEME = "https"
const val BACKEND_WS_SCHEME = "wss"

// Local dev, if needed again later:
// const val BACKEND_HOST = "192.168.1.3:8001"
// const val BACKEND_HTTP_SCHEME = "http"
// const val BACKEND_WS_SCHEME = "ws"

// Production backend (main branch only -- do not point this branch at it):
// const val BACKEND_HOST = "sensocrypt-v2-backend.onrender.com"
// const val BACKEND_HTTP_SCHEME = "https"
// const val BACKEND_WS_SCHEME = "wss"
