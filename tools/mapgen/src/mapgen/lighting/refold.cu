// lighting/refold.py's reference on the GPU: a thread a coarse texel and direction.
// Compiled with --fmad=false (mapgen.jit), so each operation rounds as numpy's does. The third
// group is the canopy's: 0 holds none and reads as the ground's, a stored texel is at least
// `floor`.

__device__ float shade(float h, float el, float soft) {
    return fminf(fmaxf((h - el) / soft + 0.5f, 0.0f), 1.0f);
}

// Four texels' horizon as a coarser texel stores it; `s` takes their mean shade.
__device__ float mean_shade(float a, float b, float c, float d, float el, float soft, float* s) {
    *s = ((shade(a, el, soft) + shade(b, el, soft)) + (shade(c, el, soft) + shade(d, el, soft)))
         * 0.25f;
    float mean = ((a + b) + (c + d)) * 0.25f;
    return (*s > 0.0f && *s < 1.0f) ? el + soft * (*s - 0.5f) : mean;
}

__device__ float larger(float a, float b) { return a > b ? a : b; }

// The canopy group's texel: the refold of its four, each the ground's where it holds none.
__device__ float canopy(const float* p, const float* q, int cells, int m, int k, float el,
                        float soft, float floor) {
    float t[4] = {p[m], p[cells + m], q[m], q[cells + m]};
    float g[4] = {p[k], p[cells + k], q[k], q[cells + k]};
    bool any = false;
    for (int n = 0; n < 4; ++n) {
        any = any || t[n] > 0.0f;
        t[n] = t[n] > 0.0f ? t[n] : g[n];
    }
    float unused;
    float stored = mean_shade(t[0], t[1], t[2], t[3], el, soft, &unused);
    return any ? (stored > floor ? stored : floor) : 0.0f;
}

extern "C" __global__ void refold(
    const float* fine, const float* el, float soft, float floor, int dirs, int cells,
    float* out, int rows, int cols) {
    long long t = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (t >= (long long)cols * dirs || i >= rows) return;
    long long j = t / dirs;
    int k = (int)(t % dirs);
    long long width = 2LL * cols * cells;
    const float* p = fine + 2LL * i * width + 2LL * j * cells;
    const float* q = p + width;
    float a = p[k], b = p[cells + k], c = q[k], d = q[cells + k];
    float shaded, raised, unused;
    float ground = mean_shade(a, b, c, d, el[k], soft, &shaded);
    float* o = out + ((long long)i * cols + j) * cells;
    o[k] = ground;
    for (int m = dirs + k; m < cells; m += dirs) {
        if (m >= 2 * dirs) {
            o[m] = canopy(p, q, cells, m, k, el[k], soft, floor);
            continue;
        }
        float own = mean_shade(p[m], p[cells + m], q[m], q[cells + m], el[k], soft, &unused);
        float over = mean_shade(larger(p[m], a), larger(p[cells + m], b), larger(q[m], c),
                                larger(q[cells + m], d), el[k], soft, &raised);
        o[m] = raised > shaded ? over : own;
    }
}
