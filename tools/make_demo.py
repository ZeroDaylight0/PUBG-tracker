"""Generate the sample matches used by the live demo (demo/*.json).

The data is synthetic but has the same shape as real PUBG API responses and
telemetry, so the demo runs through exactly the same code as real matches.

    python tools/make_demo.py
"""
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "demo"

MATCHES = [
    {
        "map": "Baltic_Main", "units": 816000, "mode": "squad-fpp", "seed": 7,
        "created": datetime(2026, 10, 31, 19, 42, tzinfo=timezone.utc), "rank": 3, "players": 96,
        "plane": ((60000, 120000), (760000, 700000)), "drop": (352000, 455000),  # Pochinki
        "final": (420000, 395000), "duration": 1680,
        "squad": ["DemoPlayer", "Kotik_Pro", "MedicMax", "Sn1per_Wolf"],
        # (t, attacker, victim, weapon, category, kind, dist_m, headshot)
        "fights": [
            (205, "DemoPlayer", "VolkovSV", "WeapHK416_C", "Damage_Gun", "knock", 34, True),
            (214, "Kotik_Pro", "VolkovSV", "WeapBerylM762_C", "Damage_Gun", "finish_for:DemoPlayer", 12, False),
            (240, "Sn1per_Wolf", "ProGamer_UA", "WeapKar98k_C", "Damage_Gun", "knock", 188, True),
            (262, "Sn1per_Wolf", "ProGamer_UA", "WeapKar98k_C", "Damage_Gun", "kill", 190, False),
            (610, "DemoPlayer", "lucky_noob", "ProjGrenade_C", "Damage_Explosion_Grenade", "kill", 22, False),
            (845, "Shadow_x", "MedicMax", "WeapMini14_C", "Damage_Gun", "knock", 96, False),
            (861, "Kotik_Pro", "MedicMax", None, None, "revive", 0, False),
            (870, "DemoPlayer", "Shadow_x", "WeapHK416_C", "Damage_Gun", "knock", 61, False),
            (884, "DemoPlayer", "Shadow_x", "WeapHK416_C", "Damage_Gun", "kill", 58, True),
            (1105, "MedicMax", "Rusher228", "WeapUMP_C", "Damage_Gun", "knock", 18, False),
            (1112, "MedicMax", "Rusher228", "WeapUMP_C", "Damage_Gun", "kill", 9, False),
            (1390, "TTV_Hunter", "Sn1per_Wolf", "WeapMk14_C", "Damage_Gun", "knock", 143, True),
            (1415, "TTV_Hunter", "Sn1per_Wolf", "WeapMk14_C", "Damage_Gun", "death", 140, False),
            (1452, "DemoPlayer", "TTV_Hunter", "WeapHK416_C", "Damage_Gun", "knock", 74, False),
            (1470, "Kotik_Pro", "TTV_Hunter", "WeapBerylM762_C", "Damage_Gun", "kill", 40, False),
            (1588, "Arctic_Fox", "Kotik_Pro", "WeapACE32_C", "Damage_Gun", "knock", 55, False),
            (1601, "Arctic_Fox", "Kotik_Pro", "WeapACE32_C", "Damage_Gun", "death", 50, True),
            (1622, "Arctic_Fox", "MedicMax", "WeapACE32_C", "Damage_Gun", "knock", 47, False),
            (1640, None, "MedicMax", "BattleRoyaleModeController_Def_C", "Damage_Groggy", "death", 0, False),
            (1665, "Arctic_Fox", "DemoPlayer", "WeapACE32_C", "Damage_Gun", "knock", 31, False),
            (1680, "Arctic_Fox", "DemoPlayer", "WeapACE32_C", "Damage_Gun", "death", 28, False),
        ],
    },
    {
        "map": "Desert_Main", "units": 816000, "mode": "squad-fpp", "seed": 11,
        "created": datetime(2026, 10, 30, 17, 5, tzinfo=timezone.utc), "rank": 1, "players": 94,
        "plane": ((780000, 90000), (90000, 760000)), "drop": (420000, 460000),  # Pecado
        "final": (470000, 520000), "duration": 1850,
        "squad": ["DemoPlayer", "Kotik_Pro", "MedicMax", "Sn1per_Wolf"],
        "fights": [
            (180, "DemoPlayer", "Bandito_7", "WeapSaiga12_C", "Damage_Gun", "knock", 6, False),
            (186, "DemoPlayer", "Bandito_7", "WeapSaiga12_C", "Damage_Gun", "kill", 5, False),
            (300, "Sn1per_Wolf", "Cactus_Jack", "WeapMk12_C", "Damage_Gun", "knock", 212, True),
            (322, "Sn1per_Wolf", "Cactus_Jack", "WeapMk12_C", "Damage_Gun", "kill", 215, False),
            (720, "Dune_Rider", "DemoPlayer", "Dacia_A_01_v2_C", "Damage_VehicleHit", "knock", 0, False),
            (735, "MedicMax", "DemoPlayer", None, None, "revive", 0, False),
            (742, "Kotik_Pro", "Dune_Rider", "WeapAK47_C", "Damage_Gun", "knock", 63, True),
            (755, "DemoPlayer", "Dune_Rider", "WeapAK47_C", "Damage_Gun", "finish_for:Kotik_Pro", 21, False),
            (1210, "DemoPlayer", "Mirage_Q", "WeapSCAR-L_C", "Damage_Gun", "knock", 88, True),
            (1224, "DemoPlayer", "Mirage_Q", "WeapSCAR-L_C", "Damage_Gun", "kill", 85, True),
            (1500, "Scorpion_Z", "Sn1per_Wolf", "WeapAWM_C", "Damage_Gun", "death", 410, True),
            (1580, "MedicMax", "Scorpion_Z", "ProjMolotov_C", "Damage_Molotov", "kill", 30, False),
            (1795, "DemoPlayer", "Last_Circle", "WeapSCAR-L_C", "Damage_Gun", "knock", 44, False),
            (1810, "Kotik_Pro", "Last_Circle", "WeapAK47_C", "Damage_Gun", "kill", 39, False),
        ],
    },
]


def iso(base, t):
    return (base + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def character(name, team, loc):
    return {"name": name, "teamId": team, "health": 100, "location": {"x": round(loc[0], 1), "y": round(loc[1], 1), "z": 1500.0},
            "ranking": 0, "accountId": f"account.demo.{name.lower()}", "isInBlueZone": False, "isInRedZone": False, "zone": []}


def build(cfg, idx):
    rnd = random.Random(cfg["seed"])
    base, units, dur = cfg["created"], cfg["units"], cfg["duration"]
    squad, fights = cfg["squad"], cfg["fights"]
    ev = [{"_T": "LogMatchStart", "_D": iso(base, 0), "mapName": cfg["map"], "teamSize": 4, "isCustomGame": False}]

    # Zones: white circles shrinking towards the final point
    zones, white = [], ((units / 2, units / 2), units * 0.32)
    fx, fy = cfg["final"]
    radii = [units * r for r in (0.32, 0.2, 0.12, 0.07, 0.04, 0.02, 0.01)]
    starts = [120, 420, 690, 930, 1150, 1360, 1560]
    for i, (r, t0) in enumerate(zip(radii, starts)):
        k = (i + 1) / len(radii)
        cx = units / 2 + (fx - units / 2) * k + rnd.uniform(-1, 1) * r * 0.3
        cy = units / 2 + (fy - units / 2) * k + rnd.uniform(-1, 1) * r * 0.3
        zones.append((t0, (cx, cy), r))
    for t in range(120, dur + 1, 10):
        cur = [z for z in zones if z[0] <= t][-1]
        i = zones.index(cur)
        prev_r = zones[i - 1][2] * 1.6 if i else units * 0.7
        shrink = min(1, max(0, (t - cur[0]) / 200))
        blue_r = prev_r + (cur[2] * 1.05 - prev_r) * shrink
        ev.append({"_T": "LogGameStatePeriodic", "_D": iso(base, t), "gameState": {
            "elapsedTime": t, "numAliveTeams": 25, "numAlivePlayers": max(4, 96 - t // 18),
            "safetyZonePosition": {"x": cur[1][0], "y": cur[1][1], "z": 0}, "safetyZoneRadius": cur[2],
            "poisonGasWarningPosition": {"x": cur[1][0], "y": cur[1][1], "z": 0}, "poisonGasWarningRadius": blue_r}})

    def white_at(t):
        return [z for z in zones if z[0] <= t][-1][1] if t >= zones[0][0] else (units / 2, units / 2)

    # Plane and jumps (squad + some other players along the line)
    (ax, ay), (bx, by) = cfg["plane"]
    dx, dy = cfg["drop"]
    # point on the plane line closest to the drop
    vx, vy = bx - ax, by - ay
    k = ((dx - ax) * vx + (dy - ay) * vy) / (vx * vx + vy * vy)
    for j, kk in enumerate([0.15, 0.3, 0.45, 0.6, 0.75, 0.9]):
        ev.append({"_T": "LogVehicleLeave", "_D": iso(base, 40 + j * 15), "character": character(f"Jumper{j}", 50 + j, (ax + vx * kk, ay + vy * kk)),
                   "vehicle": {"vehicleType": "TransportAircraft", "vehicleId": "DummyTransportAircraft_C"}, "rideDistance": 0, "seatIndex": 0})

    death_t = {}
    for (t, a, v, w, c, kind, d, hs) in fights:
        if kind == "death" and v in squad:
            death_t[v] = t
    if cfg["rank"] != 1:
        for n in squad:
            death_t.setdefault(n, dur)

    positions = {}
    for si, name in enumerate(squad):
        jump_t = 60 + int(k * 60)
        jump = (ax + vx * k + si * 900, ay + vy * k + si * 900)
        land = (dx + rnd.uniform(-2500, 2500) + si * 1800, dy + rnd.uniform(-2500, 2500) - si * 1200)
        ev.append({"_T": "LogVehicleLeave", "_D": iso(base, jump_t), "character": character(name, 1, jump),
                   "vehicle": {"vehicleType": "TransportAircraft", "vehicleId": "DummyTransportAircraft_C"}, "rideDistance": 0, "seatIndex": si})
        land_t = jump_t + 70
        ev.append({"_T": "LogParachuteLanding", "_D": iso(base, land_t), "character": character(name, 1, land), "distance": 0})
        x, y = land
        end = death_t.get(name, dur)
        pos = {}
        heading = rnd.uniform(0, math.tau)
        for t in range(land_t + 10, end + 1, 10):
            tx, ty = white_at(t)
            # loot around the drop for a while, then rotate to the zone (faster when far away)
            to_zone = math.atan2(ty - y, tx - x)
            far = math.hypot(tx - x, ty - y)
            heading = to_zone + rnd.uniform(-0.5, 0.5) if t > land_t + 240 else heading + rnd.uniform(-1.2, 1.2)
            speed = 0 if far < 3000 and t > land_t + 240 else (1600 if far > 90000 and t > land_t + 240 else 450)
            x += math.cos(heading) * speed * 10 + rnd.uniform(-300, 300)
            y += math.sin(heading) * speed * 10 + rnd.uniform(-300, 300)
            pos[t] = (x, y)
            ev.append({"_T": "LogPlayerPosition", "_D": iso(base, t), "character": character(name, 1, (x, y)),
                       "elapsedTime": t, "numAlivePlayers": 50, "common": {"isGame": 1.0}})
        positions[name] = pos

    def pos_of(name, t):
        pos = positions.get(name)
        if not pos:
            return None
        key = min(pos, key=lambda tt: abs(tt - t))
        return pos[key]

    enemy_pos, enemy_team, attack = {}, {}, 1000
    for (t, a, v, w, c, kind, d, hs) in fights:
        attack += 1
        squad_side = a if a in squad else v
        ref = pos_of(squad_side, t) or cfg["drop"]
        other = v if a in squad else a
        if other and other not in squad and other not in enemy_pos:
            ang = rnd.uniform(0, math.tau)
            enemy_pos[other] = (ref[0] + math.cos(ang) * d * 100, ref[1] + math.sin(ang) * d * 100)
            enemy_team[other] = 10 + len(enemy_team)

        def char(n):
            if n is None:
                return {"name": "", "teamId": 0, "location": {"x": 0, "y": 0, "z": 0}, "accountId": ""}
            if n in squad:
                return character(n, 1, pos_of(n, t) or ref)
            return character(n, enemy_team[n], enemy_pos[n])

        info = {"damageReason": "HeadShot" if hs else "TorsoShot", "damageTypeCategory": c, "damageCauserName": w,
                "additionalInfo": [], "distance": d * 100, "isThroughPenetrableWall": False} if w else None
        if kind == "knock":
            ev.append({"_T": "LogPlayerMakeGroggy", "_D": iso(base, t), "attackId": attack, "attacker": char(a), "victim": char(v),
                       "damageReason": info["damageReason"], "damageTypeCategory": c, "damageCauserName": w,
                       "damageCauserAdditionalInfo": [], "victimWeapon": "", "victimWeaponAdditionalInfo": [],
                       "distance": d * 100, "isAttackerInVehicle": c == "Damage_VehicleHit", "dBNOId": attack})
        elif kind == "revive":
            ev.append({"_T": "LogPlayerRevive", "_D": iso(base, t), "reviver": char(a), "victim": char(v), "dBNOId": attack})
        else:  # kill / death / finish_for:<credited player>
            killer = kind.split(":", 1)[1] if kind.startswith("finish_for:") else a
            # The credited player's damage info is the one from their knock, not the finishing shot
            knock = next((f for f in fights if f[5] == "knock" and f[2] == v and f[0] < t), None)
            credit_info = info
            if killer != a and knock and knock[1] == killer and knock[3]:
                credit_info = {"damageReason": "HeadShot" if knock[7] else "TorsoShot", "damageTypeCategory": knock[4],
                               "damageCauserName": knock[3], "additionalInfo": [], "distance": knock[6] * 100,
                               "isThroughPenetrableWall": False}
            ev.append({"_T": "LogPlayerKillV2", "_D": iso(base, t), "attackId": attack, "dBNOId": attack,
                       "victimGameResult": {"rank": 0}, "victim": char(v), "victimWeapon": "", "victimWeaponAdditionalInfo": [],
                       "dBNOMaker": char(killer) if killer else None, "dBNODamageInfo": credit_info,
                       "finisher": char(a) if a else None, "finishDamageInfo": info,
                       "killer": char(killer) if killer else char(None), "killerDamageInfo": credit_info,
                       "assists_AccountId": [], "teamKillers_AccountId": [], "isSuicide": False})

    ev.sort(key=lambda e: e["_D"])

    # Official-style match summary computed from the same script
    stats = {}
    for n in squad:
        kills = sum(1 for f in fights if f[5] in ("kill",) and f[1] == n) + \
            sum(1 for f in fights if f[5].startswith("finish_for:") and f[5].split(":")[1] == n)
        knocks = sum(1 for f in fights if f[5] == "knock" and f[1] == n)
        hs = sum(1 for f in fights if f[5] == "kill" and f[1] == n and f[7])
        longest = max([f[6] for f in fights if f[5] == "kill" and f[1] == n] + [0])
        revives = sum(1 for f in fights if f[5] == "revive" and f[1] == n)
        pos = positions[n]
        walk = sum(math.dist(pos[a], pos[b]) for a, b in zip(sorted(pos), sorted(pos)[1:])) / 100
        survived = death_t.get(n, dur)
        stats[n] = {"name": n, "playerId": f"account.demo.{n.lower()}", "kills": kills, "DBNOs": knocks,
                    "assists": rnd.randint(0, 2), "headshotKills": hs, "damageDealt": knocks * 100 + kills * 40 + rnd.uniform(20, 160),
                    "revives": revives, "longestKill": float(longest), "timeSurvived": survived, "winPlace": cfg["rank"],
                    "walkDistance": walk * 0.7, "rideDistance": walk * 0.3, "swimDistance": 0.0}
    match = {
        "data": {"type": "match", "id": f"demo-{idx}", "attributes": {
            "mapName": cfg["map"], "gameMode": cfg["mode"], "duration": dur,
            "createdAt": cfg["created"].strftime("%Y-%m-%dT%H:%M:%SZ")}},
        "included": [{"type": "participant", "id": f"p{i}", "attributes": {"stats": s}} for i, s in enumerate(stats.values())]
        + [{"type": "roster", "id": "r1", "relationships": {"participants": {"data": [{"id": f"p{i}"} for i in range(len(squad))]}}},
           {"type": "asset", "id": "a1", "attributes": {"URL": f"demo/telemetry-{idx}.json"}}],
    }
    return match, ev


def main():
    sys.path.insert(0, str(OUT.parent))
    import app  # summarize exactly like real matches

    OUT.mkdir(exist_ok=True)
    summaries = []
    for i, cfg in enumerate(MATCHES, start=1):
        match, events = build(cfg, i)
        (OUT / f"telemetry-{i}.json").write_text(json.dumps(events, separators=(",", ":")), encoding="utf-8")
        summary = app.summarize_match(match, f"account.demo.{cfg['squad'][0].lower()}", cfg["squad"][0])
        summary["players"] = cfg["players"]
        summaries.append(summary)
    (OUT / "matches.json").write_text(json.dumps(summaries, indent=1), encoding="utf-8")
    print(f"Wrote {len(summaries)} demo matches to {OUT}")


if __name__ == "__main__":
    main()
