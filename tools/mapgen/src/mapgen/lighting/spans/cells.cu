// A block's atlas cells on the GPU, a thread a pixel: march._finish without its arctangent,
// holes.fill_holes, bake.path_horizon and where it folds a band, each numpy operation the
// float32 one numpy does, in its order (--fmad=false). np.maximum, np.minimum and np.clip as
// their scalar loops have them. docs/map/renders.md section 41, "On the GPU".

__device__ float np_maximum(float a, float b) { return (a >= b || isnan(a)) ? a : b; }

__device__ float np_minimum(float a, float b) { return (a <= b || isnan(a)) ? a : b; }

__device__ float np_clip(float x, float lo, float hi) {
    float m = isnan(x) ? x : (x > lo ? x : lo);
    return isnan(m) ? m : (m < hi ? m : hi);
}

// march._finish before its degrees: a band reaching down to the horizon joins it in `best`;
// `floating` marks the bands that stay.
extern "C" __global__ void finish(float* best, const float* lo, const float* hi,
                                  unsigned char* floating, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    float b = best[i], low = lo[i];
    bool banded = isfinite(low);
    bool late = banded && low <= b;
    best[i] = np_maximum(b, late ? hi[i] : b);
    floating[i] = banded && !late;
}

// holes.fill_holes of a direction's bands: `nearest` is the flat index of each pixel's
// nearest with a height.
extern "C" __global__ void fill_bands(const float* hz, const float* lo, const float* hi,
                                      const bool* seen, const long long* nearest, float* f_hz,
                                      float* f_lo, float* f_hi, bool* f_seen, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    long long at = nearest[i];
    f_hz[i] = hz[at];
    f_lo[i] = lo[at];
    f_hi[i] = hi[at];
    f_seen[i] = seen[at];
}

// bake.path_horizon at the sun path's elevation `el`, with `floor_el` and `ceil_el` the sun
// disc's edges there and `soft` its width: band_cover's one band, NaN taken as none.
extern "C" __global__ void path(const float* hz, const float* lo, const float* hi, float el,
                                float floor_el, float ceil_el, float soft, float* cell,
                                long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    float h = hz[i];
    float bottom = np_maximum(h, floor_el);
    float span = np_minimum(hi[i], ceil_el) - np_maximum(lo[i], bottom);
    float part = np_clip(span, 0.0f, soft);
    float cover = (0.0f + (isnan(part) ? 0.0f : part)) / soft;
    float folded = el + soft * (cover - 0.5f);
    cell[i] = cover > 0.0f ? np_maximum(h, folded) : h;
}

// holes.fill_holes of one plane.
extern "C" __global__ void fill(const float* plane, const long long* nearest, float* out,
                                long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    out[i] = plane[nearest[i]];
}

// Where a cell is the band folded in: it stands above the horizon it was cut from.
extern "C" __global__ void band_in(const float* cell, const float* hz, bool* out, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    out[i] = cell[i] > hz[i];
}
