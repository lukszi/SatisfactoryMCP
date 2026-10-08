// lighting/refold.py's reference on the GPU: a thread a coarse texel and direction.
// Compiled with --fmad=false (mapgen.jit), so each operation rounds as numpy's does.

__device__ float shade(float h, float el, float soft) {
    return fminf(fmaxf((h - el) / soft + 0.5f, 0.0f), 1.0f);
}

__device__ float mean_shade(float a, float b, float c, float d, float el, float soft) {
    float s = ((shade(a, el, soft) + shade(b, el, soft)) + (shade(c, el, soft) + shade(d, el, soft)))
              * 0.25f;
    float mean = ((a + b) + (c + d)) * 0.25f;
    return (s > 0.0f && s < 1.0f) ? el + soft * (s - 0.5f) : mean;
}

__device__ float larger(float a, float b) { return a > b ? a : b; }

extern "C" __global__ void refold(
    const float* fine, const float* el, float soft, int dirs, int cells,
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
    float ground = mean_shade(a, b, c, d, el[k], soft);
    float* o = out + ((long long)i * cols + j) * cells;
    o[k] = ground;
    if (cells > dirs) {
        int m = dirs + k;
        float over = mean_shade(larger(p[m], a), larger(p[cells + m], b), larger(q[m], c),
                                larger(q[cells + m], d), el[k], soft);
        o[m] = over > ground ? over : 0.0f;
    }
}
