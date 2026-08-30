"""Per-map, per-area object/enemy placement data (e.g. map_forest01_00o.dat / _00e.dat), found
inside Data.gsl or as loose files next to a map's .rel/.xvm. Not a relocatable/pointer format -
just a flat array of fixed-size records, so this doesn't use the Serializable/Rel machinery those
modules rely on (same reasoning as gsl.py).

Field layout confirmed against fuzziqersoftware/newserv (MIT licensed) - src/Map.hh's
ObjectSetEntry/EnemySetEntry structs - and independently re-verified byte-for-byte against real
map_forest01 data pulled from Data.gsl. See local-wiki/pages/dat.html for the full writeup and
local-wiki/pages/credits.html for the newserv attribution.

Position is room-relative, not world space: to place an object in a Blender scene, parent it under
that room's existing "chunk_root_<room>" empty (created by n_rel.py on REL import) and let Blender's
own parenting math do the rotate+translate, exactly like n_rel.py already does for mesh objects.
"""
import struct
from dataclasses import dataclass


@dataclass
class ObjectSetEntry:
    base_type: int = 0
    floor: int = 0
    room: int = 0
    group: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    angle_x: int = 0
    angle_y: int = 0
    angle_z: int = 0
    param1: float = 0.0
    param2: float = 0.0
    param3: float = 0.0
    param4: int = 0
    param5: int = 0
    param6: int = 0


@dataclass
class EnemySetEntry:
    base_type: int = 0
    floor: int = 0
    room: int = 0
    wave_number: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    angle_x: int = 0
    angle_y: int = 0
    angle_z: int = 0
    param1: float = 0.0
    param2: float = 0.0
    param3: float = 0.0
    param4: float = 0.0
    param5: float = 0.0
    param6: int = 0
    param7: int = 0


_OBJECT_ENTRY_SIZE = 0x44
_ENEMY_ENTRY_SIZE = 0x48


def parse_objects(data: bytes) -> list[ObjectSetEntry]:
    entries: list[ObjectSetEntry] = []
    for offset in range(0, len(data) - len(data) % _OBJECT_ENTRY_SIZE, _OBJECT_ENTRY_SIZE):
        base_type, _set_flags, _index, floor, _entity_id, group, room, _unk = struct.unpack_from("<8H", data, offset)
        x, y, z = struct.unpack_from("<3f", data, offset + 0x10)
        angle_x, angle_y, angle_z = struct.unpack_from("<3H", data, offset + 0x1c)
        param1, param2, param3 = struct.unpack_from("<3f", data, offset + 0x28)
        param4, param5, param6 = struct.unpack_from("<3i", data, offset + 0x34)
        entries.append(ObjectSetEntry(
            base_type=base_type, floor=floor, room=room, group=group,
            x=x, y=y, z=z, angle_x=angle_x, angle_y=angle_y, angle_z=angle_z,
            param1=param1, param2=param2, param3=param3, param4=param4, param5=param5, param6=param6))
    return entries


def parse_enemies(data: bytes) -> list[EnemySetEntry]:
    entries: list[EnemySetEntry] = []
    for offset in range(0, len(data) - len(data) % _ENEMY_ENTRY_SIZE, _ENEMY_ENTRY_SIZE):
        base_type, _set_flags, _index, _num_children, floor, _entity_id, room, wave_number, _wave_number2, _unk = struct.unpack_from("<10H", data, offset)
        x, y, z = struct.unpack_from("<3f", data, offset + 0x14)
        angle_x, angle_y, angle_z = struct.unpack_from("<3H", data, offset + 0x24)
        param1, param2, param3, param4, param5 = struct.unpack_from("<5f", data, offset + 0x2c)
        param6, param7 = struct.unpack_from("<2h", data, offset + 0x40)
        entries.append(EnemySetEntry(
            base_type=base_type, floor=floor, room=room, wave_number=wave_number,
            x=x, y=y, z=z, angle_x=angle_x, angle_y=angle_y, angle_z=angle_z,
            param1=param1, param2=param2, param3=param3, param4=param4, param5=param5, param6=param6, param7=param7))
    return entries


def read_objects(path: str) -> list[ObjectSetEntry]:
    with open(path, "rb") as f:
        return parse_objects(f.read())


def read_enemies(path: str) -> list[EnemySetEntry]:
    with open(path, "rb") as f:
        return parse_enemies(f.read())


# Door/switch object type IDs - these use param4 as a switch-flag number (see local-wiki's evt.html
# for the set_switch_flag/clear_switch_flag mechanism that reads it).
DOOR_SWITCH_TYPE_IDS: frozenset[int] = frozenset({
    0x0046, 0x0047, 0x0048, 0x0049, 0x0054, 0x0056, 0x0080, 0x0081, 0x0084,
    0x0090, 0x008E, 0x00C1, 0x00C2, 0x0100, 0x0102, 0x0130, 0x0144, 0x0145,
    0x0146, 0x0147, 0x0148, 0x0149, 0x014A, 0x014B, 0x01A0, 0x01AB, 0x01C0,
    0x0202, 0x0204, 0x0221, 0x0222,
})

# Object type names - the subset of newserv's much larger, area/version-conditional object type
# table (src/Map.cc's dat_object_definitions) that's actually verified and useful for this addon's
# purposes so far. A type ID not listed here isn't unknown, just not worth a name yet - see
# local-wiki/pages/dat.html for the full confirmed table and its area/version caveats (the same
# numeric ID can mean a different object in a different area/game version).
# Source: fuzziqersoftware/newserv (MIT) - Copyright (c) 2024 Martin Michelsen. Attribution kept per
# local-wiki/pages/credits.html.
KNOWN_OBJECT_TYPE_NAMES: dict[int, str] = {
    0x0000: "TObjPlayerSet",
    0x0001: "TObjParticle",
    0x0002: "TObjAreaWarpForest",
    0x0003: "TObjMapWarpForest",
    0x0008: "TObjEvtCollision",
    0x000E: "TObjRoomId",
    0x0046: "TObjCityDoor_Shop",
    0x0047: "TObjCityDoor_Guild",
    0x0048: "TObjCityDoor_Warp",
    0x0049: "TObjCityDoor_Med",
    0x0054: "TObjCityDoor_Lobby",
    0x0056: "TODoorLabo",
    0x0080: "TObjDoor",
    0x0081: "TObjDoorKey",
    0x0082: "TObjLazerFenceNorm",
    0x0084: "TLazerFenceSw",
    0x0087: "TMotorcycle",
    0x0088: "TObjContainerBase2",
    0x008D: "TOCapsuleAncient01",
    0x008E: "TOBarrierEnergy01",
    0x0090: "TOKeyGenericSw",
    0x0092: "TObjContainerBase",
    0x00C1: "TODoorCave01",
    0x00C2: "TODoorCave02",
    0x0100: "TODoorMachine01",
    0x0102: "TODoorMachine02",
    0x0130: "TODoorVoShip",
    0x0144: "TODoorAncient01",
    0x0145: "TODoorAncient03",
    0x0146: "TODoorAncient04",
    0x0147: "TODoorAncient05",
    0x0148: "TODoorAncient06",
    0x0149: "TODoorAncient07",
    0x014A: "TODoorAncient08",
    0x014B: "TODoorAncient09",
    0x01A0: "TODoorVS2Door01",
    0x01AB: "TODoorFourLightRuins",
    0x01C0: "TODoorFourLightSpace",
    0x0202: "TObjDoorJung",
    0x0204: "TODoorJungleMain",
    0x0221: "TODoorFourLightSeabed",
    0x0222: "TODoorFourLightSeabedU",
}

# One-line, user-facing descriptions for the same type IDs - condensed from newserv's own comments
# (same source/attribution as KNOWN_OBJECT_TYPE_NAMES above) and from local-wiki/pages/dat.html.
# A type ID not listed here just doesn't have a description yet, not necessarily unknown.
OBJECT_TYPE_DESCRIPTIONS: dict[int, str] = {
    0x0000: "Defines where a player starts when entering a floor. param1 = client ID, param4 = source type.",
    0x0001: "Displays a particle effect. Not constructed in split-screen mode.",
    0x0002: "Triangular cross-floor warp. param4 = destination floor.",
    0x0003: "Triangular intra-floor warp. param1-3 = destination coords, param4 = destination angle.",
    0x0008: "Event collision - triggers a wave event when a player is nearby. param1 = radius, param4 = event ID.",
    0x000E: "Sets a player's room ID when nearby (split-screen/room-scoping logic).",
    0x0046: "City door to the shop area. No parameters.",
    0x0047: "City door to the Hunter's Guild. No parameters.",
    0x0048: "City door to the Ragol warp. No parameters.",
    0x0049: "City door to the Medical Center. No parameters.",
    0x0054: "Door that blocks the lobby teleporter in offline mode.",
    0x0056: "Episode 2 Lab door. param4 = switch flag number + activation mode.",
    0x0080: "Forest door. param4 = switch flag (low byte) + door number. param6 = 1 enables the unlock cutscene.",
    0x0081: "Key-locked door/switch (Forest/Cave/VR). param4 = switch flag number.",
    0x0082: "Laser fence. param4 = switch flag, param6 = model (short/long).",
    0x0084: "Laser fence switch. param4 = switch flag, param6 = color.",
    0x0087: "Small vehicle prop. param1 = model number (crashed/intact).",
    0x0088: "Item/drop box.",
    0x0090: "Generic switch for non-door triggers (lights, poison rooms, bridges). param4 = switch flag.",
    0x008D: "Capsule/container. param6 = quest label called when activated.",
    0x008E: "Energy barrier. param4 = switch flag number + activation mode.",
    0x0092: "Large box (specialized drops). Same parameters as TObjContainerBase2 (0x0088).",
    0x00C1: "Caves door (multi-switch). param4 = switch flag number.",
    0x00C2: "Caves door (standard). param4 = switch flag number.",
    0x0100: "Mines door. param4 = switch flag number.",
    0x0102: "Mines door - also reused for the Episode 4 desert door. param4 = switch flag number.",
    0x0130: "Post-Vol Opt ruins door. Reads quest flags directly, not switch flags.",
    0x0144: "Ruins door (usually Ruins 1). Same params as TODoorCave02 (0x00C2).",
    0x0145: "Ruins door (usually Ruins 3). Same params as TODoorCave02 (0x00C2).",
    0x0146: "Ruins door (usually Ruins 2). Same params as TODoorCave02 (0x00C2).",
    0x0147: "Ruins door (usually Ruins 1). Same params as TODoorCave02 (0x00C2).",
    0x0148: "Ruins door (usually Ruins 2). Same params as TODoorCave02 (0x00C2).",
    0x0149: "Ruins door (usually Ruins 3). Same params as TODoorCave02 (0x00C2).",
    0x014A: "Ruins door. param4 = switch flag number + activation mode.",
    0x014B: "Ruins door. param4 = switch flag number + activation mode.",
    0x01A0: "Temple/Palace door. param4 = switch flag number.",
    0x01AB: "Temple door (Four-Light Ruins). param4 = switch flag number.",
    0x01C0: "Spaceship door (Four-Light Space). param4 = switch flag number.",
    0x0202: "CCA door. param4 = switch flag number.",
    0x0204: "CCA main door. A matching switch sets one of 3 quest flags it checks directly.",
    0x0221: "Seabed multiplayer door. param4 = switch flag number.",
    0x0222: "Seabed multiplayer door (variant). param4 = switch flag number.",
}
