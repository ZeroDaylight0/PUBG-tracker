// Telemetry parsing for PUBG Tracker. Works in the browser (window.PubgTelemetry) and in Node (tests).
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.PubgTelemetry = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // Map size in centimeters (telemetry units)
    const MAP_SIZES = {
        Baltic_Main: 816000, Erangel_Main: 816000, Desert_Main: 816000, Savage_Main: 408000,
        Summerland_Main: 204000, Chimera_Main: 306000, Tiger_Main: 816000, Kiki_Main: 816000,
        Neon_Main: 816000, DihorOtok_Main: 816000, Heaven_Main: 102000, Range_Main: 204000,
    };

    // Readable names for weapons that are newer than the bundled dictionary
    const EXTRA_CAUSERS = {
        WeapFamasG2_C: 'FAMAS', WeapMk47Mutant_C: 'Mk47 Mutant', WeapP90_C: 'P90', WeapK2_C: 'K2',
        WeapACE32_C: 'ACE32', WeapJS9_C: 'JS9', WeapL6_C: 'Lynx AMR', WeapDragunov_C: 'Dragunov',
        WeapMosinNagant_C: 'Mosin-Nagant', WeapAUG_C: 'AUG', WeapM79_C: 'M79', WeapFlareGun_C: 'Flare Gun',
        WeapStunGun_C: 'Stun Gun', WeapSpikeTrap_C: 'Spike Trap', WeapBlueChipDetector_C: 'Blue Chip Detector',
        WeapMortar_C: 'Mortar', WeapCrossbow_1_C: 'Crossbow', WeapPanzerFaust100M1_C: 'Panzerfaust',
        WeapMG3_C: 'MG3', WeapDuncansHK416_C: 'M416', WeapHK416_C: 'M416', WeapAK47_C: 'AKM',
        WeapM16A4_C: 'M16A4', WeapQBZ95_C: 'QBZ', WeapG36C_C: 'G36C',
        WeapBerylM762_C: 'Beryl M762', WeapGroza_C: 'Groza', WeapUZI_C: 'Micro UZI', WeapUMP_C: 'UMP45',
        WeapVector_C: 'Vector', WeapThompson_C: 'Tommy Gun', WeapBizonPP19_C: 'PP-19 Bizon', WeapMP5K_C: 'MP5K',
        WeapMP9_C: 'MP9', WeapKar98k_C: 'Kar98k', WeapM24_C: 'M24', WeapAWM_C: 'AWM', WeapWin94_C: 'Win94',
        WeapSKS_C: 'SKS', WeapMini14_C: 'Mini 14', WeapMk14_C: 'Mk14', WeapFNFal_C: 'SLR', WeapQBU88_C: 'QBU',
        WeapMk12_C: 'Mk12', WeapVSS_C: 'VSS', WeapM249_C: 'M249', WeapDP28_C: 'DP-28', WeapSaiga12_C: 'S12K',
        WeapBerreta686_C: 'S686', WeapWinchester_C: 'S1897', WeapDP12_C: 'DBS', WeapOriginS12_C: 'O12',
        WeapSawnoff_C: 'Sawed-off', WeapG18_C: 'P18C', WeapM1911_C: 'P1911', WeapM9_C: 'P92',
        WeapNagantM1895_C: 'R1895', WeapRhino_C: 'R45', WeapDesertEagle_C: 'Deagle', Weapvz61Skorpion_C: 'Skorpion',
        PlayerMale_A_C: 'Fists', PlayerFemale_A_C: 'Fists',
    };

    const CATEGORY_FALLBACK = {
        Damage_BlueZone: 'Blue zone', Damage_Groggy: 'Bleed out', Damage_Drown: 'Drowning',
        Damage_Instant_Fall: 'Fall damage', Damage_Explosion_RedZone: 'Red zone', Damage_Explosion_BlackZone: 'Black zone',
        Damage_VehicleHit: 'Vehicle', Damage_VehicleCrashHit: 'Vehicle crash', Damage_Explosion_Vehicle: 'Vehicle explosion',
        Damage_Punch: 'Fists', Damage_Molotov: 'Molotov', Damage_Explosion_Grenade: 'Frag Grenade',
    };

    // Causers that only say "who" but not "what" — prefer the damage category for them
    const GENERIC_CAUSERS = /^(None|PlayerMale_A_C|PlayerFemale_A_C|BattleRoyaleModeController_.*|RacingModeContorller_.*)$/;

    function prettifyId(id) {
        return String(id)
            .replace(/^(Weap|BP_|Proj|Item_Weapon_)/, '')
            .replace(/(_\d+)?_C$/, '')
            .replace(/_/g, ' ')
            .replace(/([a-z])([A-Z])/g, '$1 $2')
            .trim() || 'Unknown';
    }

    function makeNamer(dict) {
        const causers = Object.assign({}, (dict && dict.damageCauserName) || {}, EXTRA_CAUSERS);
        const categories = Object.assign({}, (dict && dict.damageTypeCategory) || {}, CATEGORY_FALLBACK);
        return function weaponName(causer, category) {
            if (category === 'Damage_Groggy') return 'Bleed out';
            if (category === 'Damage_Punch') return 'Fists';
            if (causer && !GENERIC_CAUSERS.test(causer)) {
                return causers[causer] || prettifyId(causer);
            }
            if (category) return categories[category] || prettifyId(category.replace(/^Damage_/, ''));
            if (causer) return causers[causer] || prettifyId(causer);
            return 'Unknown';
        };
    }

    const hasName = (c) => !!(c && c.name);
    const toMeters = (cm) => Math.round((cm || 0) / 100);

    function distance(a, b) {
        if (!a || !b) return 0;
        return Math.hypot((a.x || 0) - (b.x || 0), (a.y || 0) - (b.y || 0), (a.z || 0) - (b.z || 0));
    }

    function damageInfo(info, from, to) {
        info = info || {};
        let dist = info.distance;
        if (!(dist > 0)) dist = distance(from && from.location, to && to.location);
        return {
            causer: info.damageCauserName || '',
            category: info.damageTypeCategory || '',
            headshot: info.damageReason === 'HeadShot',
            dist: toMeters(dist),
        };
    }

    // Converts LogPlayerKillV2 (current API) and LogPlayerKill (old matches) to one shape
    function normalizeKill(ev) {
        if (ev._T === 'LogPlayerKillV2') {
            const killer = hasName(ev.killer) ? ev.killer : null;
            const finisher = hasName(ev.finisher) ? ev.finisher : null;
            const dbnoMaker = hasName(ev.dBNOMaker) ? ev.dBNOMaker : null;
            const creditInfo = damageInfo(ev.killerDamageInfo, killer, ev.victim);
            const finishInfo = damageInfo(ev.finishDamageInfo, finisher, ev.victim);
            // Bleed-outs and zone deaths often have no killerDamageInfo: fall back to the finishing blow
            const main = creditInfo.causer || creditInfo.category ? creditInfo : finishInfo;
            return { time: ev._D, victim: ev.victim, killer, finisher, dbnoMaker, info: main, finishInfo, isSuicide: !!ev.isSuicide };
        }
        if (ev._T === 'LogPlayerKill') {
            const killer = hasName(ev.killer) ? ev.killer : null;
            const info = damageInfo({
                damageCauserName: ev.damageCauserName, damageTypeCategory: ev.damageTypeCategory,
                damageReason: ev.damageReason, distance: ev.distance,
            }, killer, ev.victim);
            return { time: ev._D, victim: ev.victim, killer, finisher: killer, dbnoMaker: null, info, finishInfo: info, isSuicide: false };
        }
        return null;
    }

    function sameTeam(a, b) {
        return !!(a && b && a.teamId !== undefined && a.teamId === b.teamId);
    }

    function parse(events, teamNames, options) {
        options = options || {};
        const weaponName = makeNamer(options.dictionaries);
        const mapUnits = options.mapUnits || 816000;
        const team = new Set(teamNames);

        const matchStart = events.find((e) => e._T === 'LogMatchStart');
        const startTime = matchStart ? Date.parse(matchStart._D) : (events.length ? Date.parse(events[0]._D) : 0);
        const clock = (iso) => Math.max(0, Math.round((Date.parse(iso) - startTime) / 1000));

        // --- Zone phases: a new phase starts whenever a new white circle appears
        const phases = [];
        let prevWhite = null;
        for (const ev of events) {
            if (ev._T !== 'LogGameStatePeriodic') continue;
            const gs = ev.gameState;
            if (!gs || !(gs.safetyZoneRadius > 0)) continue;
            const white = { x: gs.safetyZonePosition.x, y: gs.safetyZonePosition.y, r: gs.safetyZoneRadius };
            const blue = { x: gs.poisonGasWarningPosition.x, y: gs.poisonGasWarningPosition.y, r: gs.poisonGasWarningRadius };
            const changed = !prevWhite
                || Math.abs(white.r - prevWhite.r) > mapUnits * 0.005
                || Math.hypot(white.x - prevWhite.x, white.y - prevWhite.y) > mapUnits * 0.005;
            if (changed) phases.push({ time: ev._D, t: clock(ev._D), white, blue });
            prevWhite = white;
        }

        // --- Plane path: everybody who jumps leaves the aircraft on its flight line
        const jumps = events.filter((e) => e._T === 'LogVehicleLeave' && e.vehicle && e.vehicle.vehicleType === 'TransportAircraft' && e.character);
        let planePath = null;
        if (jumps.length >= 2) {
            // The two jump points that are farthest apart define the line best
            let a = jumps[0].character.location, b = a, best = -1;
            for (const j of jumps) {
                const d = distance(a, j.character.location);
                if (d > best) { best = d; b = j.character.location; }
            }
            best = -1;
            for (const j of jumps) {
                const d = distance(b, j.character.location);
                if (d > best) { best = d; a = j.character.location; }
            }
            if (best > 0) planePath = { start: a, end: b };
        }

        // --- Per-player data
        const players = {};
        const lastPos = {};
        teamNames.forEach((n) => {
            players[n] = { path: [], landing: null, jump: null, kills: [], finishes: [], knocks: [], knockedBy: [], revives: [], revivedBy: [], death: null };
        });

        for (const ev of events) {
            switch (ev._T) {
                case 'LogPlayerPosition': {
                    const n = ev.character && ev.character.name;
                    if (!team.has(n)) break;
                    lastPos[n] = ev.character.location;
                    // Skip the lobby/plane part (isGame < 1 before landing)
                    if (players[n].landing) players[n].path.push(Object.assign({ t: clock(ev._D) }, ev.character.location));
                    break;
                }
                case 'LogParachuteLanding': {
                    const n = ev.character && ev.character.name;
                    if (team.has(n) && !players[n].landing) {
                        players[n].landing = Object.assign({ t: clock(ev._D) }, ev.character.location);
                        players[n].path.push(players[n].landing);
                    }
                    break;
                }
                case 'LogVehicleLeave': {
                    const n = ev.character && ev.character.name;
                    if (team.has(n) && ev.vehicle && ev.vehicle.vehicleType === 'TransportAircraft' && !players[n].jump) {
                        players[n].jump = ev.character.location;
                    }
                    break;
                }
                case 'LogPlayerMakeGroggy': {
                    const info = damageInfo({
                        damageCauserName: ev.damageCauserName, damageTypeCategory: ev.damageTypeCategory,
                        damageReason: ev.damageReason, distance: ev.distance,
                    }, ev.attacker, ev.victim);
                    const base = {
                        time: ev._D, t: clock(ev._D), loc: (ev.victim && ev.victim.location) || null,
                        weapon: weaponName(info.causer, info.category), dist: info.dist, headshot: info.headshot,
                    };
                    const a = hasName(ev.attacker) ? ev.attacker.name : null;
                    const v = hasName(ev.victim) ? ev.victim.name : null;
                    if (team.has(a) && v && a !== v) {
                        players[a].knocks.push(Object.assign({ type: 'KNOCK', name: v, teamKill: sameTeam(ev.attacker, ev.victim) }, base));
                    }
                    if (team.has(v)) {
                        players[v].knockedBy.push(Object.assign({ type: 'KNOCKED', name: a || weaponName(info.causer, info.category) }, base));
                    }
                    break;
                }
                case 'LogPlayerRevive': {
                    const r = ev.reviver && ev.reviver.name, v = ev.victim && ev.victim.name;
                    const base = { time: ev._D, t: clock(ev._D), loc: (ev.victim && ev.victim.location) || null, weapon: '', dist: 0 };
                    if (team.has(r)) players[r].revives.push(Object.assign({ type: 'REVIVE', name: v }, base));
                    if (team.has(v)) players[v].revivedBy.push(Object.assign({ type: 'REVIVED', name: r }, base));
                    break;
                }
                case 'LogPlayerKill':
                case 'LogPlayerKillV2': {
                    const k = normalizeKill(ev);
                    const vName = k.victim && k.victim.name;
                    const loc = (k.victim && k.victim.location) || lastPos[vName] || null;
                    const base = { time: k.time, t: clock(k.time), loc, name: vName };
                    if (k.killer && team.has(k.killer.name) && k.killer.name !== vName) {
                        players[k.killer.name].kills.push(Object.assign({}, base, {
                            type: 'KILL', weapon: weaponName(k.info.causer, k.info.category), dist: k.info.dist,
                            headshot: k.info.headshot, teamKill: sameTeam(k.killer, k.victim),
                        }));
                    }
                    // Finishing someone whose kill was credited to another player (e.g. a teammate's knock)
                    if (k.finisher && team.has(k.finisher.name) && k.finisher.name !== vName
                        && (!k.killer || k.finisher.name !== k.killer.name)) {
                        players[k.finisher.name].finishes.push(Object.assign({}, base, {
                            type: 'FINISH', weapon: weaponName(k.finishInfo.causer, k.finishInfo.category),
                            dist: k.finishInfo.dist, headshot: k.finishInfo.headshot,
                        }));
                    }
                    if (team.has(vName)) {
                        const by = k.killer || k.finisher || k.dbnoMaker;
                        players[vName].death = Object.assign({}, base, {
                            type: 'DEATH',
                            name: k.isSuicide || (by && by.name === vName) ? vName : (by ? by.name : weaponName(k.info.causer, k.info.category)),
                            killer: by ? by.name : null,
                            weapon: weaponName(k.info.causer, k.info.category), dist: k.info.dist, headshot: k.info.headshot,
                            suicide: k.isSuicide,
                        });
                    }
                    break;
                }
                default:
                    break;
            }
        }

        for (const n of teamNames) {
            const p = players[n];
            if (!p.death && options.eliminated && options.eliminated(n) && lastPos[n]) {
                // Player left/disconnected or the kill event is missing — mark the last known position
                p.death = { type: 'DEATH', name: 'Unknown', killer: null, weapon: 'Unknown', dist: 0, loc: lastPos[n], unknown: true, t: null };
            }
            if (p.death && !p.death.loc) p.death.loc = lastPos[n] || null;
            const timeline = [...p.knocks, ...p.kills, ...p.finishes, ...p.knockedBy, ...p.revives, ...p.revivedBy];
            if (p.death) timeline.push(p.death);
            p.timeline = timeline.sort((x, y) => (x.t === null) - (y.t === null) || x.t - y.t);
        }

        return { phases, planePath, players, startTime };
    }

    return { parse, makeNamer, prettifyId, normalizeKill, MAP_SIZES };
});
