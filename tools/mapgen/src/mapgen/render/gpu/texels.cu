// terrain/texels.py on the GPU, a thread a pixel: a texture or an atlas tile read bilinear,
// and sprites or crowns stamped over a band in their order, each pixel walking the sprites of
// its cell.
// The float32 operations of the reference in its order; compiled with --fmad=false
// (mapgen.jit). docs/map/renders.md section 41, "The draw on the GPU".

struct Corners {
    long long a, b;
    float f;
};

__device__ long long wrapped(long long i, long long n) {
    long long m = i % n;
    return m < 0 ? m + n : m;
}

__device__ long long clamped(long long i, long long n) {
    return i < 0 ? 0 : (i > n - 1 ? n - 1 : i);
}

__device__ Corners corners(float position, long long size, int wrap) {
    float at = position - 0.5f;
    float first = floorf(at);
    Corners c;
    c.f = at - first;
    long long low = (long long)first;
    c.a = wrap ? wrapped(low, size) : clamped(low, size);
    c.b = wrap ? wrapped(low + 1, size) : clamped(low + 1, size);
    return c;
}

// One channel k of texels (rows of `width`, `channels` deep) between four texels.
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

extern "C" __global__ void sample_texture(const float* texture, int h, int w, int channels,
                                          const float* u, const float* v, long long n, int wrap,
                                          float* out) {
    long long p = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (p >= n) return;
    Corners r = corners(v[p], h, wrap), c = corners(u[p], w, wrap);
    for (int k = 0; k < channels; ++k)
        out[p * channels + k] = bilinear(texture, w, channels, k, r.a, r.b, r.f, c.a, c.b, c.f);
}

// tiles: per tile its first column, first row, width and height in the atlas.
__device__ void atlas_texel(const float* atlas, int width, int channels, const int* tiles,
                            int tile, float u, float v, int wrap, float* out) {
    const int* t = tiles + tile * 4;
    Corners r = corners(v, t[3], wrap), c = corners(u, t[2], wrap);
    for (int k = 0; k < channels; ++k)
        out[k] = bilinear(atlas, width, channels, k, t[1] + r.a, t[1] + r.b, r.f, t[0] + c.a,
                          t[0] + c.b, c.f);
}

extern "C" __global__ void sample_atlas(const float* atlas, int width, int channels,
                                        const int* tiles, const int* tile, const float* u,
                                        const float* v, long long n, int wrap, float* out) {
    long long p = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (p >= n) return;
    atlas_texel(atlas, width, channels, tiles, tile[p], u[p], v[p], wrap, out + p * channels);
}

#define MAX_CHANNELS 8

// sprites: x, y, half, cos, sin, opacity, each a float per sprite; boxes: r0, r1, c0, c1.
// starts/ids: each cell's sprites in their order (cells of `cell` pixels, `cells_x` a row).
extern "C" __global__ void stamp(float* colour, float* cover, int rows, int cols,
                                 const float* sprites, long long count, const int* tile,
                                 const long long* boxes, const int* starts, const int* ids,
                                 int cell, int cells_x, const float* atlas, int width,
                                 int channels, const int* tiles) {
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    int r = blockIdx.y;
    if (c >= cols || r >= rows) return;
    int at = (r / cell) * cells_x + c / cell;
    long long p = (long long)r * cols + c;
    float texel[MAX_CHANNELS];
    for (int i = starts[at]; i < starts[at + 1]; ++i) {
        int k = ids[i];
        const long long* box = boxes + (long long)k * 4;
        if (r < box[0] || r >= box[1] || c < box[2] || c >= box[3]) continue;
        const int* t = tiles + tile[k] * 4;
        float x = sprites[k], y = sprites[count + k], half = sprites[2 * count + k];
        float cs = sprites[3 * count + k], sn = sprites[4 * count + k];
        float dx = ((float)c + 0.5f) - x, dy = ((float)r + 0.5f) - y;
        float lx = dx * cs + dy * sn, ly = dy * cs - dx * sn;
        float u = (lx / half + 1.0f) * (0.5f * (float)t[2]);
        float v = (ly / half + 1.0f) * (0.5f * (float)t[3]);
        if (!(u >= 0.0f && u < (float)t[2] && v >= 0.0f && v < (float)t[3])) continue;
        atlas_texel(atlas, width, channels, tiles, tile[k], u, v, 0, texel);
        float alpha = texel[channels - 1] * sprites[5 * count + k];
        for (int j = 0; j < channels - 1; ++j) {
            long long o = p * (channels - 1) + j;
            colour[o] = colour[o] * (1.0f - alpha) + texel[j] * alpha;
        }
        cover[p] = cover[p] + alpha * (1.0f - cover[p]);
    }
}

#define CROWN_CHANNELS 8

// terrain/crown_stamp.py's crowns, a thread a pixel walking the crowns of its cell in their
// order. Per crown: centre (x, y) in world cm; pose: cos, sin, the tile's corner (x, y) and
// texel in scaled cm, rise, z, opacity; boxes: r0, r1, c0, c1. A tile's texels are colour,
// normal and top, each times alpha, then alpha. Outputs start empty and top at -inf.
extern "C" __global__ void stamp_crowns(float* cover, float* rgb, float* normal, float* top,
                                        int rows, int cols, const double* xs,
                                        const double* ys, const double* centre,
                                        const float* pose, const int* tile,
                                        const long long* boxes, const int* starts,
                                        const int* ids, int cell, int cells_x,
                                        const float* atlas, int width, const int* tiles,
                                        float seen_from) {
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    int r = blockIdx.y;
    if (c >= cols || r >= rows) return;
    int at = (r / cell) * cells_x + c / cell;
    float a_sum = 0.0f, r0 = 0.0f, r1 = 0.0f, r2 = 0.0f, n0 = 0.0f, n1 = 0.0f, n2 = 0.0f;
    float high = __int_as_float(0xff800000);  // -inf
    float t[CROWN_CHANNELS];
    for (int i = starts[at]; i < starts[at + 1]; ++i) {
        int k = ids[i];
        const long long* box = boxes + (long long)k * 4;
        if (r < box[0] || r >= box[1] || c < box[2] || c >= box[3]) continue;
        const float* q = pose + (long long)k * 8;
        float dx = (float)(xs[c] - centre[2 * k]);
        float dy = (float)(ys[r] - centre[2 * k + 1]);
        float u = (q[0] * dx + q[1] * dy - q[2]) / q[4];
        float v = (q[0] * dy - q[1] * dx - q[3]) / q[4];
        atlas_texel(atlas, width, CROWN_CHANNELS, tiles, tile[k], u, v, 0, t);
        float a = t[7] * q[7];
        a = a < 0.0f ? 0.0f : (a > 1.0f ? 1.0f : a);
        float keep = 1.0f - a;
        a_sum = a + a_sum * keep;
        r0 = t[0] * q[7] + r0 * keep;
        r1 = t[1] * q[7] + r1 * keep;
        r2 = t[2] * q[7] + r2 * keep;
        float nx = q[0] * t[3] - q[1] * t[4];
        float ny = q[1] * t[3] + q[0] * t[4];
        n0 = nx * q[7] + n0 * keep;
        n1 = ny * q[7] + n1 * keep;
        n2 = t[5] * q[7] + n2 * keep;
        if (a >= seen_from) {
            float height = q[6] + t[6] / t[7] * q[5];
            if (height > high) high = height;
        }
    }
    long long p = (long long)r * cols + c;
    cover[p] = a_sum;
    rgb[p * 3] = r0;
    rgb[p * 3 + 1] = r1;
    rgb[p * 3 + 2] = r2;
    normal[p * 3] = n0;
    normal[p * 3 + 1] = n1;
    normal[p * 3 + 2] = n2;
    top[p] = high;
}
