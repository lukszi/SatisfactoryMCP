// terrain/texels.py on the GPU, a thread a pixel: a texture or an atlas tile read bilinear,
// and sprites stamped over a band in their order, each pixel walking the sprites of its cell.
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
