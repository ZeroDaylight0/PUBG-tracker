// Run with: node --test tests/
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const H = require('../heatmap.js');

const load = (...p) => JSON.parse(fs.readFileSync(path.join(__dirname, ...p), 'utf8'));

test('extractPoints gives the same layers as heatmap.py', () => {
    const events = load('fixtures', 'telemetry_v2.json');
    const expected = load('fixtures', 'heatmap_expected.json');
    // Account id wins over a changed name; the name is the fallback when there is no account id
    assert.deepStrictEqual(H.extractPoints(events, { account_id: 'acc_Alpha', name: 'Renamed' }), expected.by_account);
    assert.deepStrictEqual(H.extractPoints(events, { name: 'Alpha' }), expected.by_name);
    assert.deepStrictEqual(H.extractPoints(events, { account_id: 'acc_Bravo', name: 'Bravo' }), expected.bravo);
});

test('layers: landing, kills, knocks, deaths, knocked, movement', () => {
    const pts = H.extractPoints(load('fixtures', 'telemetry_v2.json'), { name: 'Alpha' });
    assert.deepStrictEqual(Object.keys(pts), ['landing', 'kills', 'knocks', 'deaths', 'knocked', 'movement']);
    assert.deepStrictEqual(pts.landing, [[200000, 200000]]);
    assert.strictEqual(pts.kills.length, 2);
    assert.strictEqual(pts.knocks.length, 1);
    assert.strictEqual(pts.knocked.length, 1);
    // Movement starts at the landing: the position in the plane is skipped
    assert.strictEqual(pts.movement.length, 5);
    assert.ok(pts.movement.every(([x]) => x >= 200000));
    assert.deepStrictEqual(H.extractPoints(null, { name: 'x' }), H.emptyLayers());
});

test('merge aggregates several matches', () => {
    const a = { landing: [[1, 1]], kills: [[2, 2]] };
    const b = { landing: [[3, 3]], movement: [[4, 4], [5, 5]] };
    const all = H.merge(H.merge({}, a), b);
    assert.deepStrictEqual(all.landing, [[1, 1], [3, 3]]);
    assert.strictEqual(all.movement.length, 2);
    assert.strictEqual(all.deaths.length, 0);
});

test('buildGrid: normalized density, hottest cell at the cluster, layers selectable', () => {
    const units = 816000, size = 64;
    const cell = (x, y) => [Math.round((x + 0.5) * units / size), Math.round((y + 0.5) * units / size)];
    const layers = { kills: [cell(10, 10), cell(10, 10), cell(10, 10), cell(50, 50)], movement: [cell(30, 30)] };
    const g = H.buildGrid(layers, ['kills'], { size, mapUnits: units });
    assert.strictEqual(g.points, 4);
    let best = 0;
    for (let i = 0; i < g.data.length; i++) if (g.data[i] > g.data[best]) best = i;
    assert.strictEqual(best, 10 * size + 10);
    assert.ok(Math.abs(g.data[best] - 1) < 1e-6);
    assert.ok(g.data[50 * size + 50] > 0 && g.data[50 * size + 50] < 0.5);
    assert.strictEqual(g.data[30 * size + 30], 0); // movement not selected

    const both = H.buildGrid(layers, ['kills', 'movement'], { size, mapUnits: units });
    assert.strictEqual(both.points, 5);
    assert.ok(both.data[30 * size + 30] > 0.3); // each layer is normalized on its own
    const none = H.buildGrid(layers, [], { size, mapUnits: units });
    assert.strictEqual(none.points, 0);
    assert.ok(none.data.every((v) => v === 0));
    // Points outside the map are ignored
    assert.strictEqual(H.buildGrid({ kills: [[-5, 5], [units + 1, 5]] }, ['kills'], { size, mapUnits: units }).points, 0);
});

test('blur keeps the total mass', () => {
    const size = 32, g = new Float32Array(size * size);
    g[16 * size + 16] = 10;
    const b = H.blur(g, size, 2);
    const sum = b.reduce((s, v) => s + v, 0);
    assert.ok(Math.abs(sum - 10) < 1e-3);
});

test('colorize: transparent when empty, opaque and red when hot', () => {
    assert.strictEqual(H.palette(0)[3], 0);
    const hot = H.palette(1);
    assert.ok(hot[0] > 200 && hot[3] > 200);
    const grid = { size: 2, data: new Float32Array([0, 0.5, 1, 0.01]) };
    const px = H.colorize(grid);
    assert.strictEqual(px.length, 16);
    assert.strictEqual(px[3], 0);
    assert.ok(px[7] > 0 && px[11] > px[7]);
    assert.strictEqual(px[15], 0);
});
