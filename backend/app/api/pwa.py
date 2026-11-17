"""PWA support: manifest and service worker.

A PWA is the deliberate first step before native shells: it validates the product
on real phones/tablets, supports camera capture via a file input, installs to the
home screen, and needs no app-store release cycle.

The service worker caches only the app shell. It never caches API responses, so
per-user data cannot leak between accounts on a shared device.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

router = APIRouter(tags=["pwa"])

MANIFEST = {
    "name": "MistakeBook AI 错题本",
    "short_name": "错题本",
    "description": "AI 家庭错题本：拍照上传、自动切题、推荐错题、由你确认。",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#f5f7fb",
    "theme_color": "#2563eb",
    "lang": "zh-CN",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {
            "src": "/icons/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
    "shortcuts": [
        {"name": "拍照上传", "url": "/upload?camera=1"},
        {"name": "错题库", "url": "/mistakes"},
    ],
}

SERVICE_WORKER = """\
/* MistakeBook AI service worker.
 * Strategy:
 *   - navigation requests: network first, fall back to cached shell when offline
 *   - static assets (js/css/png/woff): cache first (fingerprinted filenames)
 *   - /api/*: never cached (per-user data must not persist across accounts)
 */
const CACHE = 'mistakebook-shell-v1';
const SHELL = ['/', '/index.html', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return; // always live

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match('/index.html').then((cached) => cached || new Response('', { status: 504 }))
      )
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request)
        .then((response) => {
          if (response.ok && response.type === 'basic') {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached || new Response('', { status: 504 }));
    })
  );
});
"""


@router.get("/manifest.webmanifest")
def manifest() -> Response:
    import json

    return Response(
        content=json.dumps(MANIFEST, ensure_ascii=False),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sw.js")
def service_worker() -> Response:
    return Response(
        content=SERVICE_WORKER,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            # Allow the worker to control the whole origin.
            "Service-Worker-Allowed": "/",
        },
    )


@router.get("/offline")
def offline_page() -> Response:
    html = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>离线</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif;
margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f7fb;color:#1f2937}
.box{text-align:center;padding:32px}
h1{font-size:18px;margin:0 0 8px}p{color:#6b7280;font-size:13px;margin:0}
</style></head>
<body><div class="box"><h1>当前处于离线状态</h1>
<p>已保存的页面仍可浏览，恢复网络后会自动同步。</p></div></body></html>"""
    return Response(content=html, media_type="text/html")
