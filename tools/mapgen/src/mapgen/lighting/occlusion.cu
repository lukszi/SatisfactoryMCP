// lighting/occlusion.py's reference on the GPU, a thread a value. The box sums are of integers,
// exact in any order; the rest is each float operation numpy does, in its order
// (--fmad=false). docs/map/light-and-crowns.md section 29, "Ambient occlusion".

// The plane in integer steps, a pixel with no height at `open`.
extern "C" __global__ void steps(const float* plane, float open, float per_m, long long* out,
                                 long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    float v = plane[i];
    out[i] = (long long)rintf((isnan(v) ? open : v) * per_m);
}

// Each row's sums along the `2 r + 1` columns about each core column: `out` is rows x cores.
extern "C" __global__ void across(const long long* steps, int rows, int cols, int m, int r,
                                  long long* out, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    long long w = cols - 2 * m;
    long long row = i / w, col = i % w + m;
    const long long* p = steps + row * cols;
    long long sum = 0;
    for (long long c = col - r; c <= col + r; ++c) sum += p[c];
    out[i] = sum;
}

// Those sums down the `2 r + 1` rows about each core row: the box's sum, core by core.
extern "C" __global__ void down(const long long* across, int rows, int cols, int m, int r,
                                long long* out, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    long long w = cols - 2 * m;
    long long row = i / w + m, col = i % w;
    long long sum = 0;
    for (long long k = row - r; k <= row + r; ++k) sum += across[k * w + col];
    out[i] = sum;
}

// One scale's occlusion added: the box's mean against the receiver, clipped, times its weight.
extern "C" __global__ void add_scale(const long long* box, const float* receivers, double divisor,
                                     float depth, float weight, float* out, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    float under = receivers[i];
    float mean = (float)((double)box[i] / divisor);
    float x = (mean - under) / depth;
    float part = isnan(x) ? x : (x > 0.0f ? x : 0.0f);
    part = isnan(part) ? part : (part < 1.0f ? part : 1.0f);
    out[i] = out[i] + weight * part;
}

// The sum times the strength; a pixel with no height takes none.
extern "C" __global__ void finish(float* out, float strength, long long n) {
    long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    float v = out[i] * strength;
    out[i] = isnan(v) ? 0.0f : v;
}
