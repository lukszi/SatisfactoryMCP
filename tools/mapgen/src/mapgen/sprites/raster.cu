// The sprite raster's per-sample fill on the GPU: sprites/fill.py's top_hits, a thread a sample.
// Compiled with --fmad=false, so every float operation is the one IEEE operation numpy does,
// in the same order. A thread walks its bin's triangles in index order, as the reference walks
// every triangle, and tests each one at the same integer bounds.
// docs/map/light-and-crowns.md section 36, "Crown sprites".

// fill.taps: the two texels along one axis, wrapped round n or held at the edges.
__device__ void taps(float first, long long n, int wrap, long long* low, long long* high) {
    long long i = (long long)first;
    if (wrap) {
        *low = (i % n + n) % n;
        *high = ((i + 1) % n + n) % n;
    } else {
        *low = i < 0 ? 0 : (i > n - 1 ? n - 1 : i);
        *high = i + 1 < 0 ? 0 : (i + 1 > n - 1 ? n - 1 : i + 1);
    }
}

__device__ float mask_at(const unsigned char* texels, const int* row, float u, float v,
                         int wrap) {
    long long offset = row[0];
    long long w = row[1], h = row[2];
    float fu = u * (float)w - 0.5f;
    float fv = v * (float)h - 0.5f;
    float x0 = floorf(fu), y0 = floorf(fv);
    float fx = fu - x0, fy = fv - y0;
    float gx = 1.0f - fx, gy = 1.0f - fy;
    long long i0, i1, j0, j1;
    taps(x0, w, wrap, &i0, &i1);
    taps(y0, h, wrap, &j0, &j1);
    float a00 = (float)texels[offset + j0 * w + i0];
    float a01 = (float)texels[offset + j0 * w + i1];
    float a10 = (float)texels[offset + j1 * w + i0];
    float a11 = (float)texels[offset + j1 * w + i1];
    float top = a00 * gx + a01 * fx;
    float bottom = a10 * gx + a11 * fx;
    return top * gy + bottom * fy;
}

extern "C" __global__ void top_hits(
    const float* setup, const int* bounds, const int* bin_start, const int* bin_tris,
    int bins_across, int bin, const unsigned char* texels, const int* table, float cut,
    int rows, int cols, float* best, int* which, float* w1, float* w2) {
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    int r = blockIdx.y;
    if (c >= cols || r >= rows) return;
    int b = (r / bin) * bins_across + c / bin;
    float px = (float)c + 0.5f, py = (float)r + 0.5f;
    float top = __int_as_float(0xff800000), k1 = 0.0f, k2 = 0.0f;  // -inf: NVRTC has no INFINITY
    int win = -1;
    for (int e = bin_start[b]; e < bin_start[b + 1]; ++e) {
        int t = bin_tris[e];
        const int* bd = bounds + 6 * (long long)t;
        if (c < bd[0] || c > bd[1] || r < bd[2] || r > bd[3]) continue;
        const float* s = setup + 16 * (long long)t;
        float dx = px - s[0], dy = py - s[1];
        float b1 = (dx * s[5] - dy * s[4]) * s[6];
        float b2 = (dy * s[2] - dx * s[3]) * s[6];
        if (!(b1 >= 0.0f && b2 >= 0.0f && b1 + b2 <= 1.0f)) continue;
        float z = (s[7] + b1 * s[8]) + b2 * s[9];
        if (!(z > top)) continue;
        const int* row = table + 4 * (long long)bd[4];
        if (row[3]) {
            float u = (s[10] + b1 * s[11]) + b2 * s[12];
            float v = (s[13] + b1 * s[14]) + b2 * s[15];
            if (!(mask_at(texels, row, u, v, bd[5]) >= cut)) continue;
        }
        top = z;
        win = t;
        k1 = b1;
        k2 = b2;
    }
    long long at = (long long)r * cols + c;
    best[at] = top;
    which[at] = win;
    w1[at] = k1;
    w2[at] = k2;
}
