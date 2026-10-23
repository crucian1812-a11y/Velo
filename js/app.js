/* Велотрекер ВЕЛО1: Конаково ГРЭС → Яхрома */
(function () {
  'use strict';

  // ---------- Константы ----------
  var STORE_KEY = 'velo1.ride.v1';
  var SETTINGS_KEY = 'velo1.settings.v1';
  var MOVING_SPEED = 1.0;      // м/с (3.6 км/ч): ниже — считаем, что стоим
  var MAX_JUMP_SPEED = 22;     // м/с (≈80 км/ч): быстрее — выброс GPS
  var OFF_ROUTE_M = 150;       // сход с маршрута
  var GPS_LOST_S = 20;         // нет координат дольше — предупреждение
  var POI_RADIUS_KM = 0.25;    // за сколько до точки открывать рассказ
  var AUTO_PAUSE_S = 15;       // автопауза после стольких секунд стоянки

  var $ = function (id) { return document.getElementById(id); };

  // Android-приложение (Capacitor): фоновый GPS и системная озвучка работают при погашенном экране
  // Мост Capacitor сам кладёт плагины в window.Capacitor.Plugins (registerPlugin есть
  // только в JS-библиотеке @capacitor/core, которую мы не подключаем).
  var Cap = window.Capacitor;
  var NATIVE = !!(Cap && typeof Cap.isNativePlatform === 'function' && Cap.isNativePlatform());
  function nativePlugin(name) {
    if (!NATIVE) return null;
    if (Cap.Plugins && Cap.Plugins[name]) return Cap.Plugins[name];
    if (typeof Cap.registerPlugin === 'function') return Cap.registerPlugin(name);
    return null;
  }
  var BgGeo = nativePlugin('BackgroundGeolocation');
  var Tts = nativePlugin('TextToSpeech');
  var AppPlugin = nativePlugin('App');
  var Notif = nativePlugin('LocalNotifications');
  // Методы плагинов возвращают то промис, то сразу значение — приводим к промису
  function call(fn) {
    try { return Promise.resolve(fn()); } catch (e) { return Promise.reject(e); }
  }

  // Любая ошибка скрипта — на экран, чтобы было видно, что сломалось
  window.addEventListener('error', function (e) {
    var b = document.getElementById('banner');
    if (!b) return;
    b.textContent = 'Ошибка приложения: ' + (e.message || e) + (e.lineno ? ' (строка ' + e.lineno + ')' : '');
    b.className = 'banner err';
  });

  // ---------- Геометрия ----------
  var R = 6371000;
  function rad(d) { return d * Math.PI / 180; }
  function hav(aLat, aLon, bLat, bLon) {
    var dLat = rad(bLat - aLat), dLon = rad(bLon - aLon);
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(rad(aLat)) * Math.cos(rad(bLat)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  var ROUTE = window.ROUTE;
  var CUM = [0];
  for (var i = 1; i < ROUTE.length; i++) {
    CUM.push(CUM[i - 1] + hav(ROUTE[i - 1][0], ROUTE[i - 1][1], ROUTE[i][0], ROUTE[i][1]));
  }
  var ROUTE_LEN = CUM[CUM.length - 1];
  var POIS = window.POIS.slice().sort(function (a, b) { return a.km - b.km; });
  POIS.forEach(function (p) {
    if (p.type === 'finish') p.km = ROUTE_LEN / 1000;
    if (p.lat == null) { var pt = pointAtKm(p.km); p.lat = pt[0]; p.lon = pt[1]; }
  });

  function pointAtKm(km) {
    var m = km * 1000;
    for (var j = 1; j < CUM.length; j++) {
      if (CUM[j] >= m) {
        var t = (m - CUM[j - 1]) / ((CUM[j] - CUM[j - 1]) || 1);
        return [ROUTE[j - 1][0] + t * (ROUTE[j][0] - ROUTE[j - 1][0]), ROUTE[j - 1][1] + t * (ROUTE[j][1] - ROUTE[j - 1][1])];
      }
    }
    return ROUTE[ROUTE.length - 1];
  }

  // Проекция точки на маршрут: километр и расстояние до линии.
  // Ищем сначала рядом с прошлой позицией, чтобы не «прыгать» между близкими участками.
  var lastIdx = -1;
  function project(lat, lon) {
    var kx = Math.cos(rad(lat)) * 111320, ky = 110540;
    function scan(from, to) {
      var best = { d: Infinity, idx: 0, m: 0 };
      for (var j = Math.max(1, from); j <= Math.min(ROUTE.length - 1, to); j++) {
        var ax = (ROUTE[j - 1][1] - lon) * kx, ay = (ROUTE[j - 1][0] - lat) * ky;
        var bx = (ROUTE[j][1] - lon) * kx, by = (ROUTE[j][0] - lat) * ky;
        var dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
        var t = len2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
        var px = ax + t * dx, py = ay + t * dy, d = Math.sqrt(px * px + py * py);
        if (d < best.d) best = { d: d, idx: j, m: CUM[j - 1] + t * (CUM[j] - CUM[j - 1]) };
      }
      return best;
    }
    var res = null;
    if (lastIdx > 0) {
      res = scan(lastIdx - 60, lastIdx + 120);
      if (res.d > 200) res = null;
    }
    if (!res) res = scan(1, ROUTE.length - 1);
    lastIdx = res.idx;
    return { km: res.m / 1000, off: res.d, idx: res.idx };
  }

  // ---------- Форматирование ----------
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function fmtDur(ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    return Math.floor(s / 3600) + ':' + pad(Math.floor(s / 60) % 60) + ':' + pad(s % 60);
  }
  function fmtPace(secPerKm) {
    if (!isFinite(secPerKm) || secPerKm <= 0 || secPerKm > 3600) return '—';
    var s = Math.round(secPerKm);
    return Math.floor(s / 60) + ':' + pad(s % 60);
  }
  function fmtClock(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function fmtKm(m, digits) { return (m / 1000).toFixed(digits == null ? 2 : digits); }
  function kmh(ms) { return ms * 3.6; }

  // ---------- Настройки и состояние ----------
  function loadJSON(key, def) {
    try { var v = JSON.parse(localStorage.getItem(key)); return v || def; } catch (e) { return def; }
  }
  function saveJSON(key, v) {
    try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* хранилище недоступно */ }
  }

  var settings = Object.assign({ voice: true, auto: true, autoPause: true, weight: 75, acc: 35 }, loadJSON(SETTINGS_KEY, {}));

  function newRide() {
    return {
      status: 'idle',          // idle | riding | paused | finished
      startedAt: null,         // время старта (мс)
      finishedAt: null,
      elapsedMs: 0,            // общее время с паузами — считаем от startedAt
      movingMs: 0,
      dist: 0,                 // м
      maxSpeed: 0,             // м/с
      gain: 0, loss: 0,
      altRef: null, altSmooth: null,
      track: [[]],             // сегменты: [[lat, lon, t, alt], ...]
      splits: [],              // [{km, movingMs}]
      seen: {},                // открытые экскурсии
      quiz: {},                // ответы викторины
      autoPaused: false
    };
  }
  var ride = Object.assign(newRide(), loadJSON(STORE_KEY, {}));
  if (!Array.isArray(ride.track) || !ride.track.length) ride.track = [[]];

  var saveTimer = null;
  function persist(now) {
    if (now) { clearTimeout(saveTimer); saveTimer = null; saveJSON(STORE_KEY, ride); return; }
    if (saveTimer) return;
    saveTimer = setTimeout(function () { saveTimer = null; saveJSON(STORE_KEY, ride); }, 4000);
  }

  // ---------- Живые данные GPS ----------
  var fix = null;            // последняя точка {lat, lon, acc, alt, t}
  var lastRec = null;        // последняя записанная точка трека
  var rawPrev = null;        // предыдущая сырая точка (для скорости)
  var speed = 0;             // сглаженная текущая скорость, м/с
  var pos = null;            // проекция на маршрут
  var lastMoveAt = 0;
  var prevFixT = 0;          // время предыдущей точки (для времени в движении)
  var altBuf = [];           // высоты за последнюю минуту [t, alt]
  var watchId = null;
  var bgWatcherId = null, bgMode = null;
  var gpsGranted = false;

  // ---------- Карта ----------
  var map, routeLine, trackLines = [], meMarker, accCircle, follow = true, mapReady = false;

  function initMap() {
    if (typeof L === 'undefined') { toast('Карта не загрузилась — нет интернета. Трекер работает.'); return; }
    map = L.map('map', { zoomControl: false, attributionControl: true });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, subdomains: 'abc',
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    L.control.zoom({ position: 'bottomright' }).addTo(map);

    L.polyline(ROUTE, { color: '#ffffff', weight: 9, opacity: .8 }).addTo(map);
    routeLine = L.polyline(ROUTE, { color: getCss('--route'), weight: 5, opacity: .9 }).addTo(map);

    POIS.forEach(function (p) {
      var m = L.marker([p.lat, p.lon], {
        icon: L.divIcon({ className: 'poi-marker', html: p.icon, iconSize: [26, 26], iconAnchor: [13, 13] })
      }).addTo(map);
      m.on('click', function () { openPoi(p, false); });
    });

    for (var k = 0; k <= Math.floor(ROUTE_LEN / 10000); k++) {
      if (k === 0) continue;
      L.circleMarker(pointAtKm(k * 10), { radius: 9, color: '#fff', weight: 2, fillColor: '#111', fillOpacity: .85 })
        .bindTooltip(String(k * 10), { permanent: true, direction: 'center', className: 'km-label' }).addTo(map);
    }

    map.fitBounds(routeLine.getBounds(), { padding: [20, 20] });
    map.on('dragstart', function () { setFollow(false); });
    mapReady = true;
    redrawTrack();
    updateMe();
  }

  function getCss(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#e11d48'; }

  function setFollow(on) {
    follow = on;
    $('btnFollow').classList.toggle('active', on);
    if (on && fix && map) map.setView([fix.lat, fix.lon], Math.max(map.getZoom(), 15));
  }

  function redrawTrack() {
    if (!mapReady) return;
    trackLines.forEach(function (l) { map.removeLayer(l); });
    trackLines = ride.track.filter(function (s) { return s.length > 1; }).map(function (s) {
      return L.polyline(s.map(function (p) { return [p[0], p[1]]; }), { color: getCss('--track'), weight: 5, opacity: .9 }).addTo(map);
    });
  }

  function appendTrackPoint(p) {
    if (!mapReady) return;
    var seg = ride.track[ride.track.length - 1];
    if (seg.length === 2 || !trackLines.length) { redrawTrack(); return; }
    trackLines[trackLines.length - 1].addLatLng([p[0], p[1]]);
  }

  function updateMe() {
    if (!mapReady || !fix) return;
    var ll = [fix.lat, fix.lon];
    if (!meMarker) {
      accCircle = L.circle(ll, { radius: fix.acc, color: '#2563eb', weight: 1, fillOpacity: .12 }).addTo(map);
      meMarker = L.circleMarker(ll, { radius: 9, color: '#fff', weight: 3, fillColor: '#2563eb', fillOpacity: 1 }).addTo(map);
    } else {
      meMarker.setLatLng(ll);
      accCircle.setLatLng(ll).setRadius(fix.acc);
    }
    if (follow && isTabActive('map')) map.panTo(ll, { animate: true });
  }

  // ---------- GPS ----------
  // Источники координат: геолокация браузера/WebView (пока экран включён) и, в приложении,
  // фоновая служба Android (работает и с погашенным экраном). Точки от обоих идут в onPos.
  var gpsProblem = null;     // 'denied' | 'off' | 'nosignal' | 'unsupported' | 'insecure'
  var gpsAskedAt = 0;        // когда начали поиск (0 — ещё не начинали)
  var gpsRetryTimer = null;
  function noop() {}

  function startGps() {
    gpsAskedAt = Date.now();
    gpsProblem = null;
    if (!gpsGranted) showGpsCard('Ищем спутники…', '', null);
    if (BgGeo) startNativeGps(isRideActive(), true);
    else startWebGps();
  }

  function startWebGps() {
    if (!('geolocation' in navigator)) { if (!BgGeo) reportGps('unsupported'); return; }
    if (!window.isSecureContext) { if (!BgGeo) reportGps('insecure'); return; }
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    watchId = navigator.geolocation.watchPosition(onPos, function (err) {
      if (BgGeo && err.code !== 1) return;    // в приложении о выключенном GPS сообщает фоновая служба
      reportGps(err.code === 1 ? 'denied' : err.code === 2 ? 'off' : 'nosignal');
    }, { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 });
  }

  function isRideActive() { return ride.status === 'riding' || ride.status === 'paused'; }

  // В приложении сначала спрашиваем разрешение, потом запускаем службу геолокации. Пока идёт
  // поездка, Android показывает уведомление и присылает координаты при погашенном экране.
  function startNativeGps(background, force) {
    if (!force && bgMode === background && bgWatcherId != null) return;
    call(function () { return BgGeo.requestPermissions(); }).catch(function () { return null; }).then(function (st) {
      if (st && st.location && st.location !== 'granted') { reportGps('denied'); return; }
      if (st && watchId == null) startWebGps();   // запасной источник, если служба не отвечает
      addNativeWatcher(background, !st);           // не смогли спросить заранее — пусть спросит служба
    });
  }

  function addNativeWatcher(background, askPerm) {
    var old = bgWatcherId;
    bgWatcherId = null;
    bgMode = background;
    if (old != null) call(function () { return BgGeo.removeWatcher({ id: old }); }).catch(noop);
    var opts = { requestPermissions: !!askPerm, stale: false, distanceFilter: 0 };
    if (background) {
      opts.backgroundTitle = 'ВЕЛО1: идёт запись поездки';
      opts.backgroundMessage = 'GPS и подсказки работают при выключенном экране';
      if (Notif) call(function () { return Notif.requestPermissions(); }).catch(noop);
    }
    var myId = null, dead = false;
    call(function () { return BgGeo.addWatcher(opts, onNativeLocation); }).then(function (id) {
      myId = id;
      if (dead || bgMode !== background) call(function () { return BgGeo.removeWatcher({ id: id }); }).catch(noop);
      else bgWatcherId = id;
    }).catch(function () { reportGps('nosignal'); });

    function onNativeLocation(loc, err) {
      if (err) {
        // Ошибочный наблюдатель больше не нужен — снимаем его, чтобы повтор запустил новый
        dead = true;
        if (myId != null) call(function () { return BgGeo.removeWatcher({ id: myId }); }).catch(noop);
        if (bgWatcherId === myId) bgWatcherId = null;
        var msg = String((err && err.message) || err);
        if (/disabled/i.test(msg)) reportGps('off');
        else if ((err && err.code === 'NOT_AUTHORIZED') || /denied|permission/i.test(msg)) reportGps('denied');
        else setTimeout(function () { if (bgWatcherId == null) addNativeWatcher(bgMode, askPerm); }, 1500); // служба ещё не запустилась
        return;
      }
      if (!loc) return;
      onPos({
        coords: { latitude: loc.latitude, longitude: loc.longitude, accuracy: loc.accuracy, altitude: loc.altitude, speed: loc.speed },
        timestamp: loc.time || Date.now()
      });
    }
  }
  function syncGpsMode() { if (BgGeo && gpsGranted) startNativeGps(isRideActive(), false); }

  // Карточка состояния GPS на экране трекера (не блокирует приложение)
  function showGpsCard(text, help, btn, isErr) {
    $('gpsCard').classList.remove('hidden');
    $('gpsCard').classList.toggle('err', !!isErr);
    $('gpsCardText').textContent = text;
    $('gpsCardHelp').innerHTML = help || '';
    $('gpsCardBtn').classList.toggle('hidden', !btn);
    if (btn) $('gpsCardBtn').textContent = btn;
  }

  function gpsCardButton() {
    if (gpsProblem === 'denied' && BgGeo) {
      // Сначала спрашиваем ещё раз; если Android больше не показывает запрос — открываем настройки
      call(function () { return BgGeo.requestPermissions(); }).then(function (st) {
        if (st && st.location === 'granted') startGps();
        else call(function () { return BgGeo.openSettings(); }).catch(noop);
      }).catch(function () { call(function () { return BgGeo.openSettings(); }).catch(noop); });
      return;
    }
    startGps();
  }

  function reportGps(kind) {
    if (fix && Date.now() - fix.t < 15000) return;   // другой источник исправно присылает точки
    gpsProblem = kind;
    if (gpsGranted) { setGpsChip(null); return; }    // во время работы — только индикатор
    var t = {
      denied: BgGeo ? 'Нет разрешения на точное местоположение.' : 'Доступ к геолокации для этого сайта запрещён.',
      off: 'Геолокация на телефоне выключена.',
      nosignal: 'Сигнал GPS пока не найден. Выйдите на открытое место — поиск продолжается.',
      unsupported: 'В этом браузере нет геолокации. Откройте страницу в Chrome или Safari.',
      insecure: 'GPS работает только по защищённой ссылке (https://). Откройте приложение по ссылке из GitHub.'
    }[kind];
    var help = {
      denied: BgGeo ?
        'Нажмите «Открыть настройки» → Разрешения → Местоположение → «Разрешить только во время использования» и включите «Точное местоположение». Затем вернитесь в приложение.' :
        'Android/Chrome: значок 🔒 слева от адреса → Разрешения → Геоданные → Разрешить, затем «Попробовать снова».<br>' +
        'iPhone: Настройки → Конфиденциальность → Службы геолокации → Safari → «При использовании».',
      off: 'Опустите шторку и включите «Местоположение» (GPS). Сигнал подхватится сам, нажимать ничего не нужно.',
      nosignal: 'В помещении спутники часто не видны. Проверьте, что «Местоположение» включено.'
    }[kind] || '';
    showGpsCard(t, help, kind === 'denied' && BgGeo ? 'Открыть настройки' : 'Попробовать снова', kind !== 'nosignal');
    scheduleGpsRetry();
  }

  // Пока GPS выключен, каждые 4 секунды пробуем снова — как только геолокацию включат, поиск пойдёт
  function scheduleGpsRetry() {
    clearTimeout(gpsRetryTimer);
    if (gpsProblem !== 'off') return;
    gpsRetryTimer = setTimeout(function () {
      if (!gpsGranted && gpsProblem === 'off') startGps();
    }, 4000);
  }

  // Подсказка с таймером, пока ищем первую точку
  function updateGateWait(now) {
    if (gpsGranted || !gpsAskedAt || gpsProblem) return;
    var sec = Math.round((now - gpsAskedAt) / 1000);
    $('gpsCardText').textContent = 'Ищем спутники… ' + sec + ' с';
    if (sec >= 20) {
      $('gpsCardHelp').textContent = 'Долго? Выйдите на улицу или к окну и проверьте, что «Местоположение» включено в шторке телефона.';
      $('gpsCardBtn').textContent = 'Попробовать снова';
      $('gpsCardBtn').classList.remove('hidden');
    }
  }

  function onPos(p) {
    var c = p.coords;
    var f = {
      lat: c.latitude, lon: c.longitude, acc: c.accuracy || 999,
      alt: (c.altitude != null && isFinite(c.altitude)) ? c.altitude : null,
      spd: (c.speed != null && isFinite(c.speed) && c.speed >= 0) ? c.speed : null,
      t: p.timestamp || Date.now()
    };
    if (fix && f.t <= fix.t) return;                 // та же или более старая точка из второго источника
    gpsProblem = null;
    if (!gpsGranted) {
      gpsGranted = true;
      $('gpsCard').classList.add('hidden');
      toast('GPS подключён');
    }
    fix = f;
    setGpsChip(f.acc);

    // Текущая скорость
    var inst = null;
    if (f.spd != null) inst = f.spd;
    else if (rawPrev) {
      var dtr = (f.t - rawPrev.t) / 1000;
      if (dtr > 0) inst = hav(rawPrev.lat, rawPrev.lon, f.lat, f.lon) / dtr;
    }
    rawPrev = f;
    if (inst != null && inst < MAX_JUMP_SPEED && f.acc <= settings.acc * 1.5) {
      speed = speed * 0.4 + inst * 0.6;
      if (speed < 0.3) speed = 0;
    }
    if (speed >= MOVING_SPEED) lastMoveAt = Date.now();

    pos = project(f.lat, f.lon);

    var gap = prevFixT ? f.t - prevFixT : 0;
    prevFixT = f.t;
    if (ride.status === 'riding') {
      if (gap > 0 && gap <= 15000 && speed >= MOVING_SPEED) ride.movingMs += gap;
      record(f, gap);
      checkAutoPause(Date.now());
    }
    else if (ride.status === 'paused' && ride.autoPaused && speed >= MOVING_SPEED * 1.5 && f.acc <= settings.acc) {
      resume(true);
    }

    updateMe();
    checkPois();
    checkOffRoute();
    render();
  }

  function setGpsChip(acc) {
    var chip = $('gpsChip');
    if (acc == null) { chip.className = 'chip chip-bad'; chip.textContent = 'GPS нет'; return; }
    chip.className = 'chip ' + (acc <= 15 ? 'chip-good' : acc <= settings.acc ? 'chip-mid' : 'chip-bad');
    chip.textContent = 'GPS ±' + Math.round(acc) + ' м';
  }

  function record(f, gap) {
    if (f.acc > settings.acc) return;               // слишком неточно — не пишем
    var seg = ride.track[ride.track.length - 1];
    if (!lastRec) {
      lastRec = f;
      seg.push([f.lat, f.lon, f.t, f.alt]);
      appendTrackPoint(seg[seg.length - 1]);
      return;
    }
    var d = hav(lastRec.lat, lastRec.lon, f.lat, f.lon);
    var dt = (f.t - lastRec.t) / 1000;
    if (dt <= 0) return;
    if (d / dt > MAX_JUMP_SPEED) return;           // скачок GPS
    if (d < Math.max(4, f.acc * 0.5) && dt < 30) return; // дрожание на месте

    ride.dist += d;
    // Долгий перерыв между точками (например, браузер «уснул» с погашенным экраном):
    // если за это время проехали расстояние — засчитываем время как движение
    if (gap > 15000 && d / (gap / 1000) >= MOVING_SPEED) ride.movingMs += gap;
    if (speed > ride.maxSpeed && f.acc <= 20) ride.maxSpeed = speed;
    lastRec = f;
    seg.push([f.lat, f.lon, f.t, f.alt]);
    appendTrackPoint(seg[seg.length - 1]);

    // Высота: GPS в телефоне ошибается по высоте на 5–15 м, поэтому берём среднее за минуту
    // и учитываем только перепады больше 4 м — иначе на равнине «набирается» сотни метров шума
    if (f.alt != null && f.acc <= 25) {
      altBuf.push([f.t, f.alt]);
      while (altBuf.length && f.t - altBuf[0][0] > 60000) altBuf.shift();
    }
    if (altBuf.length >= 10) {
      ride.altSmooth = altBuf.reduce(function (sum, x) { return sum + x[1]; }, 0) / altBuf.length;
      if (ride.altRef == null) ride.altRef = ride.altSmooth;
      var diff = ride.altSmooth - ride.altRef;
      if (diff > 4) { ride.gain += diff; ride.altRef = ride.altSmooth; }
      else if (diff < -4) { ride.loss -= diff; ride.altRef = ride.altSmooth; }
    }

    // Отрезки по километрам
    while (ride.dist >= (ride.splits.length + 1) * 1000) {
      ride.splits.push({ km: ride.splits.length + 1, movingMs: ride.movingMs });
      var n = ride.splits.length;
      if (n % 5 === 0) {
        var prev = n >= 5 ? ride.splits[n - 6] : null;
        var ms = ride.movingMs - (prev ? prev.movingMs : 0);
        say(n + ' километров. Средняя скорость ' + Math.round(kmh(ride.dist / (ride.movingMs / 1000 || 1))) +
          ' километров в час. Последние пять километров за ' + Math.round(ms / 60000) + ' минут.');
      }
    }
    persist();
  }

  // ---------- Таймер ----------
  setInterval(function () {
    var now = Date.now();
    if (ride.status === 'riding') checkAutoPause(now);
    checkGpsLost(now);
    updateGateWait(now);
    render();
  }, 1000);

  function checkAutoPause(now) {
    var fresh = fix && (now - fix.t) < 10000;
    if (settings.autoPause && fresh && lastMoveAt && now - lastMoveAt > AUTO_PAUSE_S * 1000 && ride.movingMs > 0) {
      pause(true);
    }
  }

  var lostShown = false;
  function checkGpsLost(now) {
    if (!gpsGranted) return;
    var lost = !fix || (now - fix.t) / 1000 > GPS_LOST_S;
    if (lost && !lostShown) {
      lostShown = true;
      setGpsChip(null);
      showBanner(NATIVE ? 'Нет сигнала GPS. Проверьте, что геолокация включена.' :
        'Нет сигнала GPS. Держите экран включённым и проверьте геолокацию.', true);
      if (ride.status === 'riding') say('Пропал сигнал GPS');
    } else if (!lost && lostShown) {
      lostShown = false;
      hideBanner();
    }
  }

  // ---------- Управление поездкой ----------
  function start() {
    if (!fix || (Date.now() - fix.t) > 15000) { toast('Ждём сигнал GPS…'); return; }
    if (fix.acc > settings.acc) { toast('GPS неточный (±' + Math.round(fix.acc) + ' м). Подождите несколько секунд.'); return; }
    var keep = ride.seen, quiz = ride.quiz;
    ride = newRide();
    ride.seen = keep; ride.quiz = quiz;   // уже прочитанные рассказы не повторяем
    ride.status = 'riding';
    ride.startedAt = Date.now();
    lastRec = null; lastMoveAt = Date.now(); prevFixT = 0;
    redrawTrack();
    record(fix, 0);
    requestWakeLock();
    syncGpsMode();
    say('Поехали! Хорошей дороги.');
    persist(true);
    render();
  }

  function pause(auto) {
    if (ride.status !== 'riding') return;
    ride.status = 'paused';
    ride.autoPaused = !!auto;
    toast(auto ? 'Автопауза: вы остановились' : 'Пауза');
    if (!auto) say('Пауза');
    persist(true);
    render();
  }

  function resume(auto) {
    if (ride.status !== 'paused') return;
    ride.status = 'riding';
    ride.autoPaused = false;
    ride.track.push([]);      // новый сегмент — без прямой линии через паузу
    lastRec = null; lastMoveAt = Date.now(); prevFixT = 0;
    toast(auto ? 'Движение — продолжаем запись' : 'Продолжаем');
    requestWakeLock();
    persist(true);
    render();
  }

  function finish() {
    if (!confirm('Завершить поездку? Трек и статистика сохранятся.')) return;
    ride.status = 'finished';
    ride.finishedAt = Date.now();
    releaseWakeLock();
    syncGpsMode();
    say('Поездка завершена. Дистанция ' + fmtKm(ride.dist, 1).replace('.', ',') + ' километра.');
    persist(true);
    render();
    showTab('stats');
  }

  function resetRide() {
    if (!confirm('Удалить текущую поездку, трек и ответы викторины?')) return;
    ride = newRide();
    lastRec = null;
    persist(true);
    syncGpsMode();
    redrawTrack();
    renderGuide();
    render();
    toast('Поездка сброшена');
  }

  // ---------- Экскурсии ----------
  var currentPoi = null;
  var poiQueue = [];

  function checkPois() {
    if (!pos || pos.off > 400) return;
    if (ride.status !== 'riding' && ride.status !== 'paused') return;
    POIS.forEach(function (p) {
      if (ride.seen[p.id]) return;
      var nearByKm = pos.km >= p.km - POI_RADIUS_KM && pos.km <= p.km + 1.5;
      var nearByGeo = hav(fix.lat, fix.lon, p.lat, p.lon) < 120;
      if (nearByKm || nearByGeo) {
        ride.seen[p.id] = Date.now();
        persist();
        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
        if (settings.auto) poiQueue.push(p);
        else toast(p.icon + ' ' + p.title + ' — откройте во вкладке «Гид»');
        renderGuide();
      }
    });
    if (!currentPoi && poiQueue.length) openPoi(poiQueue.shift(), true);
  }

  function openPoi(p, auto) {
    currentPoi = p;
    $('pmIcon').textContent = p.icon;
    $('pmTitle').textContent = p.title;
    $('pmKm').textContent = p.km.toFixed(1) + ' км маршрута' + (pos ? ' · ' + distText(p) : '');
    $('pmStory').innerHTML = p.story.map(function (s) { return '<p>' + esc(s) + '</p>'; }).join('');
    renderQuiz(p);
    $('poiModal').classList.remove('hidden');
    if (auto && settings.voice) speakPoi(p);
  }

  function closePoi() {
    stopSpeech();
    $('poiModal').classList.add('hidden');
    currentPoi = null;
    if (poiQueue.length) setTimeout(function () { if (!currentPoi && poiQueue.length) openPoi(poiQueue.shift(), true); }, 400);
  }

  function renderQuiz(p) {
    var box = $('pmQuiz');
    if (!p.quiz) { box.innerHTML = ''; return; }
    var chosen = ride.quiz[p.id];
    var html = '<div class="q">❓ ' + esc(p.quiz.q) + '</div>';
    p.quiz.options.forEach(function (o, k) {
      var cls = '';
      if (chosen != null) {
        if (k === p.quiz.answer) cls = 'right';
        else if (k === chosen) cls = 'wrong';
      }
      html += '<button class="btn ' + cls + '" data-k="' + k + '"' + (chosen != null ? ' disabled' : '') + '>' + esc(o) + '</button>';
    });
    if (chosen != null) {
      html += '<div class="explain">' + (chosen === p.quiz.answer ? '✅ Верно! ' : '❌ Не совсем. ') + esc(p.quiz.explain) + '</div>';
    }
    box.innerHTML = html;
    Array.prototype.forEach.call(box.querySelectorAll('button[data-k]'), function (b) {
      b.addEventListener('click', function () {
        var k = Number(b.getAttribute('data-k'));
        ride.quiz[p.id] = k;
        persist(true);
        renderQuiz(p);
        renderGuide();
        if (settings.voice) say((k === p.quiz.answer ? 'Верно! ' : 'Не совсем. ') + p.quiz.explain);
      });
    });
  }

  function distText(p) {
    if (!pos) return '';
    var d = p.km - pos.km;
    if (d > 0.05) return 'через ' + d.toFixed(1) + ' км';
    if (d > -0.3) return 'вы здесь';
    return 'пройдено';
  }

  function nextPoi() {
    if (!pos) return POIS[0];
    for (var k = 0; k < POIS.length; k++) if (POIS[k].km > pos.km + 0.05) return POIS[k];
    return null;
  }

  function renderGuide() {
    var list = $('poiList');
    list.innerHTML = '';
    var total = 0, right = 0;
    POIS.forEach(function (p) {
      if (p.quiz) { total++; if (ride.quiz[p.id] === p.quiz.answer) right++; }
      var li = document.createElement('li');
      if (ride.seen[p.id]) li.className = 'done';
      li.innerHTML = '<span class="pi">' + p.icon + '</span>' +
        '<span class="pt"><b>' + esc(p.title) + '</b><span class="muted small">' + esc(p.short) + (p.quiz ? ' · ❓' : '') + '</span></span>' +
        '<span class="pk">' + p.km.toFixed(1) + ' км<br>' + (ride.seen[p.id] ? '✓' : esc(distText(p))) + '</span>';
      li.addEventListener('click', function () { openPoi(p, false); });
      list.appendChild(li);
    });
    $('quizScore').textContent = right + ' / ' + total;
  }

  function renderProgressDots() {
    var bar = $('progress');
    POIS.forEach(function (p) {
      var d = document.createElement('span');
      d.className = 'progress-dot';
      d.style.left = (p.km * 1000 / ROUTE_LEN * 100) + '%';
      d.textContent = p.icon;
      d.title = p.title;
      d.addEventListener('click', function () { openPoi(p, false); });
      bar.appendChild(d);
    });
  }

  // ---------- Сход с маршрута ----------
  var offShown = false;
  function checkOffRoute() {
    if (!pos || !fix || fix.acc > 60) return;
    var off = pos.off > OFF_ROUTE_M + Math.min(fix.acc, 50);
    if (off && ride.status === 'riding') {
      var tgt = ROUTE[pos.idx];
      var dir = bearingName(fix.lat, fix.lon, tgt[0], tgt[1]);
      showBanner('⚠️ Вы в ' + Math.round(pos.off) + ' м от маршрута. Трасса — на ' + dir + '.', false);
      if (!offShown) { say('Внимание, вы сошли с маршрута. Трасса на ' + dir + '.'); if (navigator.vibrate) navigator.vibrate(500); }
      offShown = true;
    } else if (offShown) {
      offShown = false;
      if (!lostShown) hideBanner();
      if (!off) toast('Вы снова на маршруте');
    }
  }

  function bearingName(aLat, aLon, bLat, bLon) {
    var y = Math.sin(rad(bLon - aLon)) * Math.cos(rad(bLat));
    var x = Math.cos(rad(aLat)) * Math.sin(rad(bLat)) - Math.sin(rad(aLat)) * Math.cos(rad(bLat)) * Math.cos(rad(bLon - aLon));
    var b = (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    return ['севере', 'северо-востоке', 'востоке', 'юго-востоке', 'юге', 'юго-западе', 'западе', 'северо-западе'][Math.round(b / 45) % 8];
  }

  // ---------- Голос ----------
  var ruVoice = null;
  function pickVoice() {
    if (!('speechSynthesis' in window)) return;
    var vs = speechSynthesis.getVoices();
    ruVoice = vs.filter(function (v) { return /^ru/i.test(v.lang); })[0] || null;
  }
  if ('speechSynthesis' in window) {
    pickVoice();
    speechSynthesis.onvoiceschanged = pickVoice;
  }
  // В приложении — системный синтезатор речи Android (работает и с погашенным экраном),
  // в браузере — Web Speech API (голос из настроек телефона)
  var nativeSpeaking = 0;
  function say(text, interrupt) {
    if (!settings.voice) return;
    if (Tts) {
      nativeSpeaking++;
      call(function () { return Tts.speak({ text: text, lang: 'ru-RU', rate: 1.0, queueStrategy: interrupt ? 0 : 1 }); })
        .catch(function () {})
        .then(function () { nativeSpeaking = Math.max(0, nativeSpeaking - 1); });
      return;
    }
    if (!('speechSynthesis' in window)) return;
    if (interrupt) speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(text);
    u.lang = 'ru-RU';
    if (ruVoice) u.voice = ruVoice;
    u.rate = 1.0;
    speechSynthesis.speak(u);
  }
  function speakPoi(p) {
    say(p.title + '. ' + p.story.join(' ') + (p.quiz && ride.quiz[p.id] == null ? ' Вопрос викторины: ' + p.quiz.q : ''), true);
  }
  function stopSpeech() {
    if (Tts) { nativeSpeaking = 0; call(function () { return Tts.stop(); }).catch(function () {}); return; }
    if ('speechSynthesis' in window) speechSynthesis.cancel();
  }
  function isSpeaking() {
    if (Tts) return nativeSpeaking > 0;
    return 'speechSynthesis' in window && speechSynthesis.speaking;
  }

  // ---------- Блокировка сна экрана ----------
  var wakeLock = null;
  function requestWakeLock() {
    if (NATIVE || !('wakeLock' in navigator)) return; // в приложении экран можно гасить
    navigator.wakeLock.request('screen').then(function (wl) {
      wakeLock = wl;
      wl.addEventListener('release', function () { wakeLock = null; });
    }).catch(function () { /* не поддерживается или нет жеста */ });
  }
  function releaseWakeLock() { if (wakeLock) { wakeLock.release(); wakeLock = null; } }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      if (ride.status === 'riding' || ride.status === 'paused') requestWakeLock();
      if (map) setTimeout(function () { map.invalidateSize(); }, 100);
      if (!NATIVE && !gpsGranted && gpsAskedAt) startGps();   // вернулись из настроек браузера
    } else {
      persist(true);
    }
  });
  window.addEventListener('pagehide', function () { persist(true); });

  // ---------- Отрисовка ----------
  function render() {
    var now = Date.now();
    var elapsed = ride.startedAt ? (ride.finishedAt || now) - ride.startedAt : 0;
    var movingS = ride.movingMs / 1000;
    var avg = movingS > 0 ? ride.dist / movingS : 0;
    var curSpeed = (ride.status === 'finished') ? 0 : speed;

    $('mSpeed').textContent = kmh(curSpeed).toFixed(1);
    $('mPaceNow').textContent = curSpeed >= MOVING_SPEED ? fmtPace(1000 / curSpeed) : '—';
    var moving = curSpeed >= MOVING_SPEED && fix && now - fix.t < 10000;
    $('movingFlag').textContent = moving ? 'в движении' : 'стоим';
    $('movingFlag').classList.toggle('on', !!moving);

    $('mDist').textContent = fmtKm(ride.dist);
    $('mMoving').textContent = fmtDur(ride.movingMs);
    $('mElapsed').textContent = fmtDur(elapsed);
    $('mAvg').textContent = kmh(avg).toFixed(1);
    $('mPace').textContent = ride.dist > 100 ? fmtPace(movingS / (ride.dist / 1000)) : '—';
    $('mMax').textContent = kmh(ride.maxSpeed).toFixed(1);
    $('mGain').textContent = Math.round(ride.gain);
    $('mAlt').textContent = fix && fix.alt != null ? Math.round(fix.alt) : '—';
    $('mKcal').textContent = Math.round(kcal(avg, ride.movingMs));

    var onRoute = pos && pos.off < 1000;
    var leftM = onRoute ? Math.max(0, ROUTE_LEN - pos.km * 1000) : null;
    $('mLeft').textContent = leftM != null ? fmtKm(leftM, 1) : '—';
    $('mKm').textContent = onRoute ? pos.km.toFixed(1) : '—';
    var etaSpeed = avg > 1.5 ? avg : (curSpeed > 1.5 ? curSpeed : 4.2); // по умолчанию ~15 км/ч
    $('mEta').textContent = leftM != null ? fmtClock(new Date(now + leftM / etaSpeed * 1000)) : '—';

    var pct = onRoute ? pos.km * 1000 / ROUTE_LEN * 100 : 0;
    $('mPct').textContent = Math.round(pct) + '%';
    $('progressFill').style.width = pct + '%';
    $('progressMe').classList.toggle('hidden', !onRoute);
    $('progressMe').style.left = pct + '%';

    var np = nextPoi();
    $('nextPoi').textContent = np ? 'Далее: ' + np.icon + ' ' + np.title + (pos ? ' — ' + distText(np) : '') : 'Все точки пройдены 🎉';
    $('nextPoi').onclick = np ? function () { openPoi(np, false); } : null;

    $('mapSpeed').textContent = kmh(curSpeed).toFixed(1);
    $('mapDist').textContent = fmtKm(ride.dist);
    $('mapLeft').textContent = leftM != null ? fmtKm(leftM, 1) : '—';

    var st = { idle: 'Готов к старту', riding: '● Запись', paused: ride.autoPaused ? '⏸ Автопауза' : '⏸ Пауза', finished: '🏁 Поездка завершена' };
    $('statusText').textContent = st[ride.status];
    $('clockNow').textContent = fmtClock(new Date(now));

    var s = ride.status;
    $('btnStart').classList.toggle('hidden', s !== 'idle');
    $('btnStart').disabled = !gpsGranted;
    $('btnPause').classList.toggle('hidden', s !== 'riding');
    $('btnResume').classList.toggle('hidden', s !== 'paused');
    $('btnStop').classList.toggle('hidden', s !== 'riding' && s !== 'paused');
    $('btnNew').classList.toggle('hidden', s !== 'finished');

    if (isTabActive('stats')) renderStats(elapsed, avg);
  }

  function kcal(avgMs, movingMs) {
    var v = kmh(avgMs);
    var met = v < 0.5 ? 0 : v < 16 ? 4 : v < 19 ? 6.8 : v < 22 ? 8 : v < 25 ? 10 : 12;
    return met * settings.weight * movingMs / 3600000;
  }

  var lastStatsHtml = '';
  function renderStats(elapsed, avg) {
    var items = [
      ['Дистанция', fmtKm(ride.dist) + ' км'],
      ['Время в движении', fmtDur(ride.movingMs)],
      ['Общее время', fmtDur(elapsed)],
      ['Средняя скорость', kmh(avg).toFixed(1) + ' км/ч'],
      ['Средний темп', ride.dist > 100 ? fmtPace((ride.movingMs / 1000) / (ride.dist / 1000)) + ' /км' : '—'],
      ['Макс. скорость', kmh(ride.maxSpeed).toFixed(1) + ' км/ч'],
      ['Набор / сброс', Math.round(ride.gain) + ' / ' + Math.round(ride.loss) + ' м'],
      ['Калории', Math.round(kcal(avg, ride.movingMs)) + ' ккал'],
      ['Стоянки', fmtDur(Math.max(0, elapsed - ride.movingMs))],
      ['Старт', ride.startedAt ? fmtClock(new Date(ride.startedAt)) : '—']
    ];
    var html = items.map(function (it) { return '<div><label>' + it[0] + '</label><b>' + it[1] + '</b></div>'; }).join('');
    if (html !== lastStatsHtml) { $('summary').innerHTML = html; lastStatsHtml = html; }

    var body = $('splitsBody');
    if (!ride.splits.length) { body.innerHTML = '<tr><td colspan="4" class="muted">Пока нет данных</td></tr>'; return; }
    if (body.getAttribute('data-n') === String(ride.splits.length)) return;
    body.setAttribute('data-n', ride.splits.length);
    var rows = '';
    for (var k = ride.splits.length - 1; k >= 0; k--) {
      var ms = ride.splits[k].movingMs - (k ? ride.splits[k - 1].movingMs : 0);
      var sp = ms > 0 ? 3600000 / ms : 0;
      rows += '<tr><td>' + ride.splits[k].km + '</td><td>' + fmtDur(ms).replace(/^0:/, '') + '</td><td>' + fmtPace(ms / 1000) + '</td><td>' + sp.toFixed(1) + '</td></tr>';
    }
    body.innerHTML = rows;
  }

  // ---------- GPX и «поделиться» ----------
  function exportGpx() {
    var pts = ride.track.filter(function (s) { return s.length; });
    if (!pts.length) { toast('Трек пока пуст'); return; }
    var name = 'velo1-' + new Date(ride.startedAt || Date.now()).toISOString().slice(0, 10);
    var gpx = '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<gpx version="1.1" creator="Velo1 tracker" xmlns="http://www.topografix.com/GPX/1/1">\n' +
      '<metadata><name>Конаково ГРЭС — Яхрома</name><time>' + new Date(ride.startedAt || Date.now()).toISOString() + '</time></metadata>\n<trk><name>' + name + '</name><type>cycling</type>\n';
    pts.forEach(function (seg) {
      gpx += '<trkseg>\n';
      seg.forEach(function (p) {
        gpx += '<trkpt lat="' + p[0].toFixed(7) + '" lon="' + p[1].toFixed(7) + '">' +
          (p[3] != null ? '<ele>' + p[3].toFixed(1) + '</ele>' : '') +
          '<time>' + new Date(p[2]).toISOString() + '</time></trkpt>\n';
      });
      gpx += '</trkseg>\n';
    });
    gpx += '</trk>\n</gpx>\n';
    var file = name + '.gpx';
    var blob = new Blob([gpx], { type: 'application/gpx+xml' });
    try {
      var f = new File([blob], file, { type: 'application/gpx+xml' });
      if (navigator.canShare && navigator.canShare({ files: [f] })) {
        navigator.share({ files: [f], title: file }).catch(function () { download(blob, file); });
        return;
      }
    } catch (e) { /* File API недоступен */ }
    download(blob, file);
  }
  function download(blob, file) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = file;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  function shareLocation() {
    if (!fix) { toast('Нет координат GPS'); return; }
    var lat = fix.lat.toFixed(5), lon = fix.lon.toFixed(5);
    var text = 'Я на ВЕЛО1' + (pos && pos.off < 1000 ? ', ' + pos.km.toFixed(1) + ' км маршрута' : '') +
      ': https://yandex.ru/maps/?pt=' + lon + ',' + lat + '&z=15&l=map';
    if (navigator.share) navigator.share({ text: text }).catch(function () {});
    else if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { toast('Ссылка скопирована'); });
    else prompt('Скопируйте ссылку', text);
  }

  // ---------- Интерфейс ----------
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  var toastTimer;
  function toast(text) {
    var t = $('toast');
    t.textContent = text;
    t.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.add('hidden'); }, 3000);
  }
  function showBanner(text, err) {
    var b = $('banner');
    b.textContent = text;
    b.className = 'banner' + (err ? ' err' : '');
  }
  function hideBanner() { $('banner').className = 'banner hidden'; }

  function isTabActive(name) { return $('tab-' + name).classList.contains('active'); }
  function showTab(name) {
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) { t.classList.toggle('active', t.id === 'tab-' + name); });
    Array.prototype.forEach.call(document.querySelectorAll('.tabs button'), function (b) { b.classList.toggle('active', b.getAttribute('data-tab') === name); });
    if (name === 'map') {
      if (!map) initMap();
      setTimeout(function () { if (map) { map.invalidateSize(); if (follow && fix) map.setView([fix.lat, fix.lon], Math.max(map.getZoom(), 15)); } }, 50);
    }
    if (name === 'guide') renderGuide();
    if (name === 'stats') { lastStatsHtml = ''; $('splitsBody').removeAttribute('data-n'); render(); }
  }

  function bindSettings() {
    $('setVoice').checked = settings.voice;
    $('setAuto').checked = settings.auto;
    $('setAutoPause').checked = settings.autoPause;
    $('setWeight').value = settings.weight;
    $('setAcc').value = settings.acc;
    function save() {
      settings.voice = $('setVoice').checked;
      settings.auto = $('setAuto').checked;
      settings.autoPause = $('setAutoPause').checked;
      settings.weight = Math.min(200, Math.max(30, Number($('setWeight').value) || 75));
      settings.acc = Math.min(100, Math.max(10, Number($('setAcc').value) || 35));
      if (!settings.voice) stopSpeech();
      saveJSON(SETTINGS_KEY, settings);
    }
    ['setVoice', 'setAuto', 'setAutoPause', 'setWeight', 'setAcc'].forEach(function (id) { $(id).addEventListener('change', save); });
  }

  function bind() {
    $('gpsCardBtn').addEventListener('click', gpsCardButton);
    $('btnStart').addEventListener('click', start);
    $('btnPause').addEventListener('click', function () { pause(false); });
    $('btnResume').addEventListener('click', function () { resume(false); });
    $('btnStop').addEventListener('click', finish);
    $('btnNew').addEventListener('click', function () {
      if (!confirm('Начать новую поездку? Текущая будет удалена — сначала сохраните GPX.')) return;
      ride = newRide(); lastRec = null; persist(true); syncGpsMode(); redrawTrack(); renderGuide(); render(); showTab('ride');
    });
    $('btnReset').addEventListener('click', resetRide);
    $('btnGpx').addEventListener('click', exportGpx);
    $('btnShare').addEventListener('click', shareLocation);
    $('btnFollow').addEventListener('click', function () { setFollow(!follow); });
    $('btnFit').addEventListener('click', function () { setFollow(false); if (map) map.fitBounds(routeLine.getBounds(), { padding: [20, 20] }); });
    $('pmClose').addEventListener('click', closePoi);
    $('pmSpeak').addEventListener('click', function () {
      if (!currentPoi) return;
      if (isSpeaking()) { stopSpeech(); return; }
      var was = settings.voice; settings.voice = true; speakPoi(currentPoi); settings.voice = was;
    });
    $('pmMap').addEventListener('click', function () {
      var p = currentPoi;
      closePoi();
      showTab('map');
      setTimeout(function () { if (map) { setFollow(false); map.setView([p.lat, p.lon], 15); } }, 120);
    });
    $('poiModal').addEventListener('click', function (e) { if (e.target === $('poiModal')) closePoi(); });
    Array.prototype.forEach.call(document.querySelectorAll('.tabs button'), function (b) {
      b.addEventListener('click', function () { showTab(b.getAttribute('data-tab')); });
    });
  }

  // «Назад» на Android: закрываем окно рассказа, иначе сворачиваем приложение (не закрываем —
  // иначе запись поездки прервётся)
  if (AppPlugin) {
    AppPlugin.addListener('backButton', function () {
      if (!$('poiModal').classList.contains('hidden')) { closePoi(); return; }
      if (!isTabActive('ride')) { showTab('ride'); return; }
      call(function () { return AppPlugin.minimizeApp(); }).catch(function () {});
    });
    // Вернулись из настроек с выданным разрешением — подключаем GPS снова
    AppPlugin.addListener('resume', function () {
      if (!gpsGranted && gpsAskedAt) startGps();
    });
  }

  // ---------- Запуск ----------
  bind();
  bindSettings();
  if (NATIVE) $('screenNote').textContent = 'Приложение пишет трек и озвучивает подсказки при заблокированном экране. ' +
    'Если запись обрывается — отключите для «ВЕЛО1 Трекер» оптимизацию батареи в настройках телефона.';
  renderProgressDots();
  renderGuide();
  render();

  if (ride.status === 'riding' || ride.status === 'paused') {
    toast('Поездка восстановлена');
    // Время, когда страница была закрыта, не считаем движением
    lastRec = null;
    if (ride.track[ride.track.length - 1].length) ride.track.push([]);
  }

  // GPS подключаем сразу при открытии — без отдельного экрана
  startGps();

  if (!NATIVE && 'serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('sw.js').catch(function () {});
  }
})();
