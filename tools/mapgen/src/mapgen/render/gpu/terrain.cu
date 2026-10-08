// The terrain layer's piece on the GPU, a thread a pixel: the hillshade, the height ramp lit by
// it and the borrowed shading, the shore's water composite, the void, and the byte. Each step
// does the float32 operations of its numpy or numba twin in their order (render/draw/
// painting.py, lighting/hillshade.py, palette/styles.py, palette/water/kernels.py). Compiled
// with --fmad=false (mapgen.jit). docs/map/renders.md section 43.

__device__ float clip_unit(float x) {
    // np.clip(x, 0, 1): NaN stays NaN, -0 becomes +0.
    if (isnan(x)) return x;
    if (x <= 0.0f) return 0.0f;
    return fminf(1.0f, x);
}

__device__ float clip_to(float x, float lo, float hi) {
    if (isnan(x)) return x;
    float m = x > lo ? x : lo;
    return m < hi ? m : hi;
}

// lighting.hillshade.sun_dot over a piece, np.gradient's differences at its edges, then
// hillshade's floor and range when range is not 0.
extern "C" __global__ void sun_dot(const float* z, int rows, int cols, float two_dy, float dy,
                                   const float* sun, float range, float floor, float* out) {
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    int r = blockIdx.y;
    if (c >= cols || r >= rows) return;
    const float* row = z + (long long)r * cols;
    float south, east;
    if (r == 0) south = (row[cols + c] - row[c]) / dy;
    else if (r == rows - 1) south = (row[c] - row[c - cols]) / dy;
    else south = (row[cols + c] - row[c - cols]) / two_dy;
    if (c == 0) east = (row[1] - row[0]) / dy;
    else if (c == cols - 1) east = (row[c] - row[c - 1]) / dy;
    else east = (row[c + 1] - row[c - 1]) / two_dy;
    float lit = (-east * sun[0] - south * sun[1] + sun[2])
                / sqrtf(east * east + south * south + 1.0f);
    lit = clip_unit(lit);
    out[(long long)r * cols + c] = range != 0.0f ? lit * range + floor : lit;
}

struct Shore {
    float band_m, edge_alpha, wet_darken, shade_floor, shade_range;
    float stroke, foam, foam_depth, foam_width, foam_white;
    float shallow[3], deep[3], tint[3];
};

__device__ Shore read_shore(const float* v) {
    Shore s;
    s.band_m = v[0];
    s.edge_alpha = v[1];
    s.wet_darken = v[2];
    s.shade_floor = v[3];
    s.shade_range = v[4];
    s.stroke = v[5];
    s.foam = v[6];
    s.foam_depth = v[7];
    s.foam_width = v[8];
    s.foam_white = v[9];
    for (int k = 0; k < 3; ++k) {
        s.shallow[k] = v[10 + k];
        s.deep[k] = v[13 + k];
        s.tint[k] = v[16 + k];
    }
    return s;
}

struct Water {
    const float *cover, *depth, *banks, *above_m, *edge, *depth_m, *below_m, *ocean, *transmit;
};

// palette/water/kernels.py's water_composite at pixel p: land in, sRGB 0..255 out.
__device__ void composite(const Water& w, const Shore& s, long long p, float shade, bool every,
                          float* rgb) {
    float bank = w.banks[p], banded[3], under[3];
    for (int k = 0; k < 3; ++k) banded[k] = rgb[k];
    if (s.band_m != 0.0f) {
        float reach = clip_unit(1.0f - w.above_m[p] / s.band_m);
        float weight = reach * reach * bank;
        for (int k = 0; k < 3; ++k) banded[k] = banded[k] * (1.0f - weight + weight * s.tint[k]);
    }
    float opacity = bank * (s.edge_alpha + (1.0f - s.edge_alpha) * (1.0f - w.transmit[p]))
                    + (1.0f - bank);
    float wet = 1.0f - (1.0f - s.wet_darken) * bank;
    float t = w.depth[p], light = s.shade_floor + s.shade_range * shade;
    for (int k = 0; k < 3; ++k) {
        float colour = (s.shallow[k] * (1.0f - t) + s.deep[k] * t) * light;
        under[k] = banded[k] * wet * (1.0f - opacity) + colour * opacity;
    }
    float cover = w.cover[p];
    bool mixed = every || cover != 0.0f;
    float keep = 1.0f - cover, foam = 0.0f;
    if (s.foam != 0.0f) {
        float shallow = clip_unit(1.0f - w.depth_m[p] / s.foam_depth);
        shallow = shallow * clip_unit(1.0f - w.below_m[p] / s.foam_width);
        foam = s.foam * shallow * cover * w.ocean[p];
    }
    for (int k = 0; k < 3; ++k) {
        float value = mixed ? banded[k] * keep + under[k] * cover : banded[k];
        if (s.stroke != 0.0f) value = value * (1.0f - s.stroke * w.edge[p]);
        if (s.foam != 0.0f) value = value * (1.0f - foam) + s.foam_white * foam;
        rgb[k] = value;
    }
}

struct Void {
    const float *cover, *falloff, *pit, *rim;
    const unsigned char* missing;
};

// palette.styles' with_void (an open sea) or with_sea (none) at pixel p. colours: the void's
// edge, the pit's edge, the sea, the pit, the rim.
__device__ void voided(const Void& v, const float* colours, int mode, long long p, float* rgb) {
    const float *edge_rgb = colours, *pit_edge = colours + 3, *sea = colours + 6;
    const float *pit_rgb = colours + 9, *rim_rgb = colours + 12;
    if (mode == 0) {
        if (v.missing[p]) for (int k = 0; k < 3; ++k) rgb[k] = sea[k];
        return;
    }
    if (mode == 1) return;
    float weight = v.cover[p], line = v.rim[p];
    if (!(weight != 0.0f || line != 0.0f)) return;
    float deep = v.falloff[p], hole = v.pit[p];
    for (int k = 0; k < 3; ++k) {
        float edge = edge_rgb[k] * (1.0f - hole) + pit_edge[k] * hole;
        float colour = edge * (1.0f - deep) + (sea[k] * (1.0f - hole) + pit_rgb[k] * hole) * deep;
        rgb[k] = (rgb[k] * (1.0f - weight) + colour * weight) * (1.0f - line) + rim_rgb[k] * line;
    }
}

__device__ unsigned char truncated(float v) {
    // np.clip(v, 0, 255).astype(np.uint8)
    if (isnan(v)) return 0;
    return (unsigned char)clip_to(v, 0.0f, 255.0f);
}

// water: cover, depth, banks, above_m, edge, depth_m, below_m, ocean, transmit, each a plane
// of the piece. voids: cover, falloff, pit, rim. knobs: ramp low, ramp span, then the Shore
// block. every: the composite mixes every pixel. void_mode: 0 with_sea, 1 none, 2 with_void.
extern "C" __global__ void terrain(
    const float* z, const float* shade, const float* borrow, const float* cover,
    const float* depth, const float* banks, const float* above_m, const float* edge,
    const float* depth_m, const float* below_m, const float* ocean, const float* transmit,
    const float* void_cover, const float* falloff, const float* pit, const float* rim,
    const unsigned char* missing, const float* stops, int n_stops, const float* knobs,
    const float* void_colours, int every, int void_mode, int cols, int r0, int c0,
    int out_rows, int out_cols, unsigned char* out) {
    int j = blockIdx.x * blockDim.x + threadIdx.x;
    int i = blockIdx.y;
    if (j >= out_cols || i >= out_rows) return;
    long long p = (long long)(r0 + i) * cols + (c0 + j);
    Water w = {cover, depth, banks, above_m, edge, depth_m, below_m, ocean, transmit};
    Void v = {void_cover, falloff, pit, rim, missing};
    Shore s = read_shore(knobs + 2);
    float height = clip_unit((z[p] - knobs[0]) / knobs[1]);
    float position = clip_unit(height) * (float)(n_stops - 1);
    float low = fminf(fmaxf(floorf(position), 0.0f), (float)(n_stops - 2));
    int at = (int)low;
    float fraction = position - low;
    float light = shade[p] * borrow[p], rgb[3];
    for (int k = 0; k < 3; ++k) {
        float ramp = stops[at * 3 + k] * (1.0f - fraction) + stops[(at + 1) * 3 + k] * fraction;
        rgb[k] = ramp * light;
    }
    composite(w, s, p, shade[p], every != 0, rgb);
    voided(v, void_colours, void_mode, p, rgb);
    long long o = ((long long)i * out_cols + j) * 3;
    for (int k = 0; k < 3; ++k) out[o + k] = truncated(rgb[k]);
}
