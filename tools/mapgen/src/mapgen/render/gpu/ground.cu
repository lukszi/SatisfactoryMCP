// terrain/ground_detail/reference.py on the GPU, a thread a pixel: the layers' weights from
// a piece's window, the noise's cells, each present layer's reads, the height blend and the
// overlay. The float32 operations of the reference in its order; the texel reads are
// texels.cu's. Compiled with --fmad=false (mapgen.jit). docs/map/painted.md section 30,
// "The layers' own textures".

#define TOP_LAYERS 4
#define NO_LAYER 255
#define MAX_LAYERS 32
#define CELLS_NONE 0
#define CELLS_ROTATED 1
#define BLEND_FLOOR 1e-4f

struct Corners {
    long long a, b;
    float f;
};

__device__ long long wrapped(long long i, long long n) {
    long long m = i % n;
    return m < 0 ? m + n : m;
}

__device__ Corners corners(float position, long long size) {
    float at = position - 0.5f;
    float first = floorf(at);
    Corners c;
    c.f = at - first;
    long long low = (long long)first;
    c.a = wrapped(low, size);
    c.b = wrapped(low + 1, size);
    return c;
}

__device__ float bilinear(const float* texels, long long width, int channels, int k,
                          long long y0, long long y1, float fy, long long x0, long long x1,
                          float fx) {
    float gx = 1.0f - fx, gy = 1.0f - fy;
    float a = texels[(y0 * width + x0) * channels + k];
    float b = texels[(y0 * width + x1) * channels + k];
    float c = texels[(y1 * width + x0) * channels + k];
    float d = texels[(y1 * width + x1) * channels + k];
    float top = a * gx + b * fx;
    float bottom = c * gx + d * fx;
    return top * gy + bottom * fy;
}

// One atlas: its texels, width, channels and tiles (first column, first row, width, height).
struct Atlas {
    const float* texels;
    int width;
    int channels;
    const int* tiles;
};

__device__ void texel(Atlas atlas, int tile, float u, float v, float* out) {
    const int* t = atlas.tiles + tile * 4;
    Corners r = corners(v, t[3]), c = corners(u, t[2]);
    for (int k = 0; k < atlas.channels; ++k)
        out[k] = bilinear(atlas.texels, atlas.width, atlas.channels, k, t[1] + r.a, t[1] + r.b,
                          r.f, t[0] + c.a, t[0] + c.b, c.f);
}

struct Cells {
    float du, dv, scale, cs, sn, mask;
};

__device__ float in_tile(float t, float size) { return (t - floorf(t)) * size; }

__device__ Cells cells_at(const float* noise, int side, float u, float v) {
    float n = (float)side;
    float inverse = 0.01f;
    float tu = u * inverse, tv = v * inverse;
    Corners r = corners(in_tile(tv, n), side), c = corners(in_tile(tu, n), side);
    float t[6];
    for (int k = 0; k < 6; ++k) t[k] = bilinear(noise, side, 6, k, r.a, r.b, r.f, c.a, c.b, c.f);
    Cells out;
    out.du = t[0];
    out.dv = t[1];
    out.scale = t[2];
    out.cs = t[3];
    out.sn = t[4];
    out.mask = fminf(fmaxf(2.0f * t[5] - 0.5f, 0.0f), 1.0f);
    return out;
}

// One read of a layer's tile at (u, v) metres into `plain`, and into `cell` its read in the
// cells; true when it has cells. `read` is the tile, the cells' kind and the inverse repeat.
__device__ bool read(Atlas atlas, int tile, int mode, float inverse, float u, float v,
                     const Cells& c, float* plain, float* cell) {
    const int* t = atlas.tiles + tile * 4;
    float w = (float)t[2], h = (float)t[3];
    float tu = u * inverse, tv = v * inverse;
    texel(atlas, tile, in_tile(tu, w), in_tile(tv, h), plain);
    if (mode == CELLS_NONE) return false;
    float su = c.scale * (tu + c.du), sv = c.scale * (tv + c.dv);
    if (mode == CELLS_ROTATED) {
        float ru = c.cs * su - c.sn * sv;
        float rv = c.sn * su + c.cs * sv;
        su = ru;
        sv = rv;
    }
    texel(atlas, tile, in_tile(su, w), in_tile(sv, h), cell);
    return true;
}

__device__ void mixed(float* plain, const float* cell, int channels, float mask) {
    for (int k = 0; k < channels; ++k) plain[k] = plain[k] + mask * (cell[k] - plain[k]);
}

// A normal read into `out`, the turned cell's turned back before it is mixed in.
__device__ void normal_read(Atlas surface, int tile, int mode, float inverse, float u, float v,
                            const Cells& c, float* out) {
    float cell[2];
    if (!read(surface, tile, mode, inverse, u, v, c, out, cell)) return;
    if (mode == CELLS_ROTATED) {
        float east = c.cs * cell[0] + c.sn * cell[1];
        float south = c.cs * cell[1] - c.sn * cell[0];
        cell[0] = east;
        cell[1] = south;
    }
    mixed(out, cell, 2, c.mask);
}

// The layers' table: per layer, its albedo, normal and height read's tile, cells, inverse.
struct Layers {
    const int* tiles;
    const int* cells;
    const float* inverse;
};

__device__ void blend(Atlas colour, Atlas surface, Layers layers, const int* present, int count,
                      const float* weight, float u, float v, const Cells& c, float* ratio,
                      float* normal) {
    float sum = 0.0f, sum_low = 0.0f;
    float albedo[3] = {0.0f, 0.0f, 0.0f}, low[3] = {0.0f, 0.0f, 0.0f};
    float nrm[2] = {0.0f, 0.0f};
    for (int i = 0; i < count; ++i) {
        int id = present[i];
        float w = weight[id];
        if (!(w > 0.0f)) continue;
        const int* t = layers.tiles + id * 3;
        const int* m = layers.cells + id * 3;
        const float* s = layers.inverse + id * 3;
        float col[6], col_cell[6], hgt[2], hgt_cell[2];
        if (read(colour, t[0], m[0], s[0], u, v, c, col, col_cell))
            mixed(col, col_cell, 6, c.mask);
        if (read(surface, t[2], m[2], s[2], u, v, c, hgt, hgt_cell))
            mixed(hgt, hgt_cell, 2, c.mask);
        float lift = 2.0f * w - 1.0f;
        float h = fminf(fmaxf(lift + hgt[0], BLEND_FLOOR), 1.0f);
        float h_low = fminf(fmaxf(lift + hgt[1], BLEND_FLOOR), 1.0f);
        sum = sum + h;
        sum_low = sum_low + h_low;
        for (int k = 0; k < 3; ++k) {
            albedo[k] = albedo[k] + h * col[k];
            low[k] = low[k] + h_low * col[3 + k];
        }
        if (t[1] >= 0) {
            float n[2];
            normal_read(surface, t[1], m[1], s[1], u, v, c, n);
            nrm[0] = nrm[0] + h * n[0];
            nrm[1] = nrm[1] + h * n[1];
        }
    }
    for (int k = 0; k < 3; ++k) {
        float mean = albedo[k] / sum;
        float mean_low = low[k] / sum_low;
        ratio[k] = mean / fmaxf(mean_low, BLEND_FLOOR);
    }
    normal[0] = nrm[0] / sum;
    normal[1] = nrm[1] / sum;
}

__device__ void overlay(Atlas colour, Atlas surface, Layers layers, int id, float w, float u,
                        float v, const Cells& c, float* ratio, float* normal) {
    const int* t = layers.tiles + id * 3;
    const int* m = layers.cells + id * 3;
    const float* s = layers.inverse + id * 3;
    float col[6], col_cell[6];
    if (read(colour, t[0], m[0], s[0], u, v, c, col, col_cell)) mixed(col, col_cell, 6, c.mask);
    for (int k = 0; k < 3; ++k) {
        float own = col[k] / fmaxf(col[3 + k], BLEND_FLOOR);
        ratio[k] = ratio[k] + w * (own - ratio[k]);
    }
    if (t[1] >= 0) {
        float n[2];
        normal_read(surface, t[1], m[1], s[1], u, v, c, n);
        normal[0] = normal[0] + w * (n[0] - normal[0]);
        normal[1] = normal[1] + w * (n[1] - normal[1]);
    }
}

// ids, weights: the piece's window of the leading layers, `plane` texels each; wet: the
// overlay's weight there. Taps: rows' and columns', two each, indices into the window.
extern "C" __global__ void ground_detail(
    int rows, int cols, const float* u, const float* v, const unsigned char* ids,
    const unsigned char* weights, const unsigned char* wet, int window_cols, long long plane,
    const int* row_index, const float* row_weight, const int* col_index,
    const float* col_weight, const int* present, int count, const int* tiles, const int* cells,
    const float* inverse, int overlay_id, const float* colour, int colour_width,
    const int* colour_tiles, const float* surface, int surface_width, const int* surface_tiles,
    const float* noise, int noise_side, float* ratio, float* normal) {
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    int r = blockIdx.y;
    if (c >= cols || r >= rows) return;
    long long p = (long long)r * cols + c;
    float weight[MAX_LAYERS];
    for (int i = 0; i < MAX_LAYERS; ++i) weight[i] = 0.0f;
    float soaked = 0.0f;
    for (int a = 0; a < 2; ++a) {
        for (int b = 0; b < 2; ++b) {
            float g = row_weight[a * rows + r] * col_weight[b * cols + c];
            long long t =
                (long long)row_index[a * rows + r] * window_cols + col_index[b * cols + c];
            for (int k = 0; k < TOP_LAYERS; ++k) {
                int id = ids[k * plane + t];
                if (id != NO_LAYER && id < MAX_LAYERS)
                    weight[id] = weight[id] + g * ((float)weights[k * plane + t] / 255.0f);
            }
            soaked = soaked + g * ((float)wet[t] / 255.0f);
        }
    }
    float total = 0.0f;
    for (int i = 0; i < count; ++i) total = total + weight[present[i]];
    float out_ratio[3] = {1.0f, 1.0f, 1.0f}, out_normal[2] = {0.0f, 0.0f};
    if (total > 0.0f || soaked > 0.0f) {
        Atlas col_atlas = {colour, colour_width, 6, colour_tiles};
        Atlas surf_atlas = {surface, surface_width, 2, surface_tiles};
        Layers layers = {tiles, cells, inverse};
        Cells here = cells_at(noise, noise_side, u[c], v[r]);
        if (total > 0.0f && count > 0)
            blend(col_atlas, surf_atlas, layers, present, count, weight, u[c], v[r], here,
                  out_ratio, out_normal);
        if (overlay_id >= 0 && soaked > 0.0f)
            overlay(col_atlas, surf_atlas, layers, overlay_id, soaked, u[c], v[r], here,
                    out_ratio, out_normal);
    }
    for (int k = 0; k < 3; ++k) ratio[p * 3 + k] = out_ratio[k];
    for (int k = 0; k < 2; ++k) normal[p * 2 + k] = out_normal[k];
}
