// The light's loops on the GPU: lighting/kernels.py's march and sky view, a thread a pixel.
// Compiled with --fmad=false, so every float operation is the one IEEE operation numpy does,
// in the same order. docs/map/renders.md section 41, "On the GPU".

__device__ float raise_to(float top, float rise) {
    // np.maximum(top, rise) as terrain.kernels.raise_nan has it: NaN when either is.
    float larger = !(rise <= top) ? rise : top;
    return isnan(top) ? top : larger;
}

__device__ float bilinear(const float* z, long long width, long long r, long long c,
                          float fy, float fx, float gy, float gx) {
    const float* a = z + r * width + c;
    const float* d = a + width;
    return (a[0] * gx + a[1] * fx) * gy + (d[0] * gx + d[1] * fx) * fy;
}

__device__ float sample(const float* z, long long width, long long r, long long c, bool smooth,
                        float fy, float fx, float gy, float gx) {
    return smooth ? bilinear(z, width, r, c, fy, fx, gy, gx) : z[r * width + c];
}

extern "C" __global__ void march(
    const float* solid, long long solid_w, const float* z, long long z_w, int halo,
    const bool* smooth, const int* iy, const int* ix, const float* fy, const float* fx,
    const float* gy, const float* gx, const float* scale, int steps,
    float* best, int rows, int cols) {
    int j = blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (j >= cols || i >= rows) return;
    long long r = halo + i;
    float near = z[r * z_w + halo + j];
    float top = best[(long long)i * cols + j];
    for (int s = 0; s < steps; ++s) {
        long long sr = r + iy[s], sc = (long long)halo + ix[s] + j;
        float at = sample(solid, solid_w, sr, sc, smooth[s], fy[s], fx[s], gy[s], gx[s]);
        top = raise_to(top, (at - near) * scale[s]);
    }
    best[(long long)i * cols + j] = top;
}

extern "C" __global__ void sky_view(
    const float* z, long long z_w, int halo, const int* iy, const int* ix, const float* fy,
    const float* fx, const float* gy, const float* gx, const float* scale, int dirs, int steps,
    float* out, int rows, int cols) {
    int j = blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (j >= cols || i >= rows) return;
    long long r = halo + i;
    float near = z[r * z_w + halo + j];
    float acc = 0.0f;
    for (int d = 0; d < dirs; ++d) {
        float best = 0.0f;
        for (int s = 0; s < steps; ++s) {
            int at = d * steps + s;
            float zz = bilinear(z, z_w, r + iy[at], (long long)halo + ix[at] + j,
                                fy[at], fx[at], gy[at], gx[at]);
            best = raise_to(best, (zz - near) * scale[s]);
        }
        acc += best / sqrtf(1.0f + best * best);
    }
    out[(long long)i * cols + j] = 1.0f - acc / (float)dirs;
}
