"""Visualizes a map's .dat object/enemy placement data as Empty markers in the current scene -
doors, switches, props, item boxes, enemy spawns. Read-only, no round-trip: this is a context aid
for the texture-pack workflow, not an import of anything this addon can export back out.

Requires the map's REL to already be imported in the current scene (Import REL, n_rel.py) - each
placed marker is parented under that room's existing "chunk_root_<room>" empty rather than computing
a world position by hand, so Blender's own parenting math (rotate+translate) handles the transform
exactly like it already does for mesh objects. See local-wiki/pages/dat.html for the field layout
this is built on.

Reads its source .dat/.e.dat either straight out of an already-loaded Data.gsl (see
gsl_browser_menu.py - no disk extraction needed) or from a manually chosen file on disk, same
auto-detect-plus-manual-override pattern as gsl_browser_menu.py's own _guess_gsl_path. Objects are
listed per-type with a checkbox (not per-instance) so only the types of interest need be imported.
"""
import math, os
from typing import Any, cast, final
import bpy
from bpy.types import Context, Object, Operator, Panel, PropertyGroup, UIList
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty  # pyright: ignore[reportUnknownVariableType]
from . import dat, gsl, util
from .recent_rel_menu import load_recent_rels


_MARKER_DISPLAY_SIZE = 0.2


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class DatTypeEntry(PropertyGroup):
    type_id: IntProperty()
    type_name: StringProperty()
    is_enemy: BoolProperty(default=False)
    count: IntProperty()
    selected: BoolProperty(default=True)


class SceneWithDatObjectsSettings(bpy.types.Scene):
    pso_dat_path: str
    pso_dat_type_entries: "bpy.types.bpy_prop_collection[DatTypeEntry]"
    pso_dat_type_entries_index: int


def register_scene_properties():
    cast(Any, bpy.types.Scene).pso_dat_path = StringProperty(
        name="DAT",
        description="Path to a map's *o.dat (object placement) file - leave empty to auto-detect "
                     "from the last imported REL, either on disk or inside an already-loaded Data.gsl",
        subtype="FILE_PATH")
    cast(Any, bpy.types.Scene).pso_dat_type_entries = CollectionProperty(type=DatTypeEntry)
    cast(Any, bpy.types.Scene).pso_dat_type_entries_index = IntProperty()


def unregister_scene_properties():
    del cast(Any, bpy.types.Scene).pso_dat_path
    del cast(Any, bpy.types.Scene).pso_dat_type_entries
    del cast(Any, bpy.types.Scene).pso_dat_type_entries_index


def _object_dat_filename_for_rel(rel_path: str) -> str:
    """map_forest01_00n.rel -> map_forest01_00o.dat - same "keep the area segment, strip only the
    trailing r/n/c REL-variant letter" rule as rel_import_menu.py's _guess_tam_path, since a .dat is
    per-area just like a .tam, unlike the shared-across-areas .xvm. Doesn't attempt to pick a Solo/
    Challenge/Battle game-mode variant (_s/_c1/_d) - out of scope for now, this addon only edits
    rel/xvm."""
    filename_no_ext, _ext = os.path.splitext(os.path.basename(rel_path))
    if filename_no_ext and filename_no_ext[-1] in "rnc":
        filename_no_ext = filename_no_ext[:-1]
    return filename_no_ext + "o.dat"


def guess_dat_object_filename() -> str | None:
    """Pure lookup, no writes - same auto-detect-plus-manual-override pattern as gsl_browser_menu.py's
    _guess_gsl_path. Tries the most recently imported REL's own folder on disk first, then (if a
    Data.gsl is already loaded in the GSL browser panel) that archive's entry list. Returns None,
    leaving the field for the user to fill by hand, if neither has a match."""
    recents = load_recent_rels()
    if not recents:
        return None
    rel_path = recents[0].get("rel_path", "")
    if not rel_path:
        return None
    wanted = _object_dat_filename_for_rel(rel_path)

    disk_candidate = os.path.join(os.path.dirname(rel_path), wanted)
    if os.path.isfile(disk_candidate):
        return disk_candidate

    scene = bpy.context.scene
    if scene is not None:
        gsl_entries = getattr(scene, "pso_gsl_entries", None)
        if gsl_entries:
            for entry in gsl_entries:
                if cast(str, entry.filename).lower() == wanted.lower():
                    return wanted
    return None


def _find_gsl_entry(scene: Any, filename: str) -> gsl.GslTocEntry | None:
    gsl_entries = getattr(scene, "pso_gsl_entries", None)
    if not gsl_entries:
        return None
    for entry in gsl_entries:
        if cast(str, entry.filename).lower() == filename.lower():
            return gsl.GslTocEntry(
                filename=cast(str, entry.filename),
                byte_offset=cast(int, entry.byte_offset),
                length=cast(int, entry.length))
    return None


def _resolve_bytes(scene: Any, filename_or_path: str) -> bytes | None:
    """filename_or_path is either a real path on disk, or a bare filename to look up inside the
    already-loaded Data.gsl (scene.pso_gsl_entries) - resolved in memory via gsl.extract_entry, no
    disk write."""
    if os.path.isfile(filename_or_path):
        with open(filename_or_path, "rb") as f:
            return f.read()
    gsl_path = cast(str, getattr(scene, "pso_gsl_path", ""))
    toc_entry = _find_gsl_entry(scene, os.path.basename(filename_or_path))
    if toc_entry is not None and gsl_path and os.path.isfile(gsl_path):
        return gsl.extract_entry(gsl_path, toc_entry)
    return None


def _sibling_enemy_filename(object_dat_filename: str) -> str | None:
    noext, ext = os.path.splitext(object_dat_filename)
    if not noext.endswith("o"):
        return None
    return noext[:-1] + "e" + ext


def _resolve_dat_bytes(context: Context) -> "tuple[bytes, bytes | None] | None":
    """Returns (object_bytes, enemy_bytes_or_None), or None if the object .dat itself couldn't be
    resolved from either disk or the loaded GSL archive."""
    scene = context.scene
    if scene is None:
        return None
    path_or_name = cast(str, cast(Any, scene).pso_dat_path)
    object_bytes = _resolve_bytes(scene, path_or_name)
    if object_bytes is None:
        return None
    enemy_filename = _sibling_enemy_filename(path_or_name)
    enemy_bytes = _resolve_bytes(scene, enemy_filename) if enemy_filename else None
    return object_bytes, enemy_bytes


def _set_marker_transform(obj: Object, x: float, y: float, z: float, angle_x: int, angle_y: int, angle_z: int):
    world_scale = util.get_pso_world_scale()
    obj.rotation_mode = "XZY"
    obj.rotation_euler = (angle_x / 0x7fff * math.pi, angle_z / 0x7fff * -math.pi, angle_y / 0x7fff * math.pi)
    obj.location = (x / world_scale, -z / world_scale, y / world_scale)


def _make_marker(name: str, x: float, y: float, z: float, angle_x: int, angle_y: int, angle_z: int) -> Object:
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = "SPHERE"
    obj.empty_display_size = _MARKER_DISPLAY_SIZE
    _set_marker_transform(obj, x, y, z, angle_x, angle_y, angle_z)
    return obj


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class PsoLoadDatTypes(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Read this .dat (and its sibling *e.dat, if any) and list the object/enemy types found below"


    bl_idname = "pso_blender.load_dat_types"
    bl_label = "Load"

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        scene = cast(SceneWithDatObjectsSettings, context.scene)
        path = cast(str, scene.pso_dat_path)
        if not path:
            guessed = guess_dat_object_filename()
            if guessed:
                path = guessed
                scene.pso_dat_path = path

        if not path:
            self.report({"ERROR"}, "No .dat path set and nothing could be auto-detected.")
            return {"CANCELLED"}

        resolved = _resolve_dat_bytes(context)
        if resolved is None:
            if len(cast(Any, scene).pso_gsl_entries) == 0:
                self.report({"ERROR"}, "No .dat source found for '{}' - load Data.gsl in the GSL "
                                        "panel above (click Refresh) or point this field at a real "
                                        "file on disk.".format(path))
            else:
                self.report({"ERROR"}, "'{}' was not found on disk or in the loaded Data.gsl.".format(path))
            return {"CANCELLED"}
        object_bytes, enemy_bytes = resolved

        counts: dict[tuple[bool, int], int] = {}
        for entry in dat.parse_objects(object_bytes):
            key = (False, entry.base_type)
            counts[key] = counts.get(key, 0) + 1
        if enemy_bytes is not None:
            for entry in dat.parse_enemies(enemy_bytes):
                key = (True, entry.base_type)
                counts[key] = counts.get(key, 0) + 1

        collection = cast(Any, scene).pso_dat_type_entries
        collection.clear()
        for (is_enemy, type_id), count in sorted(counts.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            row = cast(DatTypeEntry, collection.add())
            row.type_id = type_id
            # Empty when the type isn't in KNOWN_OBJECT_TYPE_NAMES - draw_item below always shows the
            # hex ID regardless, and appends this name alongside it when known, so both are visible
            # at once instead of one replacing the other.
            row.type_name = dat.KNOWN_OBJECT_TYPE_NAMES.get(type_id, "")
            row.is_enemy = is_enemy
            row.count = count
            # Preserves this feature's original default behavior: objects visualized by default,
            # enemies opt-in (there can be many more of them, and they're less relevant to a
            # texture-pack workflow's "where are the doors" use case).
            row.selected = not is_enemy
        scene.pso_dat_type_entries_index = 0

        self.report({"INFO"}, "Found {} object type(s){} in '{}'.".format(
            len([k for k in counts if not k[0]]),
            " and {} enemy type(s)".format(len([k for k in counts if k[0]])) if enemy_bytes is not None else "",
            os.path.basename(path)))
        return {"FINISHED"}


@final
class PsoImportDatMarkers(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Place an Empty marker for every checked type above, parented under this map's already-imported chunk_root_<room> objects"


    bl_idname = "pso_blender.import_dat_markers"
    bl_label = "Import Selected"
    bl_options = {"REGISTER"}

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        scene = cast(SceneWithDatObjectsSettings, context.scene)
        type_entries = cast(Any, scene).pso_dat_type_entries
        if len(type_entries) == 0:
            self.report({"ERROR"}, "Nothing loaded yet - click Load first.")
            return {"CANCELLED"}
        selected_ids = {(cast(bool, e.is_enemy), cast(int, e.type_id)) for e in type_entries if cast(bool, e.selected)}
        if not selected_ids:
            self.report({"WARNING"}, "No types checked - nothing to import.")
            return {"CANCELLED"}

        resolved = _resolve_dat_bytes(context)
        if resolved is None:
            self.report({"ERROR"}, "Could not re-read the .dat source - try Load again.")
            return {"CANCELLED"}
        object_bytes, enemy_bytes = resolved

        area_name = os.path.splitext(os.path.basename(cast(str, scene.pso_dat_path)))[0]
        coll = bpy.data.collections.new(area_name + "_dat_objects")
        if bpy.context.scene is not None:
            bpy.context.scene.collection.children.link(coll)

        placed = 0
        missing_rooms: set[int] = set()

        for i, entry in enumerate(dat.parse_objects(object_bytes)):
            if (False, entry.base_type) not in selected_ids:
                continue
            room_obj = bpy.data.objects.get("chunk_root_" + str(entry.room))
            if room_obj is None:
                missing_rooms.add(entry.room)
                continue
            type_name = dat.KNOWN_OBJECT_TYPE_NAMES.get(entry.base_type, hex(entry.base_type))
            marker = _make_marker(
                "{}_obj_{}_{}".format(area_name, i, type_name),
                entry.x, entry.y, entry.z, entry.angle_x, entry.angle_y, entry.angle_z)
            marker.parent = room_obj
            marker["pso_dat_base_type"] = entry.base_type
            marker["pso_dat_room"] = entry.room
            marker["pso_dat_is_enemy"] = False
            if entry.base_type in dat.DOOR_SWITCH_TYPE_IDS:
                marker["pso_dat_switch_flag"] = entry.param4
            coll.objects.link(marker)
            placed += 1

        enemies_placed = 0
        if enemy_bytes is not None:
            for i, entry in enumerate(dat.parse_enemies(enemy_bytes)):
                if (True, entry.base_type) not in selected_ids:
                    continue
                room_obj = bpy.data.objects.get("chunk_root_" + str(entry.room))
                if room_obj is None:
                    missing_rooms.add(entry.room)
                    continue
                type_name = dat.KNOWN_OBJECT_TYPE_NAMES.get(entry.base_type, hex(entry.base_type))
                marker = _make_marker(
                    "{}_enemy_{}_{}".format(area_name, i, type_name),
                    entry.x, entry.y, entry.z, entry.angle_x, entry.angle_y, entry.angle_z)
                marker.parent = room_obj
                marker["pso_dat_base_type"] = entry.base_type
                marker["pso_dat_room"] = entry.room
                marker["pso_dat_is_enemy"] = True
                coll.objects.link(marker)
                enemies_placed += 1

        if missing_rooms:
            self.report({"WARNING"}, "Skipped placement for room(s) {} - no matching chunk_root found "
                                      "in the current scene. Import this map's REL first.".format(sorted(missing_rooms)))
        self.report({"INFO"}, "Placed {} object marker(s) and {} enemy marker(s).".format(placed, enemies_placed))
        return {"FINISHED"}


_NO_DESCRIPTION_TEXT = "No description recorded yet for this type - see local-wiki/pages/dat.html."


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class PsoDatTypeInfo(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Does nothing on its own - exists purely to host a hover tooltip (Blender's `description()`
    classmethod override, the standard way to get a per-instance dynamic tooltip - a plain
    layout.label() can't carry one) showing the same friendly, one-line description this addon's
    local-wiki carries for each object type, right in the DAT Objects list instead of needing to
    tab over to the wiki."""


    bl_idname = "pso_blender.dat_type_info"
    bl_label = ""
    bl_options = {"INTERNAL"}
    bl_description = _NO_DESCRIPTION_TEXT

    type_id: IntProperty(options={"HIDDEN", "SKIP_SAVE"})  # pyright: ignore[reportUnknownVariableType]

    @classmethod
    def description(cls, context: Context, properties: Any) -> str:
        return dat.OBJECT_TYPE_DESCRIPTIONS.get(cast(int, properties.type_id), _NO_DESCRIPTION_TEXT)

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        return {"FINISHED"}


@final
class PSO_UL_dat_type_entries(UIList):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_UL_dat_type_entries"

    def draw_item(self, context: Context, layout: bpy.types.UILayout, data: Any, item: Any,  # pyright: ignore[reportIncompatibleMethodOverride]
            icon: int, active_data: Any, active_propname: str, index: int = 0, flt_flag: int = 0):
        entry = cast(DatTypeEntry, item)
        type_id = cast(int, entry.type_id)
        type_name = cast(str, entry.type_name)
        # Always shows the raw hex type ID (handy to cross-reference against local-wiki's dat.html
        # table even for a named type), with the friendly name appended when one is known.
        label = "0x{:04X}  {}".format(type_id, type_name) if type_name else "0x{:04X}".format(type_id)
        row = layout.row(align=True)
        row.prop(entry, "selected", text="")
        info_op = row.operator(PsoDatTypeInfo.bl_idname, text="", icon="QUESTION", emboss=False)
        cast(Any, info_op).type_id = type_id
        row.label(text=label)
        tag_col = row.row(align=True)
        tag_col.ui_units_x = 2.5
        tag_col.alignment = "RIGHT"
        tag_col.label(text="enemy" if cast(bool, entry.is_enemy) else "obj")
        count_col = row.row(align=True)
        count_col.ui_units_x = 1.8
        count_col.alignment = "RIGHT"
        count_col.label(text=str(cast(int, entry.count)))


@final
class PSO_PT_dat_objects(Panel):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_PT_dat_objects"
    bl_label = "DAT Objects"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "pso-blender"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout or context.scene is None:
            return
        scene = cast(SceneWithDatObjectsSettings, context.scene)

        if len(cast(Any, scene).pso_gsl_entries) == 0 and not cast(str, scene.pso_dat_path):
            layout.label(text="Load Data.gsl above first (Refresh),", icon="ERROR")
            layout.label(text="or set a DAT path manually below.")

        col = layout.column(align=True)
        col.prop(scene, "pso_dat_path", text="")
        if not cast(str, scene.pso_dat_path):
            guessed = guess_dat_object_filename()
            if guessed:
                col.label(text="Detected: " + guessed, icon="INFO")
        col.operator(PsoLoadDatTypes.bl_idname)

        if len(cast(Any, scene).pso_dat_type_entries) == 0:
            return

        layout.template_list(
            PSO_UL_dat_type_entries.bl_idname, "", scene, "pso_dat_type_entries", scene, "pso_dat_type_entries_index", rows=8)
        layout.operator(PsoImportDatMarkers.bl_idname, icon="EMPTY_DATA")
