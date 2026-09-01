# SPDX-License-Identifier: GPL-2.0-or-later

bl_info = {
    "name": "Visibility Aware UV Optimizer",
    "author": "SEASUN Tools",
    "version": (0, 5, 6),
    "blender": (3, 3, 0),
    "location": "View3D > Sidebar > UV Optimizer",
    "description": "Hard-surface UV optimization with safe small-island and structure grouping",
    "category": "UV",
}

import bpy
from bpy.props import PointerProperty

from . import operators
from . import interactive_export
from . import overlay
from . import properties
from . import ui


_CLASSES = (
    properties.VUVSettings,
    operators.VUV_OT_CreateAutoCameras,
    operators.VUV_OT_RemoveAutoCameras,
    operators.VUV_OT_CreateSphereSampler,
    operators.VUV_OT_RemoveSphereSampler,
    operators.VUV_OT_AnalyzeVisibility,
    operators.VUV_OT_ToggleOverlay,
    operators.VUV_OT_ApplyVisibilityFilter,
    operators.VUV_OT_ShowAllVisibility,
    operators.VUV_OT_SelectByVisibility,
    operators.VUV_OT_SetFaceOverride,
    operators.VUV_OT_ImportProbeEvidence,
    operators.VUV_OT_ClearProbeEvidence,
    operators.VUV_OT_ExportProbeCandidateReport,
    operators.VUV_OT_OptimizeUV,
    interactive_export.VUV_OT_ExportMappingViewer,
    ui.VUV_PT_MainPanel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vuv_settings = PointerProperty(type=properties.VUVSettings)


def unregister():
    overlay.shutdown()
    if hasattr(bpy.types.Scene, "vuv_settings"):
        del bpy.types.Scene.vuv_settings
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
