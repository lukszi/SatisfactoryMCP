// The coral footprints' loops on the GPU: reference.py's texel_marks and pixel_land, a thread a
// texel or a pixel. Compiled with --fmad=false (mapgen/jit.py), so each float operation is the
// one IEEE operation numpy does. docs/map/painted.md section 27, "Whole footprints".

extern "C" __global__ void texel_marks(
    const unsigned char* cls, const float* z, long long px_w,
    const long long* row_start, const long long* row_stop,
    const long long* col_start, const long long* col_stop, const unsigned char* groups,
    const unsigned char* wet, const unsigned char* reach, const float* ground,
    const short* level, float no_ground, float ocean_dm, float ocean_m, float reach_m,
    unsigned char* marks, int rows, int cols) {
    int k = blockIdx.x * blockDim.x + threadIdx.x;
    int j = blockIdx.y;
    if (k >= cols || j >= rows) return;
    float no_top = __int_as_float(0xff800000);
    float top[3] = {no_top, no_top, no_top};
    for (long long r = row_start[j]; r < row_stop[j]; ++r) {
        for (long long c = col_start[k]; c < col_stop[k]; ++c) {
            float v = z[r * px_w + c];
            unsigned char g = groups[cls[r * px_w + c]];
            if (g != 0 && v == v && v > top[g]) top[g] = v;
        }
    }
    long long t = (long long)j * cols + k;
    bool w = wet[t] != 0;
    float g0 = ground[t];
    bool sea = w || (reach[t] != 0 && g0 != no_ground && g0 < ocean_dm);
    short lv = level[t];
    float lvl = (w && lv != -32768) ? (float)lv / 10.0f : (sea ? ocean_m : no_top);
    float floor_m = lvl - reach_m;
    unsigned char m = sea ? 4 : 0;
    if (top[1] / 100.0f > floor_m) m |= 1;
    if (top[2] / 100.0f > floor_m) m |= 2;
    marks[t] = m;
}

extern "C" __global__ void pixel_land(
    const unsigned char* land, long long land_w, const long long* rows,
    const long long* cols, const unsigned char* cls, const unsigned char* groups, bool* out,
    int out_rows, int out_cols) {
    int j = blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (j >= out_cols || i >= out_rows) return;
    long long p = (long long)i * out_cols + j;
    out[p] = (land[rows[i] * land_w + cols[j]] & groups[cls[p]]) != 0;
}
