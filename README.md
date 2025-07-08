# pso-blender

A plugin for Blender (v4.2+) that can import and export the following file formats for Phantasy Star Online Blue Burst:

### Map geometry
* **n.rel**: Map render geometry
* **c.rel**: Map collision geometry
* **r.rel**: Minimap geometry

> [!NOTE]  
> Exporting a file with the name `foo.rel` will write three files named `foon.rel`, `fooc.rel`, and `foor.rel`.

An .xvm will be created when the exported objects contain materials with image input nodes. Animated textures (.tam) can be created by using an image sequence material node.


### BML/XJ: NPCs, objects, items, etc.
Textures can be imported by selecting both the .bml/.xj and the .xvm in the file select menu. Object parenting can be used to create node hierarchy. Empty objects can be used to create meshless nodes.


### XVM
Currently only the DXT1 texture format is supported.


### NJM
Experimental support for exporting skeletal animations exists.


## Panels added by this plugin
* **REL Settings** (context: Object)
  * Used to specify which of the three .rel files the object should be exported into.
* **NJCM Settings** (context: Object)
* **XJ Settings** (context: Material)


## Installation
1. Download or git clone this project.
2. Create a zip file from the pso_blender directory.
3. Open Blender and go to `Edit>Preferences>Add-ons>Install from disk` and select the zip you created.


For more information check out [the wiki on Github](https://github.com/jtuu/pso-blender/wiki).
