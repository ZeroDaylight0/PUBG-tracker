// Data sources for PUBG Tracker.
//  - "server": the local Python proxy (app.py) keeps the API key on the machine.
//  - "browser": static hosting (GitHub Pages). The browser talks to api.pubg.com directly
//    with the visitor's own key, which never leaves their browser.
//  - "demo": bundled sample matches, no key needed.
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.PubgData = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MAP_NAMES = {
        Baltic_Main: 'Erangel', Erangel_Main: 'Erangel', Desert_Main: 'Miramar', Savage_Main: 'Sanhok',
        Summerland_Main: 'Karakin', Chimera_Main: 'Paramo', Tiger_Main: 'Taego', Kiki_Main: 'Deston',
        Neon_Main: 'Rondo', DihorOtok_Main: 'Vikendi', Heaven_Main: 'Haven', Range_Main: 'Camp Jackal',
    };
    const MAP_ASSETS = {
        Baltic_Main: 'Erangel_Main', Erangel_Main: 'Erangel_Main', Desert_Main: 'Miramar_Main', Savage_Main: 'Sanhok_Main',
        Summerland_Main: 'Karakin_Main', Chimera_Main: 'Paramo_Main', Tiger_Main: 'Taego_Main', Kiki_Main: 'Deston_Main',
        Neon_Main: 'Rondo_Main', DihorOtok_Main: 'Vikendi_Main', Heaven_Main: 'Haven_Main', Range_Main: 'Camp_Jackal_Main',
    };
    const MAP_URLS = {
        low: (n) => `https://raw.githubusercontent.com/pubg/api-assets/master/Assets/Maps/${n}_Low_Res.png`,
        high: (n) => `https://media.githubusercontent.com/media/pubg/api-assets/master/Assets/Maps/${n}_High_Res.png`,
    };
    const STORE_KEY = 'pubgTrackerSession';
    const KEY_KEY = 'pubgTrackerApiKey';
    const MAX_MATCHES = 20;

    class PubgApiError extends Error {}

    function formatDate(iso) {
        const d = new Date(iso);
        if (isNaN(d)) return iso || '';
        const p = (n) => String(n).padStart(2, '0');
        return `${p(d.getUTCDate())}.${p(d.getUTCMonth() + 1)} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
    }

    function participantStats(s) {
        return {
            name: s.name || '',
            kills: s.kills || 0,
            knocks: s.DBNOs || 0,
            assists: s.assists || 0,
            headshots: s.headshotKills || 0,
            damage: Math.round(s.damageDealt || 0),
            revives: s.revives || 0,
            longest: Math.round(s.longestKill || 0),
            survived: Math.floor(s.timeSurvived || 0),
            rank: s.winPlace || 0,
            dist: Math.floor((s.walkDistance || 0) + (s.rideDistance || 0) + (s.swimDistance || 0)),
        };
    }

    // Same as summarize_match() in app.py
    function summarizeMatch(match, playerId, searchedName) {
        const included = match.included || [];
        const participants = new Map(included.filter((i) => i.type === 'participant').map((p) => [p.id, p]));
        const me = [...participants.values()].find((p) => p.attributes.stats.playerId === playerId);
        if (!me) throw new PubgApiError('Player is missing from match roster');
        const roster = included.find((r) => r.type === 'roster'
            && r.relationships.participants.data.some((p) => p.id === me.id));
        const refs = roster ? roster.relationships.participants.data : [{ id: me.id }];
        const team = refs.filter((r) => participants.has(r.id)).map((r) => participantStats(participants.get(r.id).attributes.stats));
        const myName = me.attributes.stats.name || searchedName;
        team.sort((a, b) => (a.name !== myName) - (b.name !== myName) || b.kills - a.kills || a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

        const attrs = match.data.attributes;
        const asset = included.find((i) => i.type === 'asset');
        const mine = participantStats(me.attributes.stats);
        return {
            match_id: match.data.id,
            telemetry: asset ? asset.attributes.URL : null,
            searched_nick: myName,
            team_stats: team,
            map: attrs.mapName,
            map_name: MAP_NAMES[attrs.mapName] || attrs.mapName,
            mode: attrs.gameMode || '',
            duration: attrs.duration || 0,
            date: formatDate(attrs.createdAt),
            rank: mine.rank, kills: mine.kills, knocks: mine.knocks, damage: mine.damage,
            players: participants.size,
        };
    }

    function listItem(m, i) {
        return { id: i + 1, map: m.map_name, mode: m.mode, date: m.date, rank: m.rank, kills: m.kills, knocks: m.knocks, damage: m.damage };
    }

    async function apiGet(url, key, fetchImpl) {
        let res;
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                res = await fetchImpl(url, { headers: { Authorization: `Bearer ${key}`, Accept: 'application/vnd.api+json' } });
            } catch (e) {
                throw new PubgApiError('Could not reach api.pubg.com (network or browser blocked the request)');
            }
            if (res.status !== 429 || attempt === 2) break;
            await new Promise((r) => setTimeout(r, 5000));
        }
        if (res.status === 401) throw new PubgApiError('PUBG API rejected the key (401). Check your API key.');
        if (res.status === 404) throw new PubgApiError('Not found (404). Check the nickname (it is case-sensitive) and the platform.');
        if (res.status === 429) throw new PubgApiError('PUBG API rate limit reached (429). Wait a minute and try again.');
        if (!res.ok) throw new PubgApiError(`PUBG API error ${res.status}`);
        return res.json();
    }

    // Browser version of load_matches() in app.py
    async function loadMatchesDirect({ key, nickname, platform, count }, fetchImpl) {
        fetchImpl = fetchImpl || fetch.bind(globalThis);
        count = Math.max(1, Math.min(MAX_MATCHES, parseInt(count, 10) || 5));
        const base = `https://api.pubg.com/shards/${encodeURIComponent(platform)}`;
        const r = await apiGet(`${base}/players?filter[playerNames]=${encodeURIComponent(nickname)}`, key, fetchImpl);
        if (!r.data || !r.data.length) throw new PubgApiError('Player not found');
        const player = r.data[0];
        const name = player.attributes.name || nickname;
        const mids = player.relationships.matches.data.slice(0, count).map((m) => m.id);
        if (!mids.length) throw new PubgApiError('No matches in the last 14 days');
        const results = await Promise.all(mids.map((mid) =>
            apiGet(`${base}/matches/${mid}`, key, fetchImpl).then((m) => summarizeMatch(m, player.id, name)).catch(() => null)));
        const matches = results.filter(Boolean);
        if (!matches.length) throw new PubgApiError('Could not load any match');
        return matches;
    }

    const storage = {
        get(kind, k) { try { return JSON.parse(globalThis[kind].getItem(k)); } catch (e) { return null; } },
        set(kind, k, v) { try { globalThis[kind].setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } },
        del(kind, k) { try { globalThis[kind].removeItem(k); } catch (e) { /* ignore */ } },
    };

    async function getJson(url, what) {
        let res;
        try { res = await fetch(url); } catch (e) {
            throw new Error(`${what}: the download was blocked (network or CORS)`);
        }
        if (!res.ok) throw new Error(`${what} failed (${res.status})`);
        return res.json();
    }

    class ServerSource {
        constructor() { this.mode = 'server'; }
        list() { return getJson('matches_list', 'Match list'); }
        meta(i) { return getJson(`match_meta?i=${i}`, 'Match'); }
        telemetry(meta) { return getJson(`fetch_telemetry?url=${encodeURIComponent(meta.telemetry)}`, 'Telemetry download'); }
        mapUrl(map, q) { return `fetch_image?map_id=${encodeURIComponent(map)}&q=${q}`; }
        dictionaries() { return getJson('dictionaries', 'Dictionaries').catch(() => ({})); }
    }

    // Matches are kept in sessionStorage; telemetry and map images are fetched straight from PUBG / GitHub
    class BrowserSource {
        constructor(session) { this.session = session; this.mode = session.demo ? 'demo' : 'browser'; }
        async list() { return this.session.matches.map(listItem); }
        async meta(i) {
            const m = this.session.matches[i - 1];
            if (!m) throw new Error('Match not found');
            return m;
        }
        telemetry(meta) {
            return getJson(meta.telemetry, 'Telemetry download');
        }
        mapUrl(map, q) {
            const name = MAP_ASSETS[map];
            return name ? MAP_URLS[q](name) : '';
        }
        dictionaries() {
            return Promise.all(['damageCauserName', 'damageTypeCategory'].map((k) => getJson(`data/${k}.json`, k).catch(() => ({}))))
                .then(([damageCauserName, damageTypeCategory]) => ({ damageCauserName, damageTypeCategory }));
        }
    }

    // Is the local Python server behind this page?
    async function detectMode() {
        try {
            const r = await fetch('mode', { cache: 'no-store' });
            if (r.ok && (await r.json()).mode === 'server') return 'server';
        } catch (e) { /* static hosting */ }
        return 'browser';
    }

    async function getSource() {
        const session = storage.get('sessionStorage', STORE_KEY);
        if (session && session.matches && session.matches.length) return new BrowserSource(session);
        if ((await detectMode()) === 'server') return new ServerSource();
        return null;
    }

    function saveSession(matches, demo) {
        return storage.set('sessionStorage', STORE_KEY, { matches, demo: !!demo });
    }
    function clearSession() { storage.del('sessionStorage', STORE_KEY); }

    async function loadDemo() {
        const matches = await getJson('demo/matches.json', 'Demo data');
        saveSession(matches, true);
        return matches;
    }

    const apiKey = {
        get() { return storage.get('localStorage', KEY_KEY) || storage.get('sessionStorage', KEY_KEY) || ''; },
        set(key, remember) {
            storage.del('localStorage', KEY_KEY); storage.del('sessionStorage', KEY_KEY);
            storage.set(remember ? 'localStorage' : 'sessionStorage', KEY_KEY, key);
        },
        remembered() { return !!storage.get('localStorage', KEY_KEY); },
    };

    return {
        MAP_NAMES, MAP_ASSETS, PubgApiError, summarizeMatch, participantStats, formatDate, loadMatchesDirect,
        ServerSource, BrowserSource, detectMode, getSource, saveSession, clearSession, loadDemo, apiKey, listItem,
    };
});
