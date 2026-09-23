/* Mario's particle models (bubble, water splash, water rings, droplet, dust,
 * sparkles), built once from the same decoded ROM segment 4 that holds his own
 * textures, plus his lava flame from common1 (segment 3). A bounded Fast3D reader follows each particle frame's actual
 * display list: its vertices, texture load, render tile and combiner. Any
 * command, address or format outside that small set refuses the whole set;
 * Mario's own model is unaffected. Addresses and layers: n64decomp actors/
 * (bubble, walk_smoke, burn_smoke, stomp_smoke, water_wave, sparkle,
 * water_splash, white_particle_small, sparkle_animation, flame) and
 * levels/scripts.c.
 */
#include "internal.h"
#include <string.h>

static const struct { uint32_t dl; uint32_t layer; } frames[] = {
    {0x0401DD60u, SG_PARTICLE_CUTOUT},                               /* bubble */
    {0x04021718u, SG_PARTICLE_BLEND}, {0x04021730u, SG_PARTICLE_BLEND},  /* smoke (dust) */
    {0x04021748u, SG_PARTICLE_BLEND}, {0x04021760u, SG_PARTICLE_BLEND},
    {0x04021778u, SG_PARTICLE_BLEND}, {0x04021790u, SG_PARTICLE_BLEND},
    {0x040217A8u, SG_PARTICLE_BLEND},
    {0x04022070u, SG_PARTICLE_BLEND},                                /* burn smoke */
    {0x040251F8u, SG_PARTICLE_BLEND}, {0x04025210u, SG_PARTICLE_BLEND},  /* small water splash */
    {0x04025228u, SG_PARTICLE_BLEND}, {0x04025240u, SG_PARTICLE_BLEND},
    {0x04025258u, SG_PARTICLE_BLEND}, {0x04025270u, SG_PARTICLE_BLEND},
    {0x04025288u, SG_PARTICLE_BLEND}, {0x040252A0u, SG_PARTICLE_BLEND},
    {0x040252B8u, SG_PARTICLE_BLEND}, {0x040252D0u, SG_PARTICLE_BLEND},
    {0x040252E8u, SG_PARTICLE_BLEND}, {0x04025300u, SG_PARTICLE_BLEND},
    {0x040273F0u, SG_PARTICLE_BLEND}, {0x04027408u, SG_PARTICLE_BLEND},  /* water rings */
    {0x04027420u, SG_PARTICLE_BLEND}, {0x04027438u, SG_PARTICLE_BLEND},
    {0x0402A4F8u, SG_PARTICLE_CUTOUT}, {0x0402A510u, SG_PARTICLE_CUTOUT}, /* sparkles */
    {0x0402A528u, SG_PARTICLE_CUTOUT}, {0x0402A540u, SG_PARTICLE_CUTOUT},
    {0x0402A558u, SG_PARTICLE_CUTOUT}, {0x0402A570u, SG_PARTICLE_CUTOUT},
    {0x04032640u, SG_PARTICLE_CUTOUT}, {0x04032658u, SG_PARTICLE_CUTOUT}, /* water splash */
    {0x04032670u, SG_PARTICLE_CUTOUT}, {0x04032688u, SG_PARTICLE_CUTOUT},
    {0x040326A0u, SG_PARTICLE_CUTOUT}, {0x040326B8u, SG_PARTICLE_CUTOUT},
    {0x040326D0u, SG_PARTICLE_CUTOUT}, {0x040326E8u, SG_PARTICLE_CUTOUT},
    {0x04032A18u, SG_PARTICLE_CUTOUT},                               /* water droplet */
    {0x04035300u, SG_PARTICLE_BLEND}, {0x04035318u, SG_PARTICLE_BLEND},  /* sparkle animation */
    {0x04035330u, SG_PARTICLE_BLEND}, {0x04035348u, SG_PARTICLE_BLEND},
    {0x04035360u, SG_PARTICLE_BLEND},
};
static const struct { uint32_t dl; uint32_t layer; } common1_frames[] = {
    {0x0301B3B0u, SG_PARTICLE_BLEND}, {0x0301B3C8u, SG_PARTICLE_BLEND},  /* red flame (bhvFlameMario) */
    {0x0301B3E0u, SG_PARTICLE_BLEND}, {0x0301B3F8u, SG_PARTICLE_BLEND},
    {0x0301B410u, SG_PARTICLE_BLEND}, {0x0301B428u, SG_PARTICLE_BLEND},
    {0x0301B440u, SG_PARTICLE_BLEND}, {0x0301B458u, SG_PARTICLE_BLEND},
    {0x03000880u, SG_PARTICLE_BLEND}, {0x03000920u, SG_PARTICLE_BLEND},  /* mist, white puff (ground pound, dust) */
    {0x0302BCD0u, SG_PARTICLE_CUTOUT},                               /* sand (bhvGroundSand) */
    {0x0301CDE0u, SG_PARTICLE_CUTOUT},                               /* leaves (bhvSnowLeafParticleSpawn) */
    {0x0302C8A0u, SG_PARTICLE_CUTOUT},                               /* snow (bhvGroundSnow, white particle) */
};

static uint32_t be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}
static uint16_t be16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }

struct reader {
    const uint8_t *segment;
    uint32_t size, segment_id;
    struct sg_particles *out;
    /* RSP/RDP state this subset needs. */
    int16_t slot_position[16][3], slot_st[16][2];
    uint8_t slot_color[16][4];
    uint32_t slots_loaded;
    uint32_t image, image_format, image_size, loaded, loaded_texels;
    uint32_t tile_format, tile_size, tile_width, tile_height, texture_on;
    float scale_s, scale_t;
    uint8_t environment[4];
    uint32_t fades; /* a triangle took its alpha from the environment */
    int combiner; /* 0 none, 1 decal, 2 modulate by shade, 3 modulate by environment,
                   * 4 colour by shade, alpha by environment (MODULATEIFADEA) */
};

static int offset(const struct reader *r, uint32_t address, uint32_t bytes, uint32_t *out)
{
    if ((address >> 24) != r->segment_id) return 0;
    address &= 0x00ffffffu;
    if (address > r->size || bytes > r->size - address) return 0;
    *out = address;
    return 1;
}

/* One cycle of G_SETCOMBINE: DECALRGBA, MODULATEIA/RGBA, FADEA (texture
 * times environment) or MODULATEIFADEA (shade colour, environment alpha: the
 * object's opacity, which playback applies), else refused. */
static int combiner(uint32_t w0, uint32_t w1)
{
    const uint32_t a = (w0 >> 20) & 15u, c = (w0 >> 15) & 31u, aa = (w0 >> 12) & 7u, ac = (w0 >> 9) & 7u;
    const uint32_t b = (w1 >> 28) & 15u, d = (w1 >> 15) & 7u, ab = (w1 >> 12) & 7u, ad = (w1 >> 9) & 7u;
    if (c == 31u && d == 1u && ac == 7u && ad == 1u) return 1;
    if (a == 1u && b == 15u && c == 4u && d == 7u && aa == 1u && ab == 7u && ac == 4u && ad == 7u) return 2;
    if (a == 1u && b == 15u && c == 5u && d == 7u && aa == 1u && ab == 7u && ac == 5u && ad == 7u) return 3;
    if (a == 1u && b == 15u && c == 4u && d == 7u && aa == 1u && ab == 7u && ac == 5u && ad == 7u) return 4;
    return 0;
}

static int texel(const struct reader *r, uint32_t x, uint32_t y, uint8_t rgba[4])
{
    uint32_t at;
    const uint32_t index = y * r->tile_width + x;
    if (r->tile_size != 2u || index >= r->loaded_texels) return 0;
    at = r->loaded + index * 2u;
    if (r->tile_format == 0u) { /* RGBA16 5551, also for data the game mislabels */
        const unsigned v = be16(r->segment + at);
        rgba[0] = (uint8_t)(((v >> 11) & 31u) * 255u / 31u);
        rgba[1] = (uint8_t)(((v >> 6) & 31u) * 255u / 31u);
        rgba[2] = (uint8_t)(((v >> 1) & 31u) * 255u / 31u);
        rgba[3] = (v & 1u) ? 255 : 0;
        return 1;
    }
    if (r->tile_format == 3u) { /* IA16 */
        rgba[0] = rgba[1] = rgba[2] = r->segment[at];
        rgba[3] = r->segment[at + 1];
        return 1;
    }
    return 0;
}

/* The loaded texture gets one atlas cell (reused when another frame loads
 * the same image the same way). Returns the cell index or -1. */
static int cell(struct reader *r)
{
    struct sg_particles *p = r->out;
    uint32_t i, x, y;
    for (i = 0; i < p->cell_count; ++i)
        if (p->cells[i].image == r->loaded && p->cells[i].format == r->tile_format &&
            p->cells[i].width == r->tile_width && p->cells[i].height == r->tile_height) return (int)i;
    if (p->cell_count >= SG_PARTICLE_CELLS || r->tile_width > 32u || r->tile_height > 64u ||
        !r->tile_width || !r->tile_height || r->tile_width * r->tile_height > r->loaded_texels) return -1;
    i = p->cell_count++;
    p->cells[i].image = r->loaded; p->cells[i].format = r->tile_format;
    p->cells[i].width = r->tile_width; p->cells[i].height = r->tile_height;
    p->cells[i].x = (i % (SG_PARTICLE_ATLAS_WIDTH / 32u)) * 32u;
    p->cells[i].y = (i / (SG_PARTICLE_ATLAS_WIDTH / 32u)) * 64u;
    for (y = 0; y < r->tile_height; ++y) for (x = 0; x < r->tile_width; ++x) {
        uint8_t *dest = p->atlas + ((p->cells[i].y + y) * SG_PARTICLE_ATLAS_WIDTH + p->cells[i].x + x) * 4u;
        if (!texel(r, x, y, dest)) return -1;
    }
    return (int)i;
}

static int triangle(struct reader *r, const uint32_t index[3])
{
    struct sg_particles *p = r->out;
    int c;
    uint32_t k;
    if (!r->texture_on || !r->combiner || p->vertex_count + 3u > SG_PARTICLE_MAX_VERTICES) return 0;
    c = cell(r);
    if (c < 0) return 0;
    for (k = 0; k < 3u; ++k) {
        const uint32_t s = index[k];
        sg_particle_vertex *v = &p->vertices[p->vertex_count++];
        const struct sg_particle_cell *at = &p->cells[c];
        if (s >= 16u || !(r->slots_loaded & (1u << s))) return 0;
        v->position[0] = r->slot_position[s][0];
        v->position[1] = r->slot_position[s][1];
        v->position[2] = r->slot_position[s][2];
        /* S10.5 coordinates times the G_TEXTURE scale are render-tile texels. */
        v->uv[0] = (at->x + r->slot_st[s][0] / 32.0f * r->scale_s) / SG_PARTICLE_ATLAS_WIDTH;
        v->uv[1] = (at->y + r->slot_st[s][1] / 32.0f * r->scale_t) / SG_PARTICLE_ATLAS_HEIGHT;
        v->rect[0] = (at->x + .5f) / SG_PARTICLE_ATLAS_WIDTH;
        v->rect[1] = (at->y + .5f) / SG_PARTICLE_ATLAS_HEIGHT;
        v->rect[2] = (at->x + at->width - .5f) / SG_PARTICLE_ATLAS_WIDTH;
        v->rect[3] = (at->y + at->height - .5f) / SG_PARTICLE_ATLAS_HEIGHT;
        if (r->combiner == 1) memset(v->rgba, 255, 4); /* decal: texture only */
        else if (r->combiner == 3) memcpy(v->rgba, r->environment, 4);
        else if (r->combiner == 4) { memcpy(v->rgba, r->slot_color[s], 3); v->rgba[3] = 255; r->fades = 1; }
        else memcpy(v->rgba, r->slot_color[s], 4);
    }
    return 1;
}

static int run(struct reader *r, uint32_t start)
{
    uint32_t stack[4], depth = 0, pc, steps = 0;
    if (!offset(r, start, 8u, &pc)) return 0;
    for (;;) {
        uint32_t w0, w1, at;
        if (++steps > 256u || pc > r->size - 8u) return 0;
        w0 = be32(r->segment + pc); w1 = be32(r->segment + pc + 4u); pc += 8u;
        switch (w0 >> 24) {
        case 0xE6: case 0xE7: case 0xE8: break;                   /* syncs */
        case 0xB6: case 0xB7: break;                             /* geometry mode: lighting only matters via combiner */
        case 0xFC: r->combiner = combiner(w0, w1); break;
        case 0xFB:                                               /* G_SETENVCOLOR */
            r->environment[0] = (uint8_t)(w1 >> 24); r->environment[1] = (uint8_t)(w1 >> 16);
            r->environment[2] = (uint8_t)(w1 >> 8); r->environment[3] = (uint8_t)w1;
            break;
        case 0xBB:                                               /* G_TEXTURE */
            r->texture_on = w0 & 0xffu;
            r->scale_s = (float)(w1 >> 16) / 65536.0f; r->scale_t = (float)(w1 & 0xffffu) / 65536.0f;
            break;
        case 0xFD:                                               /* G_SETTIMG */
            r->image = w1; r->image_format = (w0 >> 21) & 7u; r->image_size = (w0 >> 19) & 3u;
            break;
        case 0xF5:                                               /* G_SETTILE */
            if (((w1 >> 24) & 7u) == 0u) {                        /* render tile */
                if ((w0 & 0x1ffu) != 0u) return 0;                /* TMEM 0: what LOADBLOCK wrote */
                r->tile_format = (w0 >> 21) & 7u; r->tile_size = (w0 >> 19) & 3u;
            }
            break;
        case 0xF3: {                                             /* G_LOADBLOCK */
            const uint32_t texels = ((w1 >> 12) & 0xfffu) + 1u;
            if (r->image_size != 2u || (w0 & 0xffffffu) || !offset(r, r->image, texels * 2u, &at)) return 0;
            r->loaded = at; r->loaded_texels = texels;
            break;
        }
        case 0xF2:                                               /* G_SETTILESIZE */
            if ((w0 & 0xffffffu) || ((w1 >> 24) & 7u)) return 0;
            r->tile_width = (((w1 >> 12) & 0xfffu) >> 2) + 1u;
            r->tile_height = ((w1 & 0xfffu) >> 2) + 1u;
            break;
        case 0x04: {                                             /* G_VTX */
            const uint32_t n = ((w0 >> 20) & 15u) + 1u, first = (w0 >> 16) & 15u;
            uint32_t i;
            if (first + n > 16u || (w0 & 0xffffu) != n * 16u || !offset(r, w1, n * 16u, &at)) return 0;
            for (i = 0; i < n; ++i) {
                const uint8_t *v = r->segment + at + i * 16u;
                r->slot_position[first + i][0] = (int16_t)be16(v);
                r->slot_position[first + i][1] = (int16_t)be16(v + 2);
                r->slot_position[first + i][2] = (int16_t)be16(v + 4);
                r->slot_st[first + i][0] = (int16_t)be16(v + 8);
                r->slot_st[first + i][1] = (int16_t)be16(v + 10);
                memcpy(r->slot_color[first + i], v + 12, 4);
                r->slots_loaded |= 1u << (first + i);
            }
            break;
        }
        case 0xBF: {                                             /* G_TRI1: indices x10 */
            const uint32_t index[3] = {((w1 >> 16) & 0xffu) / 10u, ((w1 >> 8) & 0xffu) / 10u, (w1 & 0xffu) / 10u};
            if (((w1 >> 16) & 0xffu) % 10u || ((w1 >> 8) & 0xffu) % 10u || (w1 & 0xffu) % 10u) return 0;
            if (!triangle(r, index)) return 0;
            break;
        }
        case 0x06:                                               /* G_DL: call, or branch */
            if (((w0 >> 16) & 0xffu) == 0u) { if (depth >= 4u) return 0; stack[depth++] = pc; }
            if (!offset(r, w1, 8u, &pc)) return 0;
            break;
        case 0xB8:                                               /* G_ENDDL */
            if (!depth) return 1;
            pc = stack[--depth];
            break;
        default: return 0;
        }
    }
}

static int append(struct sg_particles *out, const uint8_t *segment, uint32_t size, uint32_t segment_id,
                  uint32_t dl, uint32_t layer)
{
    struct reader r;
    sg_particle_frame *f;
    if (out->frame_count >= SG_PARTICLE_FRAMES) return 0;
    f = &out->frames[out->frame_count];
    memset(&r, 0, sizeof(r));
    r.segment = segment; r.size = size; r.segment_id = segment_id; r.out = out;
    f->display_list = dl; f->layer = layer;
    f->first_vertex = out->vertex_count;
    if (!run(&r, dl) || out->vertex_count == f->first_vertex) return 0;
    f->vertex_count = out->vertex_count - f->first_vertex;
    f->fade = r.fades;
    ++out->frame_count;
    return 1;
}

int sg_particles_load(struct sg_particles *out, const uint8_t *segment, uint32_t size)
{
    uint32_t i;
    memset(out, 0, sizeof(*out));
    for (i = 0; i < sizeof(frames) / sizeof(frames[0]); ++i)
        if (!append(out, segment, size, 4u, frames[i].dl, frames[i].layer)) {
            memset(out, 0, sizeof(*out));
            return SG_INVALID_ROM;
        }
    return SG_OK;
}

int sg_particles_add_common1(struct sg_particles *out, const uint8_t *segment, uint32_t size)
{
    const uint32_t frames_before = out->frame_count, vertices_before = out->vertex_count;
    const uint32_t cells_before = out->cell_count;
    uint32_t i;
    for (i = 0; i < sizeof(common1_frames) / sizeof(common1_frames[0]); ++i)
        if (!append(out, segment, size, 3u, common1_frames[i].dl, common1_frames[i].layer)) {
            /* Roll back: the segment-4 set stays exactly as it was. */
            for (; out->cell_count > cells_before; --out->cell_count) {
                const struct sg_particle_cell *c = &out->cells[out->cell_count - 1];
                uint32_t y;
                for (y = 0; y < 64u; ++y)
                    memset(out->atlas + ((c->y + y) * SG_PARTICLE_ATLAS_WIDTH + c->x) * 4u, 0, 32u * 4u);
                memset(&out->cells[out->cell_count - 1], 0, sizeof(out->cells[0]));
            }
            memset(&out->frames[frames_before], 0, (SG_PARTICLE_FRAMES - frames_before) * sizeof(out->frames[0]));
            memset(&out->vertices[vertices_before], 0,
                   (SG_PARTICLE_MAX_VERTICES - vertices_before) * sizeof(out->vertices[0]));
            out->frame_count = frames_before; out->vertex_count = vertices_before;
            return SG_INVALID_ROM;
        }
    return SG_OK;
}
