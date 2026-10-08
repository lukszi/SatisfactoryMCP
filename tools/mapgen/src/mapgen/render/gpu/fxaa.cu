// render/draw/archaa.py's FXAA on the GPU, a thread an output pixel: arch_fxaa's float32
// operations in fxaa's order, each pixel read inside its own column piece as numpy pads and
// clips it. Compiled with --fmad=false (mapgen.jit). docs/map/renders.md section 41, "The
// draw on the GPU".

#define REACH 12

struct Piece {
    const float* lum;
    int h, w, lo, hi;  // the rows of the band with its halo; the piece's columns [lo, hi)
};

__device__ int clampi(int v, int lo, int hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

__device__ float at(const Piece& p, int y, int x) {
    y = clampi(y, 0, p.h - 1);
    x = clampi(x, p.lo, p.hi - 1);
    return p.lum[(long long)y * p.w + x];
}

// arch_mask's binary dilation by a disk of radius r over the rows [top, top + rows): a pixel
// is kept where any covered pixel lies within the disk; past the band nothing is covered.
extern "C" __global__ void grow(const unsigned char* cover, int h, int w, int top, int rows,
                                int r, unsigned char* keep) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (x >= w || i >= rows) return;
    int y = top + i;
    unsigned char hit = 0;
    for (int dy = -r; dy <= r && !hit; ++dy) {
        int yy = y + dy;
        if (yy < 0 || yy >= h) continue;
        for (int dx = -r; dx <= r; ++dx) {
            int xx = x + dx;
            if (dx * dx + dy * dy > r * r || xx < 0 || xx >= w) continue;
            if (cover[(long long)yy * w + xx]) {
                hit = 1;
                break;
            }
        }
    }
    keep[(long long)i * w + x] = hit;
}

// Whether each column keeps a pixel in any row: column_pieces' cover.any(axis=0).
extern "C" __global__ void any_rows(const unsigned char* keep, int rows, int w,
                                    unsigned char* column) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    if (x >= w) return;
    unsigned char hit = 0;
    for (int i = 0; i < rows && !hit; ++i) hit = keep[(long long)i * w + x];
    column[x] = hit;
}

extern "C" __global__ void luma(const unsigned char* rgb, float scale, const float* weights,
                                float* lum, long long pixels) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= pixels) return;
    float r = (float)rgb[i * 3] * scale, g = (float)rgb[i * 3 + 1] * scale;
    float b = (float)rgb[i * 3 + 2] * scale;
    lum[i] = (r * weights[0] + g * weights[1]) + b * weights[2];
}

struct Edge {
    bool horizontal;
    int sign;
    float level, gradient, sub;
};

// fxaa's contrast test, edge direction and sub-pixel term at (y, x); false off an edge.
__device__ bool edge_at(const Piece& p, int y, int x, const float* knobs, Edge* e) {
    float m = at(p, y, x), n = at(p, y - 1, x), s = at(p, y + 1, x);
    float w = at(p, y, x - 1), ea = at(p, y, x + 1);
    float high = fmaxf(fmaxf(fmaxf(fmaxf(m, n), s), w), ea);
    float range = high - fminf(fminf(fminf(fminf(m, n), s), w), ea);
    if (!(range >= fmaxf(knobs[1], high * knobs[0]))) return false;
    float nw = at(p, y - 1, x - 1), ne = at(p, y - 1, x + 1);
    float sw = at(p, y + 1, x - 1), se = at(p, y + 1, x + 1);
    float average = ((((n + s) + w) + ea) * 2.0f + (((nw + ne) + sw) + se)) * knobs[3];
    float contrast = fabsf(average - m) / fmaxf(range, knobs[4]);
    contrast = fminf(fmaxf(contrast, 0.0f), 1.0f);
    float sub = (-2.0f * contrast + 3.0f) * contrast * contrast;
    e->sub = sub * sub * knobs[2];
    float across = (fabsf(-2.0f * w + nw + sw) + 2.0f * fabsf(-2.0f * m + n + s))
                   + fabsf(-2.0f * ea + ne + se);
    float along = (fabsf(-2.0f * n + nw + ne) + 2.0f * fabsf(-2.0f * m + w + ea))
                  + fabsf(-2.0f * s + sw + se);
    e->horizontal = across >= along;
    float l1 = e->horizontal ? n : w, l2 = e->horizontal ? s : ea;
    float g1 = l1 - m, g2 = l2 - m;
    bool first = fabsf(g1) >= fabsf(g2);
    e->gradient = 0.25f * fmaxf(fabsf(g1), fabsf(g2));
    e->level = 0.5f * ((first ? l1 : l2) + m);
    e->sign = first ? -1 : 1;
    return true;
}

// _search one way: how far the edge runs, and the luma where it ends.
__device__ void search(const Piece& p, int y, int x, const Edge& e, int d, float* dist,
                       float* end) {
    int ay = e.horizontal ? 0 : 1, ax = e.horizontal ? 1 : 0;
    int py = e.horizontal ? e.sign : 0, px = e.horizontal ? 0 : e.sign;
    for (int step = 1; step <= REACH; ++step) {
        int y0 = clampi(y + ay * d * step, 0, p.h - 1);
        int x0 = clampi(x + ax * d * step, p.lo, p.hi - 1);
        float value = 0.5f * (at(p, y0, x0) + at(p, y0 + py, x0 + px)) - e.level;
        *dist = (float)step;
        *end = value;
        if (fabsf(value) >= e.gradient) return;
    }
}

__device__ float blend(const Piece& p, int y, int x, const Edge& e) {
    float d0 = 0.0f, d1 = 0.0f, e0 = 0.0f, e1 = 0.0f;
    search(p, y, x, e, -1, &d0, &e0);
    search(p, y, x, e, 1, &d1, &e1);
    bool below = (at(p, y, x) - e.level) < 0.0f;
    bool good = d0 < d1 ? ((e0 < 0.0f) != below) : ((e1 < 0.0f) != below);
    float offset = good ? 0.5f - fminf(d0, d1) / (d0 + d1) : 0.0f;
    return fmaxf(offset, e.sub);
}

// knobs: threshold, threshold_min, subpix, 1/12, the range floor, 1/255.
extern "C" __global__ void arch_fxaa(
    const unsigned char* rgb, const float* lum, int h, int w, int top, int rows,
    const unsigned char* keep, const int* lo, const int* hi, const float* knobs,
    unsigned char* out) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (x >= w || i >= rows) return;
    int y = top + i;
    long long o = (long long)i * w + x, src = (long long)y * w + x;
    if (!keep[o] || lo[x] < 0) {
        for (int k = 0; k < 3; ++k) out[o * 3 + k] = rgb[src * 3 + k];
        return;
    }
    Piece p = {lum, h, w, lo[x], hi[x]};
    Edge e;
    bool edge = edge_at(p, y, x, knobs, &e);
    float f = 0.0f;
    long long other = src;
    if (edge) {
        f = blend(p, y, x, e);
        int oy = clampi(y + (e.horizontal ? e.sign : 0), 0, h - 1);
        int ox = clampi(x + (e.horizontal ? 0 : e.sign), p.lo, p.hi - 1);
        other = (long long)oy * w + ox;
    }
    for (int k = 0; k < 3; ++k) {
        float value = (float)rgb[src * 3 + k] * knobs[5];
        if (edge) value = value * (1.0f - f) + (float)rgb[other * 3 + k] * knobs[5] * f;
        float byte = fminf(fmaxf(value * 255.0f + 0.5f, 0.0f), 255.0f);
        out[o * 3 + k] = (unsigned char)byte;
    }
}
