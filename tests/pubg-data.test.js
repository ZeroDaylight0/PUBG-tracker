// Run with: node --test tests/
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const D = require('../pubg-data.js');
const T = require('../telemetry.js');

const root = path.join(__dirname, '..');
const json = (...p) => JSON.parse(fs.readFileSync(path.join(root, ...p), 'utf8'));

test('summarizeMatch matches the Python server output', () => {
    const m = D.summarizeMatch(json('tests', 'fixtures', 'match.json'), 'account.a', 'alpha');
    assert.strictEqual(m.searched_nick, 'Alpha');
    assert.deepStrictEqual(m.team_stats.map((t) => t.name), ['Alpha', 'Bravo']);
    const a = m.team_stats[0];
    assert.deepStrictEqual([a.kills, a.knocks, a.damage, a.dist], [3, 4, 413, 3000]);
    assert.deepStrictEqual([m.map_name, m.date, m.rank, m.players], ['Erangel', '30.10 12:34', 5, 3]);
    assert.ok(m.telemetry.startsWith('https://telemetry-cdn.pubg.com/'));
});

test('loadMatchesDirect calls the PUBG API with the key and readable errors', async () => {
    const calls = [];
    const match = json('tests', 'fixtures', 'match.json');
    const player = { data: [{ id: 'account.a', attributes: { name: 'Alpha' }, relationships: { matches: { data: [{ id: 'm1' }, { id: 'bad' }] } } }] };
    const fake = async (url, opts) => {
        calls.push([url, opts.headers.Authorization]);
        if (url.includes('/players?')) return { ok: true, status: 200, json: async () => player };
        if (url.endsWith('/m1')) return { ok: true, status: 200, json: async () => match };
        return { ok: false, status: 500, json: async () => ({}) };
    };
    const res = await D.loadMatchesDirect({ key: 'KEY', nickname: 'al pha', platform: 'steam', count: 99 }, fake);
    assert.strictEqual(res.length, 1);
    assert.strictEqual(calls[0][0], 'https://api.pubg.com/shards/steam/players?filter[playerNames]=al%20pha');
    assert.ok(calls.every((c) => c[1] === 'Bearer KEY'));

    const deny = async () => ({ ok: false, status: 401, json: async () => ({}) });
    await assert.rejects(D.loadMatchesDirect({ key: 'x', nickname: 'a', platform: 'steam' }, deny), /401/);
    const offline = async () => { throw new TypeError('Failed to fetch'); };
    await assert.rejects(D.loadMatchesDirect({ key: 'x', nickname: 'a', platform: 'steam' }, offline), /Could not reach/);
});

test('browser source builds direct map URLs', () => {
    const src = new D.BrowserSource({ matches: [], demo: true });
    assert.strictEqual(src.mode, 'demo');
    assert.match(src.mapUrl('Baltic_Main', 'low'), /raw\.githubusercontent\.com\/pubg\/api-assets\/master\/Assets\/Maps\/Erangel_Main_Low_Res\.png$/);
    assert.match(src.mapUrl('Desert_Main', 'high'), /media\.githubusercontent\.com\/.*Miramar_Main_High_Res\.png$/);
    assert.strictEqual(src.mapUrl('Nope', 'low'), '');
});

test('demo matches: telemetry agrees with the match stats', () => {
    const matches = json('demo', 'matches.json');
    assert.ok(matches.length >= 2);
    const dictionaries = { damageCauserName: json('data', 'damageCauserName.json'), damageTypeCategory: json('data', 'damageTypeCategory.json') };
    for (const m of matches) {
        const events = json(m.telemetry);
        const ranks = Object.fromEntries(m.team_stats.map((t) => [t.name, t.rank]));
        const data = T.parse(events, m.team_stats.map((t) => t.name), { dictionaries, mapUnits: T.MAP_SIZES[m.map], eliminated: (n) => ranks[n] !== 1 });
        assert.ok(data.phases.length >= 5, 'zone phases');
        assert.ok(data.planePath, 'plane path');
        for (const s of m.team_stats) {
            const p = data.players[s.name];
            assert.strictEqual(p.kills.length, s.kills, `${m.map_name} ${s.name} kills`);
            assert.strictEqual(p.knocks.length, s.knocks, `${m.map_name} ${s.name} knocks`);
            assert.ok(p.landing && p.path.length > 10, `${s.name} path`);
            assert.ok([...p.kills, ...p.knocks].every((k) => k.loc && !/^Weap|_C$/.test(k.weapon)), 'readable weapons');
            if (s.rank !== 1) assert.ok(p.death && !p.death.unknown, `${s.name} death`);
        }
    }
});

test('summarizeMatch exposes account ids for the heatmap', () => {
    const m = D.summarizeMatch(json('tests', 'fixtures', 'match.json'), 'account.a', 'alpha');
    assert.deepStrictEqual(m.team_stats.map((t) => t.account_id), ['account.a', 'account.b']);
});

async function withFetch(fake, fn) {
    const orig = globalThis.fetch;
    globalThis.fetch = fake;
    try { return await fn(); } finally { globalThis.fetch = orig; }
}

test('server source: match ids, history and heatmap URLs are encoded', async () => {
    const urls = [];
    const fake = async (url) => { urls.push(url); return { ok: true, status: 200, json: async () => ({}) }; };
    const src = new D.ServerSource();
    await withFetch(fake, async () => {
        await src.meta(2);
        await src.meta({ match_id: 'a&b=c', account_id: 'account.x' });
        await src.telemetry({ match_id: 'm1', telemetry: 'https://evil.example/x.json' });
        await src.history({ player: 'A B', map: '', mode: 'squad-fpp', page: 2 });
        await src.heatmapAll({ map: 'Baltic_Main' }, { account_id: 'account.a', name: 'Alpha' });
    });
    assert.deepStrictEqual(urls, [
        'match_meta?i=2',
        'match_meta?match_id=a%26b%3Dc&account_id=account.x',
        'fetch_telemetry?match_id=m1', // the server looks the URL up itself, the browser never sends one
        'api/history?player=A%20B&mode=squad-fpp&page=2',
        'api/heatmap?account_id=account.a&map=Baltic_Main',
    ]);
});

test('browser source only downloads telemetry from the official CDN or the demo', async () => {
    assert.ok(D.TELEMETRY_URL.test('https://telemetry-cdn.pubg.com/bluehole-pubg/steam/2026/x.json'));
    assert.ok(D.TELEMETRY_URL.test('demo/telemetry-1.json'));
    for (const bad of ['https://telemetry-cdn.pubg.com.evil.com/x', 'http://telemetry-cdn.pubg.com/x', 'javascript:alert(1)', '//evil.com/x', 'demo/../app.py']) {
        assert.ok(!D.TELEMETRY_URL.test(bad), bad);
    }
    const src = new D.BrowserSource({ matches: [], demo: false });
    await assert.rejects(src.telemetry({ telemetry: 'https://evil.example/x.json' }), /not allowed/);
});

test('browser source aggregates the heatmap over session matches on the same map', async () => {
    const H = require('../heatmap.js');
    const matches = json('demo', 'matches.json');
    const extra = Object.assign({}, matches[0], { match_id: 'copy', telemetry: matches[0].telemetry });
    const src = new D.BrowserSource({ matches: [...matches, extra], demo: true });
    const fetched = [];
    const fake = async (url) => { fetched.push(url); return { ok: true, status: 200, json: async () => json(url) }; };
    const player = { name: 'DemoPlayer', account_id: '' };
    const res = await withFetch(fake, () => src.heatmapAll(matches[0], player, H.extractPoints));
    assert.strictEqual(res.matches, 2); // two Erangel matches, Miramar is skipped
    const single = H.extractPoints(json(matches[0].telemetry), player);
    assert.strictEqual(res.layers.landing.length, 2 * single.landing.length);
    assert.strictEqual(res.layers.kills.length, 2 * single.kills.length);
    // Second call is served from the cache
    await withFetch(fake, () => src.heatmapAll(matches[0], player, H.extractPoints));
    assert.strictEqual(fetched.length, 2);
});
