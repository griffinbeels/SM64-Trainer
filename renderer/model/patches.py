"""Exact staging patches against inputs.json's pinned libsm64 source."""
from pathlib import Path


def replace(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise ValueError(f"Nonunique model patch anchor in {path.name}: {old[:70]}")
    path.write_text(source.replace(old, new), encoding="utf-8", newline="\n")


def patch(source: Path) -> None:
    def edit(name, old, new):
        replace(source / "src" / name, old, new)

    # Terrain alignment is already captured in throw_matrix. Excluding its
    # simulation-only routine avoids linking a fake floor query into the model.
    edit("decomp/engine/math_util.c", "void mtxf_align_terrain_triangle(",
         "#if 0 /* The pose API receives terrain alignment as a matrix. */\nvoid mtxf_align_terrain_triangle(")
    edit("decomp/engine/math_util.c", "void mtxf_mul(Mat4 dest, Mat4 a, Mat4 b) {",
         "#endif\nvoid mtxf_mul(Mat4 dest, Mat4 a, Mat4 b) {")
    # Keep only the existing transform helpers referenced by the geo callback.
    # The preceding object allocation/action loop is a physics dependency.
    path = source / "src/decomp/game/object_stuff.c"
    text = path.read_text(encoding="utf-8")
    marker = "void create_transformation_from_matrices("
    if text.count(marker) != 1:
        raise ValueError("Object transform helper boundary changed")
    path.write_text('#include "object_stuff.h"\n#include "../shim.h"\n'
                    '#include "../include/object_fields.h"\n' + text[text.index(marker):],
                    encoding="utf-8", newline="\n")
    edit("libsm64.h", "#if defined(_WIN32)",
         "#if defined(SM64_LIB_STATIC)\n#define SM64_LIB_FN\n#elif defined(_WIN32)")
    edit("decomp/include/platform_info.h", "#define IS_BIG_ENDIAN (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)",
         "#ifdef _WIN32\n#define IS_BIG_ENDIAN 0\n#else\n#define IS_BIG_ENDIAN (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)\n#endif")
    edit("decomp/include/macros.h", "#else\n#define ALIGNED8\n", "#else\n#define ALIGNED8 __declspec(align(8))\n")
    edit("decomp/include/macros.h", "#else\n#define ALIGNED16\n", "#else\n#define ALIGNED16 __declspec(align(16))\n")
    edit("decomp/include/PR/libaudio.h", "u8 *offset __attribute__((aligned (8)));", "__declspec(align(8)) u8 *offset;")
    edit("decomp/include/PR/libaudio.h", "s32 len __attribute__((aligned (8)));", "__declspec(align(8)) s32 len;")
    edit("decomp/include/PR/libaudio.h", "} __attribute__((aligned (16))) ALSeqFile;", "} ALSeqFile;")
    edit("decomp/include/PR/libaudio.h", "typedef struct\n{\n    unsigned short  revision;",
         "typedef struct __declspec(align(16))\n{\n    unsigned short  revision;")
    path = source / "src/gfx_adapter.h"
    path.write_text(path.read_text(encoding="utf-8").replace("({})", "((void)0)"), encoding="utf-8", newline="\n")
    edit("gfx_adapter_commands.h", "    GFXCMD_None = 0,", "    GFXCMD_None = 0,\n    GFXCMD_Material,")
    edit("gfx_macros.h", '#include "gfx_adapter_commands.h"', '#include "gfx_adapter_commands.h"\n#include "model.h"')
    edit("gfx_macros.h", "#define gsDPSetCombineMode(a, b) (GFXCMD_None)", """
/* Token pasting retains the actual named combiner before its RDP expansion.
 * Both cycles must map to the same supported behavior; unknown modes fail the
 * build instead of silently losing their material semantics.
 */
#define SG_MODE_G_CC_SHADE SG_MATERIAL_SOLID
#define SG_MODE_G_CC_SHADEFADEA SG_MATERIAL_SOLID
#define SG_MODE_G_CC_BLENDRGBA SG_MATERIAL_ALPHA_MIX
#define SG_MODE_G_CC_BLENDRGBFADEA SG_MATERIAL_ALPHA_MIX
#define SG_MODE_G_CC_DECALRGBA SG_MATERIAL_DECAL_RGBA
#define SG_MODE_G_CC_DECALFADEA SG_MATERIAL_DECAL_RGBA
#define SG_MODE_G_CC_DECALFADE SG_MATERIAL_DECAL_RGB
#define SG_MODE_G_CC_MODULATERGB SG_MATERIAL_MODULATE_RGB
#define SG_MODE_G_CC_MODULATERGBFADEA SG_MATERIAL_MODULATE_RGB
#define SG_MODE_G_CC_MODULATERGBA SG_MATERIAL_MODULATE_RGBA
#define gsDPSetCombineMode(a, b) GFXCMD_Material, SG_MODE_##a, SG_MODE_##b
""")
    edit("decomp/game/mario_misc.c", '#include "../include/PR/ultratypes.h"',
         '#include "../include/PR/ultratypes.h"\nextern const int16_t *sg_effective_head_rotation(void);')
    edit("decomp/game/mario_misc.c",
         "            vec3s_set(bodyState->headAngle, 0, 0, 0);\n            vec3s_set(rotNode->rotation, 0, 0, 0);",
         "            const int16_t *head = sg_effective_head_rotation();\n"
         "            rotNode->rotation[0] = head[0];\n            rotNode->rotation[1] = head[1];\n"
         "            rotNode->rotation[2] = head[2];")
    edit("decomp/game/rendering_graph_node.c", "void geo_process_root_hack_single_node(struct GraphNode *node)",
         "void sg_traverse_frozen(struct GraphNode *node)")
    edit("decomp/game/rendering_graph_node.c", "geo_set_animation_globals(&gMarioObject->header.gfx.animInfo, 1);",
         "geo_set_animation_globals(&gMarioObject->header.gfx.animInfo, 0);")
    path = source / "src/decomp/game/rendering_graph_node.c"
    text = path.read_text(encoding="utf-8").replace("gDisplayListHead++", "NULL")
    text += """
void sg_traversal_reset(void) {
    gCurGraphNodeRoot = NULL; gCurGraphNodeMasterList = NULL;
    gCurGraphNodeCamFrustum = NULL; gCurGraphNodeCamera = NULL;
    gCurGraphNodeObject = NULL; gCurGraphNodeHeldObject = NULL;
    gDisplayListHeap = NULL; gDisplayListHead = NULL; gMatStackIndex = 0;
}
"""
    path.write_text(text, encoding="utf-8", newline="\n")
    edit("gfx_adapter.c", '#include "libsm64.h"', '#include "libsm64.h"\n#include "model.h"\n#include <math.h>')
    edit("gfx_adapter.c", "static Mat4 s_curMatrix;", "static Mat4 s_curMatrix;\nstatic Mat4 s_normalMatrix;")
    edit("gfx_adapter.c", "static struct SM64MarioGeometryBuffers *s_outBuffers;",
         "static struct SM64MarioGeometryBuffers *s_outBuffers;\n"
         "static struct SM64MarioGeometryBuffers s_model_output;\n"
         "static uint32_t s_capacity, s_errors, s_material;\nstatic uint32_t *s_materialPtr;")
    edit("gfx_adapter.c", "            case GFXCMD_VertexData:",
         "            case GFXCMD_Material: {\n"
         "                uint32_t first = (uint32_t)*ptr++, second = (uint32_t)*ptr++;\n"
         "                if (first != second || first > SG_MATERIAL_MODULATE_RGBA)\n"
         "                    s_errors |= SG_MESH_UNSUPPORTED_MATERIAL;\n"
         "                s_material = first; break;\n            }\n"
         "            case GFXCMD_VertexData:")
    edit("gfx_adapter.c", "                UNUSED intptr_t flag0 = *ptr++;",
         "                UNUSED intptr_t flag0 = *ptr++;\n"
         "                if (s_outBuffers->numTrianglesUsed >= s_capacity / 3) {\n"
         "                    s_errors |= SG_MESH_TOO_SMALL; break;\n                }\n"
         "                *s_materialPtr++ = s_textureOn ? s_material : SG_MATERIAL_SOLID;\n"
         "                *s_materialPtr++ = s_textureOn ? s_material : SG_MATERIAL_SOLID;\n"
         "                *s_materialPtr++ = s_textureOn ? s_material : SG_MATERIAL_SOLID;")
    edit("gfx_adapter.c", "                s_textureIndex = (int)i;",
         "                s_textureIndex = (int)i;\n"
         "                if (s_textureIndex < 0 || s_textureIndex >= NUM_USED_TEXTURES) {\n"
         "                    s_errors |= SG_MESH_UNSUPPORTED_MATERIAL; s_textureIndex = 0;\n                }")
    path = source / "src/gfx_adapter.c"
    text = path.read_text(encoding="utf-8")
    text = text.replace("mtxf_mul_vec3f_x( s_curMatrix, n", "mtxf_mul_vec3f_x( s_normalMatrix, n")
    text = text.replace("// TODO normals arent correct under non-uniform scale. multiply by inverse/transpose",
                        "// Inverse-transpose preserves normals under nonuniform scale.")
    text = text.replace("    guMtxL2F( s_curMatrix, m );", "    guMtxL2F( s_curMatrix, m );\n    sg_normal_matrix();")
    normal = """
static void sg_normal_matrix(void) {
    double a=s_curMatrix[0][0], b=s_curMatrix[0][1], c=s_curMatrix[0][2];
    double d=s_curMatrix[1][0], e=s_curMatrix[1][1], f=s_curMatrix[1][2];
    double g=s_curMatrix[2][0], h=s_curMatrix[2][1], i=s_curMatrix[2][2];
    double det=a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g);
    mtxf_identity(s_normalMatrix);
    if (!(det > 1e-24 || det < -1e-24)) { s_errors |= SG_MESH_NONFINITE; return; }
    s_normalMatrix[0][0]=(float)((e*i-f*h)/det);
    s_normalMatrix[0][1]=(float)((f*g-d*i)/det);
    s_normalMatrix[0][2]=(float)((d*h-e*g)/det);
    s_normalMatrix[1][0]=(float)((c*h-b*i)/det);
    s_normalMatrix[1][1]=(float)((a*i-c*g)/det);
    s_normalMatrix[1][2]=(float)((b*g-a*h)/det);
    s_normalMatrix[2][0]=(float)((b*f-c*e)/det);
    s_normalMatrix[2][1]=(float)((c*d-a*f)/det);
    s_normalMatrix[2][2]=(float)((a*e-b*d)/det);
}
"""
    text = text.replace("void gSPMatrix(", normal + "\nvoid gSPMatrix(")
    text += """
void sg_geometry_bind(sg_mesh *mesh) {
    s_model_output.position=mesh->position; s_model_output.normal=mesh->normal;
    s_model_output.color=mesh->color; s_model_output.uv=mesh->uv;
    s_capacity=mesh->vertex_capacity; s_errors=0;
    s_materialPtr=mesh->material; s_material=SG_MATERIAL_SOLID;
    s_textureOn=0; s_textureIndex=0; s_scaleS=s_scaleT=0; s_uls=s_ult=0;
    s_texWidth=s_texHeight=64; s_curColor[0]=s_curColor[1]=s_curColor[2]=1;
    gfx_adapter_bind_output_buffers(&s_model_output);
}
uint32_t sg_geometry_errors(void) { return s_errors; }
uint32_t sg_geometry_vertex_count(void) { return s_model_output.numTrianglesUsed * 3u; }
void sg_geometry_reset(void) {
    s_outBuffers=NULL; s_trianglePtr=s_colorPtr=s_normalPtr=s_uvPtr=NULL;
    s_materialPtr=NULL; s_material=SG_MATERIAL_SOLID;
    memset(&s_model_output,0,sizeof(s_model_output)); s_capacity=s_errors=0;
}
"""
    path.write_text(text, encoding="utf-8", newline="\n")
