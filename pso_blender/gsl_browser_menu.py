"""N-panel browser for Data.gsl (or any other .gsl archive) - lists the archive's contents (name,
type, size), filterable by a search field and a type-scope dropdown, lets the user check several
entries and extract them to a chosen folder. Read-only: never writes back into the archive.
Modeled after relief_displace_menu.py's scene-property registration pair and recent_rel_menu.py's
self-contained N-panel section.
"""
import os
from typing import Any, cast, final
import bpy
from bpy.types import Context, Operator, Panel, PropertyGroup, UIList
from bpy.props import (  # pyright: ignore[reportUnknownVariableType]
    BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty)
from . import gsl
from .recent_rel_menu import load_recent_rels


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class GslBrowserEntry(PropertyGroup):
    filename: StringProperty()
    # Extension only, uppercased, no leading dot (e.g. "BML") - computed once when the archive is
    # loaded (PsoLoadGslArchive), not re-derived from filename on every redraw.
    file_type: StringProperty()
    byte_offset: IntProperty()
    length: IntProperty()
    selected: BoolProperty(default=False)


# Blender's dynamic-EnumProperty-items gotcha: the (identifier, name, description) tuples handed
# back from an `items` callback must stay referenced somewhere else, or the underlying C string
# pointers can go stale and crash Blender once the temporary Python list they lived in is garbage
# collected. Stashing the latest result here (rather than just returning a fresh list each call)
# is the fix the API docs themselves recommend.
_type_filter_items_cache: list[tuple[str, str, str]] = [("ALL", "All", "")]


def _gsl_type_filter_items(_self: Any, context: Context | None) -> list[tuple[str, str, str]]:
    global _type_filter_items_cache
    scene = context.scene if context is not None else None
    entries = getattr(scene, "pso_gsl_entries", None) if scene is not None else None
    types = sorted({cast(str, e.file_type) for e in entries if cast(str, e.file_type)}) if entries else []
    _type_filter_items_cache = [("ALL", "All", "Show every entry")] + [(t, t, "") for t in types]
    return _type_filter_items_cache


class SceneWithGslBrowserSettings(bpy.types.Scene):
    pso_gsl_path: str
    pso_gsl_entries: "bpy.types.bpy_prop_collection[GslBrowserEntry]"
    pso_gsl_entries_index: int
    pso_gsl_extract_dir: str
    pso_gsl_type_filter: str
    pso_gsl_search: str


def register_scene_properties():
    cast(Any, bpy.types.Scene).pso_gsl_path = StringProperty(
        name="Data.gsl",
        description="Path to a .gsl archive (e.g. the game's data/Data.gsl) to browse",
        subtype="FILE_PATH")
    cast(Any, bpy.types.Scene).pso_gsl_entries = CollectionProperty(type=GslBrowserEntry)
    cast(Any, bpy.types.Scene).pso_gsl_entries_index = IntProperty()
    cast(Any, bpy.types.Scene).pso_gsl_extract_dir = StringProperty(
        name="Extract To",
        description="Folder checked entries are extracted into",
        subtype="DIR_PATH")
    cast(Any, bpy.types.Scene).pso_gsl_type_filter = EnumProperty(
        name="Type",
        description="Only show entries of this file type in the list below",
        items=_gsl_type_filter_items)
    cast(Any, bpy.types.Scene).pso_gsl_search = StringProperty(
        name="Search",
        description="Only show entries whose filename contains this text")


def unregister_scene_properties():
    del cast(Any, bpy.types.Scene).pso_gsl_path
    del cast(Any, bpy.types.Scene).pso_gsl_entries
    del cast(Any, bpy.types.Scene).pso_gsl_entries_index
    del cast(Any, bpy.types.Scene).pso_gsl_extract_dir
    del cast(Any, bpy.types.Scene).pso_gsl_type_filter
    del cast(Any, bpy.types.Scene).pso_gsl_search


def _guess_gsl_path() -> str | None:
    """Data.gsl lives in the game's data/ folder, the direct parent of data/scene/ where every
    .rel this addon imports lives - so the most recently imported .rel's own path (already
    tracked by recent_rel_menu.py) is enough to guess where Data.gsl probably is, without the user
    ever having pointed at one before. Matches this addon's established auto-detect-plus-manual-
    override pattern (see e.g. rel_import_menu.py's .xvm/.tam guessing) - only ever a pre-filled
    suggestion, never forced; returns None (leaving the field for the user to fill by hand) if
    nothing is found."""
    recents = load_recent_rels()
    if not recents:
        return None
    rel_path = recents[0].get("rel_path", "")
    if not rel_path:
        return None
    data_dir = os.path.dirname(os.path.dirname(rel_path))
    for candidate_name in ("Data.gsl", "data.gsl"):
        candidate = os.path.join(data_dir, candidate_name)
        if os.path.isfile(candidate):
            return candidate
    return None


# Fixed widths (in Blender "UI units") for the Type/Size columns, so every row lines up instead
# of reflowing per-entry. Every real extension seen on Data.gsl is exactly 3 letters (BML, XVM,
# REL, ...) - Type is sized to comfortably fit that. The largest real entry seen so far is ~990KB
# ("990.0 KB") - Size is sized to comfortably fit that without truncating on a bigger file later.
_TYPE_COLUMN_WIDTH = 1.6
_SIZE_COLUMN_WIDTH = 4.5


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return "{:.0f} {}".format(size, unit) if unit == "B" else "{:.1f} {}".format(size, unit)
        size /= 1024.0
    return "{:.1f} GB".format(size)


@final
class PsoLoadGslArchive(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Read this archive's file listing (names/sizes only - nothing is extracted yet) into the list below"


    bl_idname = "pso_blender.load_gsl_archive"
    bl_label = "Load Archive"

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        scene = cast(SceneWithGslBrowserSettings, context.scene)
        path = cast(str, scene.pso_gsl_path)
        if not path:
            # Falls back to the same auto-detected guess the panel shows as a hint (see
            # _guess_gsl_path) - writing it into the property here, inside an operator, is safe;
            # doing the equivalent write from Panel.draw() raises "Writing to ID classes in this
            # context is not allowed" (confirmed live).
            guessed = _guess_gsl_path()
            if guessed:
                path = guessed
                scene.pso_gsl_path = path
        if not path or not os.path.isfile(path):
            self.report({"ERROR"}, "Data.gsl path does not point to an existing file.")
            return {"CANCELLED"}
        try:
            entries = gsl.list_entries(path)
        except Exception as ex:
            self.report({"ERROR"}, "Failed to read '{}': {}".format(path, ex))
            return {"CANCELLED"}

        collection = cast(Any, scene).pso_gsl_entries
        collection.clear()
        for entry in entries:
            row = cast(GslBrowserEntry, collection.add())
            row.filename = entry.filename
            row.file_type = os.path.splitext(entry.filename)[1].lstrip(".").upper()
            row.byte_offset = entry.byte_offset
            row.length = entry.length
        scene.pso_gsl_entries_index = 0
        self.report({"INFO"}, "Loaded {} entries from '{}'.".format(len(entries), os.path.basename(path)))
        return {"FINISHED"}


@final
class PsoExtractGslEntries(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Extract every checked entry below into the output folder"


    bl_idname = "pso_blender.extract_gsl_entries"
    bl_label = "Extract Selected"

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        scene = cast(SceneWithGslBrowserSettings, context.scene)
        gsl_path = cast(str, scene.pso_gsl_path)
        out_dir = cast(str, scene.pso_gsl_extract_dir)
        if not gsl_path or not os.path.isfile(gsl_path):
            self.report({"ERROR"}, "Data.gsl path does not point to an existing file.")
            return {"CANCELLED"}
        if not out_dir:
            self.report({"ERROR"}, "Choose an output folder first.")
            return {"CANCELLED"}

        # Selection is read straight off each entry's own `selected` flag, independent of
        # whatever the type filter/search box currently hides - a checked entry stays checked
        # (and gets extracted) even if a filter change scrolls it out of view first.
        selected = [cast(GslBrowserEntry, e) for e in cast(Any, scene).pso_gsl_entries if cast(bool, e.selected)]
        if not selected:
            self.report({"WARNING"}, "No entries checked - nothing to extract.")
            return {"CANCELLED"}

        os.makedirs(out_dir, exist_ok=True)
        written = 0
        for entry in selected:
            toc_entry = gsl.GslTocEntry(
                filename=cast(str, entry.filename),
                byte_offset=cast(int, entry.byte_offset),
                length=cast(int, entry.length))
            try:
                data = gsl.extract_entry(gsl_path, toc_entry)
            except Exception as ex:
                self.report({"WARNING"}, "Failed to extract '{}': {}".format(entry.filename, ex))
                continue
            out_path = os.path.join(out_dir, cast(str, entry.filename))
            with open(out_path, "wb") as f:
                f.write(data)
            written += 1
        self.report({"INFO"}, "Extracted {} file(s) to '{}'.".format(written, out_dir))
        return {"FINISHED"}


def _filter_and_sort_gsl_entries(items: Any, filter_name: str, bitflag_filter_item: int, type_filter: str) -> "tuple[list[int], list[int]]":
    """The actual filter/sort logic behind PSO_UL_gsl_entries.filter_items below, pulled out as a
    plain function (no `self`/live UIList instance needed) so it can be exercised directly by a
    headless test - Blender only lets the runtime instantiate a real UIList, not user code, so
    testing filter_items() itself isn't possible outside of an actual UI redraw. Always sorts
    ascending by filename - no user-facing sort direction toggle (removed as redundant once the
    dedicated search field existed alongside it)."""
    helper_funcs = bpy.types.UI_UL_list
    # Text search box (built into UIList) and the type-scope dropdown both narrow visibility - AND
    # them together rather than picking one, so a user can combine "just .BML" with typing part of
    # a name. filter_items_by_name returns [] (not one flag per item) for an empty pattern - start
    # from "everything visible" ourselves in that case instead.
    if filter_name:
        flt_flags = list(helper_funcs.filter_items_by_name(filter_name, bitflag_filter_item, items, "filename"))
    else:
        flt_flags = [bitflag_filter_item] * len(items)
    if type_filter != "ALL":
        for i, entry in enumerate(items):
            if cast(str, entry.file_type) != type_filter:
                flt_flags[i] = 0

    flt_neworder = list(helper_funcs.sort_items_by_name(items, "filename"))

    return flt_flags, flt_neworder


@final
class PSO_UL_gsl_entries(UIList):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_UL_gsl_entries"

    def draw_item(self, context: Context, layout: bpy.types.UILayout, data: Any, item: Any,  # pyright: ignore[reportIncompatibleMethodOverride]
            icon: int, active_data: Any, active_propname: str, index: int = 0, flt_flag: int = 0):
        entry = cast(GslBrowserEntry, item)
        row = layout.row(align=True)
        row.prop(entry, "selected", text="")
        row.label(text=cast(str, entry.filename))

        type_col = row.row(align=True)
        type_col.ui_units_x = _TYPE_COLUMN_WIDTH
        type_col.label(text=cast(str, entry.file_type) or "-")

        size_col = row.row(align=True)
        size_col.ui_units_x = _SIZE_COLUMN_WIDTH
        size_col.alignment = "RIGHT"
        size_col.label(text=_format_size(cast(int, entry.length)))

    def draw_filter(self, context: Context, layout: bpy.types.UILayout):  # pyright: ignore[reportIncompatibleMethodOverride]
        # Suppressed on purpose - UIList's own built-in filter row (a collapsed search icon plus
        # sort/invert buttons) would just duplicate the always-visible search field this addon
        # already draws in the panel (scene.pso_gsl_search, fed into filter_items below) and the
        # sort toggle next to the Name column header - drawing nothing here avoids showing the
        # same functionality twice in two different places.
        pass

    def filter_items(self, context: Context, data: Any, propname: str):  # pyright: ignore[reportIncompatibleMethodOverride]
        items = getattr(data, propname)
        scene = context.scene
        search = cast(str, getattr(scene, "pso_gsl_search", "")) if scene is not None else ""
        type_filter = cast(str, getattr(scene, "pso_gsl_type_filter", "ALL")) if scene is not None else "ALL"
        return _filter_and_sort_gsl_entries(items, search, self.bitflag_filter_item, type_filter)


@final
class PSO_PT_gsl_browser(Panel):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_PT_gsl_browser"
    bl_label = "Data.gsl"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "pso-blender"

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout or context.scene is None:
            return
        scene = cast(SceneWithGslBrowserSettings, context.scene)

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(scene, "pso_gsl_path", text="")
        row.operator(PsoLoadGslArchive.bl_idname, text="", icon="FILE_REFRESH")
        if not cast(str, scene.pso_gsl_path):
            # Never write into scene.pso_gsl_path from here - doing so raises "Writing to ID
            # classes in this context is not allowed" (confirmed live). Just show the suggestion;
            # PsoLoadGslArchive.execute() applies the same guess itself if the field is still
            # empty when the button is actually clicked.
            guessed = _guess_gsl_path()
            if guessed:
                col.label(text="Detected: " + guessed, icon="INFO")

        if len(cast(Any, scene).pso_gsl_entries) == 0:
            return

        layout.prop(scene, "pso_gsl_search", text="", icon="VIEWZOOM")
        layout.prop(scene, "pso_gsl_type_filter")

        layout.template_list(
            PSO_UL_gsl_entries.bl_idname, "", scene, "pso_gsl_entries", scene, "pso_gsl_entries_index", rows=10)

        extract_row = layout.row(align=True)
        extract_row.prop(scene, "pso_gsl_extract_dir", text="")
        layout.operator(PsoExtractGslEntries.bl_idname, icon="EXPORT")
