/**
 * Alf-E Service Worker
 * Strategy: cache-first for static assets, network-only for API calls.
 */

const CACHE = 'alfe-v1';

const STATIC = [
    '/',
    '/static/app.css',
    '/static/app.js',
    '/static/manifest.json',
    '/static/icon.svg',
    '/static/icon-maskable.svg',
];

// ── Install: pre-cache static assets ─────────────────────────────────────────

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE)
            .then(cache => cache.addAll(STATIC))
            .then(() => self.skipWaiting())
    );
});

// ── Activate: clean old caches ────────────────────────────────────────────────

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys.filter(k => k !== CACHE).map(k => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────

self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // API calls: always go to network (no caching)
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request).catch(() =>
                new Response(
                    JSON.stringify({ error: 'offline', detail: 'No network connection.' }),
                    { status: 503, headers: { 'Content-Type': 'application/json' } }
                )
            )
        );
        return;
    }

    // Static assets: cache-first, fall back to network
    event.respondWith(
        caches.match(event.request).then(cached => {
            if (cached) return cached;

            return fetch(event.request).then(response => {
                // Only cache successful same-origin responses
                if (response.ok && url.origin === self.location.origin) {
                    const clone = response.clone();
                    caches.open(CACHE).then(cache => cache.put(event.request, clone));
                }
                return response;
            }).catch(() => {
                // Offline fallback: serve root for navigation requests
                if (event.request.mode === 'navigate') {
                    return caches.match('/');
                }
            });
        })
    );
});
