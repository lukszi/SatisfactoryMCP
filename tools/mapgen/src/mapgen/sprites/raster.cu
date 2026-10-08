// The sprite raster's per-sample kernels on the GPU, a thread a sample: sprites/fill.py's
// top_hits and sprites/shade.py's shade. Compiled with --fmad=false, so every float operation
// is the one IEEE operation numpy does, in the same order. A fill thread walks its bin's
// triangles in index order, as the reference walks every triangle, and tests each one at the
// same integer bounds. docs/map/light-and-crowns.md section 36, "Crown sprites".

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

// shade._unit: (x, y, z) over its length, (0, 0, 1) where it has none.
__device__ void unit3(float x, float y, float z, float* o) {
    float length = sqrtf((x * x + y * y) + z * z);
    if (length > 0.0f) {
        o[0] = x / length;
        o[1] = y / length;
        o[2] = z / length;
    } else {
        o[0] = 0.0f;
        o[1] = 0.0f;
        o[2] = 1.0f;
    }
}

// shade._bilinear: three-channel texels at a UV, as mask_at reads one.
__device__ void bilinear3(const float* texels, long long offset, long long w, long long h,
                          float u, float v, int wrap, float* out) {
    float fu = u * (float)w - 0.5f;
    float fv = v * (float)h - 0.5f;
    float x0 = floorf(fu), y0 = floorf(fv);
    float fx = fu - x0, fy = fv - y0;
    float gx = 1.0f - fx, gy = 1.0f - fy;
    long long i0, i1, j0, j1;
    taps(x0, w, wrap, &i0, &i1);
    taps(y0, h, wrap, &j0, &j1);
    for (int k = 0; k < 3; ++k) {
        float a00 = texels[(offset + j0 * w + i0) * 3 + k];
        float a01 = texels[(offset + j0 * w + i1) * 3 + k];
        float a10 = texels[(offset + j1 * w + i0) * 3 + k];
        float a11 = texels[(offset + j1 * w + i1) * 3 + k];
        float top = a00 * gx + a01 * fx;
        float bottom = a10 * gx + a11 * fx;
        out[k] = top * gy + bottom * fy;
    }
}

extern "C" __global__ void shade(
    const float* setup, const int* bounds, const float* attrs, const int* tri, const float* w1,
    const float* w2, const float* best, const float* albedo, const float* normals,
    const int* ints, const float* floats, float ox, float oy, float sample, int rows, int cols,
    float* colour, float* normal) {
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    int r = blockIdx.y;
    if (c >= cols || r >= rows) return;
    long long at = (long long)r * cols + c;
    int t = tri[at];
    if (t < 0) return;  // the outputs start at zero
    float b1 = w1[at], b2 = w2[at];
    const float* s = setup + 16 * (long long)t;
    float u = (s[10] + b1 * s[11]) + b2 * s[12];
    float v = (s[13] + b1 * s[14]) + b2 * s[15];
    const int* bd = bounds + 6 * (long long)t;
    const int* in = ints + 6 * (long long)bd[4];
    const float* fl = floats + 9 * (long long)bd[4];
    int wrap = bd[5];
    const float* a = attrs + 19 * (long long)t;
    float nx = (a[0] + b1 * a[3]) + b2 * a[6];
    float ny = (a[1] + b1 * a[4]) + b2 * a[7];
    float nz = (a[2] + b1 * a[5]) + b2 * a[8];
    float tx = (a[9] + b1 * a[12]) + b2 * a[15];
    float ty = (a[10] + b1 * a[13]) + b2 * a[16];
    float tz = (a[11] + b1 * a[14]) + b2 * a[17];
    float sign = a[18];
    float bx = (ny * tz - nz * ty) * sign;
    float by = (nz * tx - nx * tz) * sign;
    float bz = (nx * ty - ny * tx) * sign;
    if (nz < 0.0f) {
        nx = -nx;
        ny = -ny;
        nz = -nz;
    }
    float m[3] = {0.0f, 0.0f, 1.0f};
    if (in[3] >= 0) bilinear3(normals, in[3], in[4], in[5], u, v, wrap, m);
    float w[3];
    unit3((m[0] * tx + m[1] * bx) + m[2] * nx, (m[0] * ty + m[1] * by) + m[2] * ny,
          (m[0] * tz + m[1] * bz) + m[2] * nz, w);
    float k = fl[5];
    if (k > 0.0f) {
        float px = ox + ((float)c + 0.5f) * sample;
        float py = oy + ((float)r + 0.5f) * sample;
        float d[3];
        unit3(px - fl[6], py - fl[7], best[at] - fl[8], d);
        unit3(w[0] + (d[0] - w[0]) * k, w[1] + (d[1] - w[1]) * k, w[2] + (d[2] - w[2]) * k, w);
    }
    if (w[2] < 0.0f) unit3(w[0], w[1], 0.0f, w);
    float base[3];
    bilinear3(albedo, in[0], in[1], in[2], u, v, wrap, base);
    float moss = fminf(fmaxf((w[2] - fl[3]) * fl[4], 0.0f), 1.0f);
    for (int j = 0; j < 3; ++j) {
        colour[3 * at + j] = base[j] + (fl[j] - base[j]) * moss;
        normal[3 * at + j] = w[j];
    }
}