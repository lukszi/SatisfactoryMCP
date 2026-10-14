// palette/painted/crown_form.py's reference on the GPU, a thread a pixel. The box sums are of
// integers, exact in any order; the rest is each float operation numpy does, in its order
// (--fmad=false). docs/map/light-and-crowns.md section 36, "Crown form".

// Each row's sums of the tops in integer steps (plane 0) and of the tops seen (plane 1) over
// the `2 r + 1` columns about each pixel, nothing past the edges.
extern "C" __global__ void across(const float* top, float per_cm, int rows, int cols, int r,
                                  long long* out, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    long long row = i / cols, col = i % cols;
    long long lo = col - r < 0 ? 0 : col - r, hi = col + r >= cols ? cols - 1 : col + r;
    const float* p = top + row * cols;
    long long sum = 0, seen = 0;
    for (long long c = lo; c <= hi; ++c) {
        float v = p[c];
        if (isnan(v)) continue;
        sum += (long long)rintf(v * per_cm);
        seen += 1;
    }
    out[i] = sum;
    out[n + i] = seen;
}

// Those sums down the `2 r + 1` rows about each pixel: the box's mean top in cm, NaN for none.
extern "C" __global__ void mean(const long long* across, int rows, int cols, int r,
                                double per_cm, float* out, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    long long row = i / cols, col = i % cols;
    long long lo = row - r < 0 ? 0 : row - r, hi = row + r >= rows ? rows - 1 : row + r;
    long long sum = 0, seen = 0;
    for (long long k = lo; k <= hi; ++k) {
        sum += across[k * cols + col];
        seen += across[n + k * cols + col];
    }
    out[i] = seen > 0 ? (float)((double)sum / ((double)seen * per_cm)) : nanf("");
}

// The mean's slope in cm a pixel between the means `d` either side, a missing side the
// pixel's own; 0 where the pixel has no mean.
__device__ float slope(const float* mean, long long at, long long pos, long long len,
                       long long stride, int d) {
    float own = mean[at];
    float ahead = pos + d < len ? mean[at + d * stride] : nanf("");
    float behind = pos - d >= 0 ? mean[at - d * stride] : nanf("");
    bool has_ahead = !isnan(ahead), has_behind = !isnan(behind);
    float hi = has_ahead ? ahead : own, lo = has_behind ? behind : own;
    long long sides = (long long)has_ahead + (long long)has_behind;
    if (sides == 0 || isnan(own)) return 0.0f;
    return (hi - lo) / (float)(sides * d);
}

// The form factor: the slope raised by `relief` and lit against flat ground; 1 with no top.
extern "C" __global__ void form(const float* top, const float* mean, int rows, int cols, int d,
                                float step_cm, float relief, float lx, float ly, float lz,
                                float* out, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    if (isnan(top[i])) {
        out[i] = 1.0f;
        return;
    }
    long long row = i / cols, col = i % cols;
    float gx = slope(mean, i, col, cols, 1, d) / step_cm * relief;
    float gy = slope(mean, i, row, rows, cols, d) / step_cm * relief;
    float lit = -gx * lx - gy * ly + lz;
    lit = lit > 0.0f ? lit : 0.0f;
    out[i] = lit / sqrtf(gx * gx + gy * gy + 1.0f) / lz;
}
