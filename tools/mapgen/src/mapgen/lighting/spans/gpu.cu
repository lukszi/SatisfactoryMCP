// The span march and the sky view with spans on the GPU: lighting/spans/kernels.py, a thread
// a pixel. Compiled with --fmad=false, so every float operation is the one IEEE operation
// numba's kernel does, in the same order. A thread visits a span sample where the sample's
// four pixels hold a span, which is where spans.SpanRuns sends numba's. Python's min and max
// keep the first of equals, as numba's do. docs/map/renders.md section 41, "On the GPU".

__device__ float infinity() { return __int_as_float(0x7f800000); }

__device__ float raise_to(float top, float rise) {
    float larger = !(rise <= top) ? rise : top;
    return isnan(top) ? top : larger;
}

__device__ float sample(const float* z, long long w, long long r, long long c, bool smooth,
                        float fy, float fx, float gy, float gx) {
    const float* a = z + r * w + c;
    if (!smooth) return a[0];
    const float* d = a + w;
    return (a[0] * gx + a[1] * fx) * gy + (d[0] * gx + d[1] * fx) * fy;
}

__device__ bool holds_span(const float* lo, long long w, long long r, long long c) {
    const float* a = lo + r * w + c;
    return isfinite(a[0]) || isfinite(a[1]) || isfinite(a[w]) || isfinite(a[w + 1]);
}

__device__ void higher(float& low, float& high, const float* lo, const float* hi, long long at) {
    float top = hi[at];
    if (top > high || (isnan(high) && !isnan(top))) {
        low = lo[at];
        high = top;
    }
}

__device__ void reach_down(float& low, float high, float under, float top) {
    if (under <= high && top >= low && under < low) low = under;
}

__device__ void quad(const float* lo, const float* hi, long long w, long long r, long long c,
                     float& low, float& high) {
    long long at[4] = {r * w + c, r * w + c + 1, (r + 1) * w + c, (r + 1) * w + c + 1};
    low = lo[at[0]];
    high = hi[at[0]];
    for (int k = 1; k < 4; ++k) higher(low, high, lo, hi, at[k]);
    for (int k = 0; k < 4; ++k) reach_down(low, high, lo[at[k]], hi[at[k]]);
}

__device__ void tangents(float low, float high, float near, float near_m, float far_m,
                         float& tl, float& th) {
    float dl = low - near, dh = high - near;
    tl = dl > 0 ? dl / far_m : dl / near_m;
    th = dh > 0 ? dh / near_m : dh / far_m;
}

__device__ float distance_to(float lo, float hi, float t) {
    if (t < lo) return lo - t;
    if (t > hi) return t - hi;
    return 0.0f;
}

__device__ void into_band(float tl, float th, float weight, float te, float gap, float& lo,
                          float& hi) {
    float sl = tl * weight, sh = th * weight;
    bool have = isfinite(lo);
    if (have && sl <= hi + gap && sh >= lo - gap) {
        lo = sl < lo ? sl : lo;
        hi = sh > hi ? sh : hi;
    } else if (!have || distance_to(sl, sh, te) < distance_to(lo, hi, te)) {
        lo = sl;
        hi = sh;
    }
}

// The planes are z, solid, lo, hi, tops, each `w` wide; per step its offsets, its fractions
// and, from `per_step`, its scale, stretch and weight; `near_spans` per output row.
extern "C" __global__ void march_spans(
    const float* z, const float* solid, const float* lo_p, const float* hi_p,
    const float* tops, long long w, int halo, const unsigned char* near_spans,
    const bool* smooth, const int* iy, const int* ix, const int* qy, const int* qx,
    const float* fy, const float* fx, const float* gy, const float* gx, const float* per_step,
    int steps, float te, float gap, float* best, float* band_lo, float* band_hi, bool* seen,
    int rows, int cols) {
    int j = blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (j >= cols || i >= rows) return;
    long long r = halo + i;
    float near = z[r * w + halo + j];
    float top = 0.0f, lo = infinity(), hi = -infinity();
    bool visited = false, spans = near_spans[i] != 0;
    for (int s = 0; s < steps; ++s) {
        const float* k = per_step + 4 * s;
        long long sr = r + iy[s], sc = (long long)halo + ix[s] + j;
        float at = sample(solid, w, sr, sc, smooth[s], fy[s], fx[s], gy[s], gx[s]);
        top = raise_to(top, (at - near) * k[0]);
        if (!spans) continue;
        long long qr = r + qy[s], qc = (long long)halo + qx[s] + j;
        if (!holds_span(lo_p, w, qr, qc)) continue;
        float low, high, tl, th;
        quad(lo_p, hi_p, w, qr, qc, low, high);
        tangents(low, high, near, k[1], k[2], tl, th);
        visited = true;
        if (tl * k[3] <= top) {
            float exact = sample(tops, w, sr, sc, smooth[s], fy[s], fx[s], gy[s], gx[s]);
            top = raise_to(top, (exact - near) * k[0]);
        } else {
            into_band(tl, th, k[3], te, gap, lo, hi);
        }
    }
    long long out = (long long)i * cols + j;
    best[out] = top;
    band_lo[out] = lo;
    band_hi[out] = hi;
    seen[out] = visited;
}

// The steps are (dirs, steps) as the sky view's; `per_step` holds a step's scale and stretch.
extern "C" __global__ void sky_view_spans(
    const float* z, const float* solid, const float* lo_p, const float* hi_p,
    const float* tops, long long w, int halo, const unsigned char* near_spans, const int* iy,
    const int* ix, const int* qy, const int* qx, const float* fy, const float* fx,
    const float* gy, const float* gx, const float* per_step, int dirs, int steps, float* out,
    int rows, int cols) {
    int j = blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (j >= cols || i >= rows) return;
    long long r = halo + i;
    float near = z[r * w + halo + j];
    bool spans = near_spans[i] != 0;
    float acc = 0.0f;
    for (int d = 0; d < dirs; ++d) {
        float best = 0.0f, lo = infinity(), hi = -infinity();
        for (int s = 0; s < steps; ++s) {
            int at = d * steps + s;
            const float* k = per_step + 3 * s;
            long long sr = r + iy[at], sc = (long long)halo + ix[at] + j;
            float ground = sample(solid, w, sr, sc, true, fy[at], fx[at], gy[at], gx[at]);
            best = raise_to(best, (ground - near) * k[0]);
            if (!spans) continue;
            long long qr = r + qy[at], qc = (long long)halo + qx[at] + j;
            if (!holds_span(lo_p, w, qr, qc)) continue;
            float low, high, tl, th;
            quad(lo_p, hi_p, w, qr, qc, low, high);
            tangents(low, high, near, k[1], k[2], tl, th);
            if (tl <= best) {
                float exact = sample(tops, w, sr, sc, true, fy[at], fx[at], gy[at], gx[at]);
                best = raise_to(best, (exact - near) * k[0]);
            } else {
                lo = tl < lo ? tl : lo;
                hi = th > hi ? th : hi;
            }
        }
        bool banded = isfinite(lo);
        bool late = banded && lo <= best;
        if (late) best = raise_to(best, hi);
        acc += best / sqrtf(1.0f + best * best);
        if (banded && !late) acc += hi / sqrtf(1.0f + hi * hi) - lo / sqrtf(1.0f + lo * lo);
    }
    out[(long long)i * cols + j] = 1.0f - acc / (float)dirs;
}
