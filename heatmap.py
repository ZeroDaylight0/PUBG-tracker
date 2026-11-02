"""Heatmap points for one player from PUBG telemetry.

Mirrors extractPoints() in heatmap.js (the browser builds the same layers for
the current match; the server builds them for every stored match on a map).
Coordinates stay in telemetry units (centimeters).
"""

LAYERS = ("landing", "kills", "knocks", "deaths", "knocked", "movement")
KILL_EVENTS = ("LogPlayerKillV2", "LogPlayerKill")


def _is_player(character, player):
    if not isinstance(character, dict):
        return False
    account = player.get("account_id")
    if account and character.get("accountId"):
        return character["accountId"] == account
    return bool(player.get("name")) and character.get("name") == player["name"]


def _key(character):
    return character.get("accountId") or character.get("name")


def _point(loc):
    if not isinstance(loc, dict):
        return None
    x, y = loc.get("x"), loc.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or (x == 0 and y == 0):
        return None
    return [int(round(x)), int(round(y))]


def extract_points(events, player):
    """player: {"account_id": ..., "name": ...}. Returns {layer: [[x, y], ...]}."""
    out = {layer: [] for layer in LAYERS}
    landed = False
    last_pos = {}

    def add(layer, loc):
        p = _point(loc)
        if p:
            out[layer].append(p)

    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("_T")
        if kind == "LogPlayerPosition":
            c = ev.get("character") or {}
            if _key(c):
                last_pos[_key(c)] = c.get("location")
            if landed and _is_player(c, player):
                add("movement", c.get("location"))
        elif kind == "LogParachuteLanding":
            c = ev.get("character") or {}
            if not landed and _is_player(c, player):
                landed = True
                add("landing", c.get("location"))
        elif kind == "LogPlayerMakeGroggy":
            attacker, victim = ev.get("attacker") or {}, ev.get("victim") or {}
            if _is_player(attacker, player) and attacker.get("name") and not _is_player(victim, player):
                add("knocks", victim.get("location"))
            if _is_player(victim, player):
                add("knocked", victim.get("location"))
        elif kind in KILL_EVENTS:
            killer, victim = ev.get("killer") or {}, ev.get("victim") or {}
            loc = victim.get("location")
            if _point(loc) is None:
                loc = last_pos.get(_key(victim))
            if killer.get("name") and _is_player(killer, player) and not _is_player(victim, player):
                add("kills", loc)
            if _is_player(victim, player):
                add("deaths", loc)
    return out


def merge(target, points):
    for layer in LAYERS:
        target.setdefault(layer, []).extend(points.get(layer, []))
    return target
