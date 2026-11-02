// Heatmap layers for the tactical map. Works in the browser (window.PubgHeatmap) and in Node (tests).
// extractPoints() mirrors extract_points() in heatmap.py, so a match gives the same points in both.
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.PubgHeatmap = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // sigma: blur radius in meters, so the spots look the same on every map size
    const LAYERS = [
        { id: 'landing', label: 'Landing', sigma: 140, on: true },
        { id: 'kills', label: 'Kills', sigma: 90, on: true },
        { id: 'knocks', label: 'Knocks dealt', sigma: 90, on: true },
        { id: 'deaths', label: 'Deaths', sigma: 110, on: true },
        { id: 'knocked', label: 'Knocked', sigma: 110, on: false },
        { id: 'movement', label: 'Movement', sigma: 70, on: false },
    ];
    const LAYER_IDS = LAYERS.map((l) => l.id);
    const KILL_EVENTS = new Set(['LogPlayerKillV2', 'LogPlayerKill']);

    function emptyLayers() {
        return Object.fromEntries(LAYER_IDS.map((id) => [id, []]));
    }

    // player: { account_id, name }. Account id wins when both sides have one (names can change).
    function isPlayer(c, player) {
        if (!c || typeof c !== 'object') return false;
        if (player.account_id && c.accountId) return c.accountId === player.account_id;
        return !!player.name && c.name === player.name;
    }

    function point(loc) {
        if (!loc || typeof loc.x !== 'number' || typeof loc.y !== 'number') return null;
        if (loc.x === 0 && loc.y === 0) return null;
        return [Math.round(loc.x), Math.round(loc.y)];
    }

    function extractPoints(events, player) {
        const out = emptyLayers();
        const lastPos = {};
        let landed = false;
        const add = (layer, loc) => { const p = point(loc); if (p) out[layer].push(p); };
        const key = (c) => c.accountId || c.name;

        for (const ev of events || []) {
            if (!ev || typeof ev !== 'object') continue;
            const t = ev._T;
            if (t === 'LogPlayerPosition') {
                const c = ev.character || {};
                if (key(c)) lastPos[key(c)] = c.location;
                if (landed && isPlayer(c, player)) add('movement', c.location);
            } else if (t === 'LogParachuteLanding') {
                const c = ev.character || {};
                if (!landed && isPlayer(c, player)) { landed = true; add('landing', c.location); }
            } else if (t === 'LogPlayerMakeGroggy') {
                const a = ev.attacker || {}, v = ev.victim || {};
                if (isPlayer(a, player) && a.name && !isPlayer(v, player)) add('knocks', v.location);
                if (isPlayer(v, player)) add('knocked', v.location);
            } else if (KILL_EVENTS.has(t)) {
                const k = ev.killer || {}, v = ev.victim || {};
                let loc = v.location;
                if (!point(loc)) loc = lastPos[key(v)];
                if (k.name && isPlayer(k, player) && !isPlayer(v, player)) add('kills', loc);
                if (isPlayer(v, player)) add('deaths', loc);
            }
        }
        return out;
    }

    function merge(target, points) {
        for (const id of LAYER_IDS) (target[id] = target[id] || []).push(...((points && points[id]) || []));
        return target;
    }

    function gaussianKernel(sigma) {
        const r = Math.max(1, Math.ceil(sigma * 3));
        const k = new Float32Array(r * 2 + 1);
        let sum = 0;
        for (let i = -r; i <= r; i++) { k[i + r] = Math.exp(-(i * i) / (2 * sigma * sigma)); sum += k[i + r]; }
        for (let i = 0; i < k.length; i++) k[i] /= sum;
        return k;
    }

    // Separable Gaussian blur of a size x size grid
    function blur(grid, size, sigma) {
        const k = gaussianKernel(sigma), r = (k.length - 1) / 2;
        const tmp = new Float32Array(grid.length), out = new Float32Array(grid.length);
        for (let y = 0; y < size; y++) {
            const row = y * size;
            for (let x = 0; x < size; x++) {
                const v = grid[row + x];
                if (!v) continue;
                for (let i = -r; i <= r; i++) { const xx = x + i; if (xx >= 0 && xx < size) tmp[row + xx] += v * k[i + r]; }
            }
        }
        for (let y = 0; y < size; y++) {
            for (let x = 0; x < size; x++) {
                const v = tmp[y * size + x];
                if (!v) continue;
                for (let i = -r; i <= r; i++) { const yy = y + i; if (yy >= 0 && yy < size) out[yy * size + x] += v * k[i + r]; }
            }
        }
        return out;
    }

    // Density grid (0..1) for the selected layers. Each layer is normalized on its own,
    // so hundreds of movement samples do not drown out a handful of kills.
    function buildGrid(layers, selected, opts) {
        opts = opts || {};
        const size = opts.size || 256;
        const mapUnits = opts.mapUnits || 816000;
        const cellMeters = mapUnits / 100 / size;
        const total = new Float32Array(size * size);
        let count = 0;
        for (const def of LAYERS) {
            if (!selected.includes(def.id)) continue;
            const pts = (layers && layers[def.id]) || [];
            if (!pts.length) continue;
            const g = new Float32Array(size * size);
            for (const [x, y] of pts) {
                const cx = Math.floor(x / mapUnits * size), cy = Math.floor(y / mapUnits * size);
                if (cx < 0 || cy < 0 || cx >= size || cy >= size) continue;
                g[cy * size + cx] += 1;
                count++;
            }
            const sigma = Math.min(12, Math.max(0.8, def.sigma / cellMeters));
            const b = blur(g, size, sigma);
            let max = 0;
            for (let i = 0; i < b.length; i++) if (b[i] > max) max = b[i];
            if (max > 0) for (let i = 0; i < b.length; i++) total[i] += b[i] / max;
        }
        let max = 0;
        for (let i = 0; i < total.length; i++) if (total[i] > max) max = total[i];
        if (max > 0) for (let i = 0; i < total.length; i++) total[i] /= max;
        return { size, data: total, points: count };
    }

    // Transparent -> blue -> cyan -> green -> yellow -> red
    const STOPS = [
        [0.0, [37, 99, 235]], [0.25, [6, 182, 212]], [0.45, [34, 197, 94]],
        [0.65, [250, 204, 21]], [0.85, [249, 115, 22]], [1.0, [220, 38, 38]],
    ];

    function palette(t) {
        t = Math.min(1, Math.max(0, t));
        let i = 1;
        while (i < STOPS.length - 1 && t > STOPS[i][0]) i++;
        const [t0, c0] = STOPS[i - 1], [t1, c1] = STOPS[i];
        const f = (t - t0) / (t1 - t0 || 1);
        const rgb = c0.map((c, j) => Math.round(c + (c1[j] - c) * f));
        const alpha = t < 0.03 ? 0 : Math.round(255 * Math.min(0.85, 0.2 + t * 0.75));
        return [rgb[0], rgb[1], rgb[2], alpha];
    }

    function colorize(grid) {
        const px = new Uint8ClampedArray(grid.size * grid.size * 4);
        for (let i = 0; i < grid.data.length; i++) {
            const v = grid.data[i];
            if (v < 0.03) continue;
            const c = palette(Math.pow(v, 0.7));
            px.set(c, i * 4);
        }
        return px;
    }

    return { LAYERS, LAYER_IDS, emptyLayers, extractPoints, merge, blur, buildGrid, palette, colorize };
});
