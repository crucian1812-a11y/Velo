// Офлайн-кэш: оболочка приложения + просмотренные тайлы карты.
var VERSION = 'velo1-v2';
var SHELL = [
  './', 'index.html', 'css/style.css', 'js/app.js', 'js/route.js', 'js/pois.js',
  'manifest.webmanifest', 'icon.svg',
  'css/vendor/leaflet.css', 'js/vendor/leaflet.js'
];
var TILES = 'velo1-tiles';
var MAX_TILES = 4000;

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(VERSION).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== VERSION && k !== TILES; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  var url = e.request.url;
  if (e.request.method !== 'GET') return;

  if (/tile\.openstreetmap\.org/.test(url)) {
    // Тайлы: сначала кэш, потом сеть
    e.respondWith(caches.open(TILES).then(function (c) {
      return c.match(e.request).then(function (hit) {
        if (hit) return hit;
        return fetch(e.request).then(function (res) {
          if (res.ok || res.type === 'opaque') {
            c.put(e.request, res.clone());
            c.keys().then(function (ks) { if (ks.length > MAX_TILES) c.delete(ks[0]); });
          }
          return res;
        });
      });
    }));
    return;
  }

  // Приложение: сначала сеть (чтобы получать обновления), при офлайне — кэш
  e.respondWith(fetch(e.request).then(function (res) {
    if (res.ok) { var copy = res.clone(); caches.open(VERSION).then(function (c) { c.put(e.request, copy); }); }
    return res;
  }).catch(function () {
    return caches.match(e.request, { ignoreSearch: true });
  }));
});
