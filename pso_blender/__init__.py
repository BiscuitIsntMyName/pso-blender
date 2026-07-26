import sys


bl_info = {
    "name": "Phantasy Star Online (PSO) file formats",
    "blender": (3, 4, 0),
    "category": "Import-Export",
}


if "unittest" not in sys.modules.keys():
    from .blender_addon import register, unregister  # pyright: ignore[reportUnusedImport]

    if __name__ == "__main__":
        register()
