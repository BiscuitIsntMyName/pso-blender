import os, json
from typing import cast, final
import bpy
from bpy.types import Context, Menu, Operator, Panel
from bpy.props import StringProperty  # pyright: ignore[reportUnknownVariableType]

MAX_RECENT_RELS = 8


def _recent_rels_path() -> str:
    base = bpy.utils.user_resource("DATAFILES", path="pso_blender_cache", create=True)
    return os.path.join(base, "recent_rel_imports.json")


def load_recent_rels() -> list[dict[str, str]]:
    path = _recent_rels_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_recent_rels(entries: list[dict[str, str]]):
    with open(_recent_rels_path(), "w", encoding="utf-8") as f:
        json.dump(entries, f)


def record_recent_rel(rel_path: str, xvm_path: str, tam_path: str):
    """Adds this REL/XVM/TAM combination to the front of the recent-imports list (see
    PSO_MT_import_rel_recent and PSO_PT_recent_rel, which both read this same list) - called from
    ImportRel.execute() after every import attempt whose main .rel file exists. Deduplicated by
    rel_path (case-insensitive/normalized, matching Windows' filesystem): re-importing the same
    file just refreshes its position to the front instead of piling up duplicate entries.
    """
    entries = load_recent_rels()
    norm = os.path.normcase(os.path.normpath(rel_path))
    entries = [e for e in entries if os.path.normcase(os.path.normpath(e.get("rel_path", ""))) != norm]
    entries.insert(0, {"rel_path": rel_path, "xvm_path": xvm_path, "tam_path": tam_path})
    save_recent_rels(entries[:MAX_RECENT_RELS])


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ImportRecentRel(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Re-import this REL/XVM/TAM combination without reopening the file dialog"


    bl_idname = "import_scene.rel_recent"
    bl_label = "Re-import Recent REL"
    bl_options = {"REGISTER"}

    rel_path: StringProperty(options={"HIDDEN"})
    xvm_path: StringProperty(options={"HIDDEN"})
    tam_path: StringProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, context: Context, properties: "ImportRecentRel") -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        # Overrides the tooltip per-button (Blender's documented mechanism for this - each entry
        # in the recent-imports list/panel calls this same operator with different property
        # values) to show the actual source path instead of the generic class docstring, which
        # was identical and unhelpful across every entry.
        return cast(str, properties.rel_path) or cls.__doc__ or ""

    # Deliberately has no invoke() - a UI button always triggers a click via Blender's default
    # invoke flow, and an Operator with no invoke() of its own falls straight through to
    # execute() (Blender's documented default), which is what makes this a true one-click
    # reimport instead of reopening ImportRel's file-picker dialog.
    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        # Invoked by idname string, not by importing ImportRel directly, so this file has no
        # import-time dependency on rel_import_menu.py (which itself depends on this file to
        # record history - importing the class here instead would make that circular).
        result = bpy.ops.import_scene.rel(  # pyright: ignore[reportAttributeAccessIssue]
            "EXEC_DEFAULT", rel_path=self.rel_path, xvm_path=self.xvm_path, tam_path=self.tam_path)
        return cast(set[str], result)


@final
class PSO_MT_import_rel_recent(Menu):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_MT_import_rel_recent"
    bl_label = "REL (PSO)"

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout:
            return
        layout.operator("import_scene.rel", text="Select File...", icon="FILEBROWSER")
        recents = load_recent_rels()
        if not recents:
            return
        layout.separator()
        for entry in recents:
            rel_path = entry.get("rel_path", "")
            if not rel_path:
                continue
            op = layout.operator(ImportRecentRel.bl_idname, text=os.path.basename(rel_path), icon="FILE")
            op.rel_path = rel_path  # pyright: ignore[reportAttributeAccessIssue]
            op.xvm_path = entry.get("xvm_path", "")  # pyright: ignore[reportAttributeAccessIssue]
            op.tam_path = entry.get("tam_path", "")  # pyright: ignore[reportAttributeAccessIssue]


@final
class PSO_PT_recent_rel(Panel):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_PT_recent_rel"
    bl_label = "Recent REL Imports"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "pso-blender"

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout:
            return
        recents = load_recent_rels()
        if not recents:
            layout.label(text="No recent imports")
            return
        for entry in recents:
            rel_path = entry.get("rel_path", "")
            if not rel_path:
                continue
            op = layout.operator(ImportRecentRel.bl_idname, text=os.path.basename(rel_path), icon="FILE")
            op.rel_path = rel_path  # pyright: ignore[reportAttributeAccessIssue]
            op.xvm_path = entry.get("xvm_path", "")  # pyright: ignore[reportAttributeAccessIssue]
            op.tam_path = entry.get("tam_path", "")  # pyright: ignore[reportAttributeAccessIssue]
