// Run with: node --test tests/
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const fs = require('node:fs');
const T = require('../telemetry.js');

const load = (f) => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', f), 'utf8'));
const dictionaries = {
    damageCauserName: JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'damageCauserName.json'), 'utf8')),
    damageTypeCategory: JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'damageTypeCategory.json'), 'utf8')),
};

test('weapon ids become readable names', () => {
    const name = T.makeNamer(dictionaries);
    assert.strictEqual(name('WeapHK416_C', 'Damage_Gun'), 'M416');
    assert.strictEqual(name('WeapSCAR-L_C', 'Damage_Gun'), 'SCAR-L');
    assert.strictEqual(name('ProjGrenade_C', 'Damage_Explosion_Grenade'), 'Frag Grenade');
    assert.strictEqual(name('BattleRoyaleModeController_Def_C', 'Damage_BlueZone'), 'Blue zone');
    assert.strictEqual(name('PlayerMale_A_C', 'Damage_Punch'), 'Fists');
    assert.strictEqual(name('WeapHK416_C', 'Damage_Groggy'), 'Bleed out');
    assert.strictEqual(name('Dacia_A_01_v2_C', 'Damage_VehicleHit'), 'Dacia');
    // Unknown ids are cleaned up instead of shown raw
    assert.strictEqual(name('WeapSomeNewGun_C', 'Damage_Gun'), 'Some New Gun');
    assert.strictEqual(name('', ''), 'Unknown');
});

test('works without dictionaries', () => {
    const name = T.makeNamer(null);
    assert.strictEqual(name('WeapHK416_C', 'Damage_Gun'), 'M416');
    assert.strictEqual(name('WeapFooBar_C'), 'Foo Bar');
});

test('LogPlayerKillV2: kills, finishes, knocks, revives and deaths', () => {
    const data = T.parse(load('telemetry_v2.json'), ['Alpha', 'Bravo'], { dictionaries, eliminated: () => true });
    const a = data.players.Alpha, b = data.players.Bravo;

    assert.strictEqual(a.knocks.length, 1);
    assert.deepStrictEqual([a.knocks[0].name, a.knocks[0].weapon, a.knocks[0].dist, a.knocks[0].headshot], ['Enemy1', 'M416', 100, true]);
    assert.ok(a.knocks[0].loc);

    assert.deepStrictEqual(a.kills.map((k) => [k.name, k.weapon]), [['Enemy1', 'M416'], ['Enemy2', 'Frag Grenade']]);
    assert.strictEqual(a.kills[0].dist, 100);
    assert.strictEqual(b.kills.length, 0);
    assert.deepStrictEqual(b.finishes.map((k) => [k.name, k.weapon, k.dist]), [['Enemy1', 'Beryl M762', 50]]);

    // Knocked by an enemy: distance is computed from positions when the event has none
    assert.deepStrictEqual([a.knockedBy[0].name, a.knockedBy[0].weapon, a.knockedBy[0].dist], ['Enemy2', 'Kar98k', 206]);
    assert.strictEqual(b.revives[0].name, 'Alpha');
    assert.strictEqual(a.revivedBy[0].name, 'Bravo');

    assert.strictEqual(a.death.unknown, true); // no kill event for Alpha: fallback marker at last position
    assert.strictEqual(b.death.weapon, 'Blue zone');
    assert.strictEqual(b.death.name, 'Blue zone');
    assert.strictEqual(b.death.killer, null);

    // Timeline is sorted by match clock
    const times = a.timeline.filter((e) => e.t !== null).map((e) => e.t);
    assert.deepStrictEqual(times, [...times].sort((x, y) => x - y));
    assert.strictEqual(a.kills[0].t, 210);
});

test('fallback death only for eliminated players with positions', () => {
    const events = load('telemetry_v2.json');
    const won = T.parse(events, ['Alpha'], { eliminated: () => false });
    assert.strictEqual(won.players.Alpha.death, null);
    const lost = T.parse(events, ['Alpha'], { eliminated: () => true });
    assert.strictEqual(lost.players.Alpha.death.unknown, true);
});

test('path starts at landing, ignores plane positions', () => {
    const data = T.parse(load('telemetry_v2.json'), ['Alpha'], {});
    const p = data.players.Alpha.path;
    assert.deepStrictEqual([p[0].x, p[0].y], [200000, 200000]);
    assert.ok(p.every((pt) => pt.x >= 200000));
    assert.ok(data.players.Alpha.jump);
});

test('zone phases and plane path', () => {
    const data = T.parse(load('telemetry_v2.json'), ['Alpha'], { mapUnits: 816000 });
    assert.strictEqual(data.phases.length, 2);
    assert.strictEqual(data.phases[1].white.r, 110000);
    assert.ok(data.planePath);
    assert.notDeepStrictEqual(data.planePath.start, data.planePath.end);
});

test('legacy LogPlayerKill is still supported', () => {
    const data = T.parse(load('telemetry_legacy.json'), ['Alpha'], { dictionaries });
    const k = data.players.Alpha.kills;
    assert.strictEqual(k.length, 1);
    assert.strictEqual(k[0].weapon, 'Some New Gun');
    assert.strictEqual(k[0].dist, 50); // computed from positions: 3000/4000 -> 5000cm
});

test('team kills are flagged and self-kills are not counted', () => {
    const ch = (n, team) => ({ name: n, teamId: team, location: { x: 1, y: 1, z: 1 } });
    const events = [
        { _T: 'LogMatchStart', _D: '2026-01-01T00:00:00Z' },
        { _T: 'LogPlayerKillV2', _D: '2026-01-01T00:01:00Z', victim: ch('Bravo', 1), killer: ch('Alpha', 1), killerDamageInfo: { damageCauserName: 'WeapAK47_C', damageTypeCategory: 'Damage_Gun' } },
        { _T: 'LogPlayerKillV2', _D: '2026-01-01T00:02:00Z', victim: ch('Alpha', 1), killer: ch('Alpha', 1), killerDamageInfo: { damageCauserName: 'ProjGrenade_C', damageTypeCategory: 'Damage_Explosion_Grenade' }, isSuicide: true },
    ];
    const data = T.parse(events, ['Alpha', 'Bravo'], {});
    assert.strictEqual(data.players.Alpha.kills.length, 1);
    assert.strictEqual(data.players.Alpha.kills[0].teamKill, true);
    assert.strictEqual(data.players.Alpha.death.suicide, true);
    assert.strictEqual(data.players.Bravo.death.killer, 'Alpha');
});
