// reference.py on the GPU, a thread a rock pixel: on the ground plane, the body's albedo with
// its rotated cell, the top layer's albedo and the normal maps laid on the surface. The float32 and float64
// operations of the reference in its order; compiled with --fmad=false (mapgen.jit).
// docs/map/painted.md section 30, "Rock textures".

#define KIND_LAYER 2
#define KIND_ARCH 3
#define KIND_DESERT 4
#define ALBEDO_CLIFF 0
#define ALBEDO_LAYER_BASE 1
#define ALBEDO_ARCH 2
#define ALBEDO_DESERT 3
#define NORMAL_CLIFF 0
#define NORMAL_DETAIL 1
#define NORMAL_ARCH 2
#define NORMAL_DESERT 3

struct Corners {
    long long a, b;
    float f;
};

struct Atlas {
    const float* texels;
    int width, channels;
    const int* tiles;
    const double* spans;
};

__device__ long long wrapped_index(long long i, long long n) {
    long long m = i % n;
    return m < 0 ? m + n : m;
}

__device__ Corners corners(float position, long long size) {
    float at = position - 0.5f;
    float first = floorf(at);
    Corners c;
    c.f = at - first;
    long long low = (long long)first;
    c.a = wrapped_index(low, size);
    c.b = wrapped_index(low + 1, size);
    return c;
}

__device__ float bilinear(const float* texels, long long width, int channels, int k, Corners r,
                          long long y0, Corners c, long long x0) {
    float gx = 1.0f - c.f, gy = 1.0f - r.f;
    float a = texels[((y0 + r.a) * width + x0 + c.a) * channels + k];
    float b = texels[((y0 + r.a) * width + x0 + c.b) * channels + k];
    float cc = texels[((y0 + r.b) * width + x0 + c.a) * channels + k];
    float d = texels[((y0 + r.b) * width + x0 + c.b) * channels + k];
    float top = a * gx + b * c.f;
    float bottom = cc * gx + d * c.f;
    return top * gy + bottom * r.f;
}

// A position in texels brought into [0, side).
__device__ float wrapped(double texel, double side) {
    return (float)(texel - floor(texel / side) * side);
}

__device__ void texel(Atlas at, int tile, float u, float v, float* out) {
    const int* t = at.tiles + tile * 4;
    Corners r = corners(v, t[3]), c = corners(u, t[2]);
    for (int k = 0; k < at.channels; ++k)
        out[k] = bilinear(at.texels, at.width, at.channels, k, r, t[1], c, t[0]);
}

__device__ void plain(Atlas at, int tile, double a, double b, float* out) {
    double side = (double)at.tiles[tile * 4 + 2], span = at.spans[tile];
    texel(at, tile, wrapped(a / span * side, side), wrapped(b / span * side, side), out);
}

struct Cell {
    float weight;
    double offset_a, offset_b, scale, cos, sin;
};

__device__ Cell cell(const float* cells, int side_px, double cell_span, const double* turn,
                     double a, double b) {
    double side = (double)side_px;
    float u = wrapped(a / cell_span * side, side), v = wrapped(b / cell_span * side, side);
    long long col = wrapped_index((long long)floorf(u), side_px);
    long long row = wrapped_index((long long)floorf(v), side_px);
    const float* here = cells + (row * side_px + col) * 4;
    float red = here[0], green = here[1], blue = here[2];
    Corners r = corners(v, side_px), c = corners(u, side_px);
    float alpha = bilinear(cells, side_px, 4, 3, r, 0, c, 0);
    Cell out;
    out.weight = fminf(fmaxf(alpha * 2.0f - 0.5f, 0.0f), 1.0f);
    float index = fminf(fmaxf(floorf(blue * 255.0f + 0.5f), 0.0f), 255.0f);
    long long k = (long long)index;
    out.offset_a = (double)red * 2.0 - 1.0;
    out.offset_b = (double)green * 2.0 - 1.0;
    out.scale = 0.9 + 0.2 * (double)green;
    out.cos = turn[2 * k];
    out.sin = turn[2 * k + 1];
    return out;
}

__device__ void albedo(Atlas at, int tile, double a, double b, Cell cl, float* out) {
    float base[3], turned[3];
    plain(at, tile, a, b, base);
    double side = (double)at.tiles[tile * 4 + 2], span = at.spans[tile];
    double ua = (a / span + cl.offset_a) * cl.scale;
    double vb = (b / span + cl.offset_b) * cl.scale;
    float ru = wrapped((cl.cos * ua - cl.sin * vb) * side, side);
    float rv = wrapped((cl.sin * ua + cl.cos * vb) * side, side);
    texel(at, tile, ru, rv, turned);
    for (int k = 0; k < 3; ++k) out[k] = base[k] + cl.weight * (turned[k] - base[k]);
}

// The base normal map with the detail one laid on it (reoriented), unit length.
__device__ void mapped(Atlas at, int kind, double a, double b, float* r) {
    float bxy[2], dxy[2] = {0.0f, 0.0f};
    bool own = kind == KIND_ARCH || kind == KIND_DESERT;
    int base = kind == KIND_ARCH ? NORMAL_ARCH : NORMAL_CLIFF;
    if (kind == KIND_DESERT) base = NORMAL_DESERT;
    plain(at, base, a, b, bxy);
    if (!own) plain(at, NORMAL_DETAIL, a, b, dxy);
    float bx = bxy[0], by = bxy[1], dx = dxy[0], dy = dxy[1];
    float bz = sqrtf(fmaxf(1.0f - bx * bx - by * by, 0.0f));
    float dz = sqrtf(fmaxf(1.0f - dx * dx - dy * dy, 0.0f));
    float t[3] = {bx * 2.0f, by * 2.0f, bz + 1.0f};
    float w[3] = {dx * -0.5f, dy * -0.5f, dz};
    float dot = t[0] * w[0] + t[1] * w[1] + t[2] * w[2];
    for (int k = 0; k < 3; ++k) r[k] = t[k] * dot - w[k] * t[2];
    float length = sqrtf(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]);
    for (int k = 0; k < 3; ++k) r[k] = r[k] / length;
}

extern "C" __global__ void look_texels(
    const double* x, const double* y, const float* normal,
    const unsigned char* kind, const int* top, long long n, const float* albedo_texels,
    int albedo_width, const int* albedo_tiles, const double* albedo_spans,
    const float* albedo_median, const float* normal_texels, int normal_width,
    const int* normal_tiles, const double* normal_spans, const float* cells, int cell_side,
    double cell_span, const double* turn, float* body_out, float* top_out, float* normal_out) {
    long long p = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (p >= n) return;
    Atlas col = {albedo_texels, albedo_width, 3, albedo_tiles, albedo_spans};
    Atlas nor = {normal_texels, normal_width, 2, normal_tiles, normal_spans};
    const float* nrm = normal + p * 3;
    int k = kind[p];
    double a = x[p], b = y[p];
    bool own = k == KIND_ARCH || k == KIND_DESERT;
    Cell cl = cell(cells, cell_side, cell_span, turn, a, b);
    if (own) cl.weight = 0.0f;
    int tile = k == KIND_ARCH ? ALBEDO_ARCH : ALBEDO_CLIFF;
    if (k == KIND_DESERT) tile = ALBEDO_DESERT;
    float fine[3], under[3];
    albedo(col, tile, a, b, cl, fine);
    if (k == KIND_LAYER) albedo(col, ALBEDO_LAYER_BASE, a, b, cl, under);
    else for (int c = 0; c < 3; ++c) under[c] = albedo_median[tile * 3 + c];
    for (int c = 0; c < 3; ++c) body_out[p * 3 + c] = fine[c] / under[c];
    if (top[p] >= 0) {
        float layer[3];
        plain(col, top[p], a, b, layer);
        for (int c = 0; c < 3; ++c) top_out[p * 3 + c] = layer[c] / albedo_median[top[p] * 3 + c];
    } else {
        for (int c = 0; c < 3; ++c) top_out[p * 3 + c] = 1.0f;
    }
    float r[3], out[3];
    mapped(nor, k, a, b, r);
    out[0] = r[0] + r[2] * nrm[0];
    out[1] = r[1] + r[2] * nrm[1];
    out[2] = r[2] * nrm[2];
    float length = sqrtf(out[0] * out[0] + out[1] * out[1] + out[2] * out[2]);
    for (int c = 0; c < 3; ++c) normal_out[p * 3 + c] = out[c] / length;
}
