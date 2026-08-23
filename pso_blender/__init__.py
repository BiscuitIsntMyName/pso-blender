import sys


# This add-on is packaged/installed as a Blender 4.2+ Extension - blender_manifest.toml, not the
# bl_info dict below, is what Blender actually reads for the version shown in Preferences > Add-ons
# (and for name/maintainer/etc). Keep bl_info around only for whatever legacy tooling still expects
# it to exist, and bump the version in blender_manifest.toml, not here.
bl_info = {
    "name": "Phantasy Star Online (PSO) file formats",
    "blender": (3, 4, 0),
    "category": "Import-Export",
}


if "unittest" not in sys.modules.keys():
    from .blender_addon import register, unregister  # pyright: ignore[reportUnusedImport]

    if __name__ == "__main__":
        register()
