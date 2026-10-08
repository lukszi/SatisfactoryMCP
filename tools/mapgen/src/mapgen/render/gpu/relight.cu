// render/draw/light.py's relight_rows on the GPU, a thread a pixel: lighting.model.apply_terms
// with the operations numpy does, in its order. Compiled with --fmad=false (mapgen.jit).
// The constants arrive as float arrays, laid out as render/gpu/relight.py writes them.
// docs/map/renders.md section 41, "The draw on the GPU".

struct Light {
    float a[3], b[3], flat[3];  // ambient * sky, (1 - ambient) * sun, the flat-ground light
    float floor, knee_sq;       // SHADOW_FLOOR, SHADOW_FLOOR_KNEE squared
};

struct Tone {
    float knee, span, four_a, two_a, top_sq, clip_u;  // the shoulder, both ways
    float floor, luma[3];                             // by_luminance's floor and weights
    float dark, slope;                                // linear_to_srgb_unit's knee and slope
};

__device__ Light read_light(const float* v) {
    Light l;
    for (int k = 0; k < 3; ++k) {
        l.a[k] = v[k];
        l.b[k] = v[3 + k];
        l.flat[k] = v[6 + k];
    }
    l.floor = v[9];
    l.knee_sq = v[10];
    return l;
}

__device__ Tone read_tone(const float* v) {
    Tone s;
    s.knee = v[0];
    s.span = v[1];
    s.four_a = v[2];
    s.two_a = v[3];
    s.top_sq = v[4];
    s.clip_u = v[5];
    s.floor = v[6];
    for (int k = 0; k < 3; ++k) s.luma[k] = v[7 + k];
    s.dark = v[10];
    s.slope = v[11];
    return s;
}

__device__ float relit(const Light& l, int k, float svf, float direct, float dry) {
    float rel = (l.a[k] * svf + l.b[k] * direct) / l.flat[k];
    float off = rel - l.floor;
    rel = 0.5f * ((rel + l.floor) + sqrtf(off * off + l.knee_sq));
    return 1.0f + (rel - 1.0f) * dry;
}

__device__ unsigned char to_byte(float unit) {
    return (unsigned char)rintf(unit * 255.0f);
}

struct Terms {
    float svf, direct, dry;
};

__device__ Terms terms_at(const unsigned char* t, int channels, int which, int sky,
                          const unsigned char* land, long long p) {
    const unsigned char* at = t + p * channels;
    Terms got;
    got.svf = (float)at[sky] / 255.0f;
    got.direct = (float)at[which] / 127.0f;
    got.dry = (float)land[p] / 255.0f;
    return got;
}

extern "C" __global__ void relight_srgb(
    const unsigned char* rgb, const unsigned char* t, int channels, int which, int sky,
    const unsigned char* land, const float* lights, unsigned char* out, long long pixels) {
    long long p = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (p >= pixels) return;
    Light light = read_light(lights);
    Terms at = terms_at(t, channels, which, sky, land, p);
    for (int k = 0; k < 3; ++k) {
        float c = (float)rgb[p * 3 + k] / 255.0f;
        float lit = c * relit(light, k, at.svf, at.direct, at.dry);
        out[p * 3 + k] = to_byte(fminf(fmaxf(lit, 0.0f), 1.0f));
    }
}

__device__ float luminance(const Tone& s, const float* c) {
    float y = c[0] * s.luma[0];
    y = y + c[1] * s.luma[1];
    return fmaxf(y + c[2] * s.luma[2], s.floor);
}

__device__ float untone(const Tone& s, float o) {
    if (!(o > s.knee)) return o;
    float u = fminf(fmaxf((o - s.knee) / s.span, 0.0f), s.clip_u);
    float b = 1.0f - u;
    return s.knee + s.span * (sqrtf(b * b + s.four_a * u) - b) / s.two_a;
}

__device__ float tone(const Tone& s, float y) {
    if (!(y > s.knee)) return y;
    float x = fmaxf(y - s.knee, 0.0f) / s.span;
    return s.knee + s.span * x * (1.0f + x / s.top_sq) / (1.0f + x);
}

// The byte linear_to_srgb_unit and the round give a clipped value: arithmetic up to the
// dark knee, past it the count of numpy's measured steps at or below it, less one.
__device__ unsigned char srgb_byte(const Tone& s, const float* steps, float c) {
    if (c <= s.dark) return to_byte(c * s.slope);
    int lo = 0, hi = 256;
    while (lo < hi) {
        int mid = (lo + hi) >> 1;
        if (steps[mid] <= c) lo = mid + 1; else hi = mid;
    }
    return (unsigned char)(lo - 1);
}

extern "C" __global__ void relight_linear(
    const unsigned char* rgb, const unsigned char* t, int channels, int which, int sky,
    const unsigned char* land, const float* lights, const float* tones,
    const float* to_linear, const float* steps, unsigned char* out, long long pixels) {
    long long p = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (p >= pixels) return;
    Light light = read_light(lights);
    Tone shape = read_tone(tones);
    Terms at = terms_at(t, channels, which, sky, land, p);
    float lin[3], lit[3];
    for (int k = 0; k < 3; ++k) lin[k] = to_linear[rgb[p * 3 + k]];
    float y = luminance(shape, lin);
    float gain = untone(shape, y) / y;
    for (int k = 0; k < 3; ++k) {
        float base = lin[k] * gain;
        lit[k] = base * relit(light, k, at.svf, at.direct, at.dry);
    }
    y = luminance(shape, lit);
    gain = tone(shape, y) / y;
    for (int k = 0; k < 3; ++k) {
        float c = fminf(fmaxf(lit[k] * gain, 0.0f), 1.0f);
        out[p * 3 + k] = srgb_byte(shape, steps, c);
    }
}
