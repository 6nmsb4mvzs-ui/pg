// Metal drawable timing probe for PG3D frame pacing diagnostics.
// Linked into PG3DFPSUnlock.dylib when built with the macOS SDK (build.sh).
// Swizzles CAMetalLayer to measure actual frame-to-frame presentation intervals,
// detects micro-stutter, and reports percentile statistics every sample window.
// Changes nothing about how the game renders — observation only.

#import <QuartzCore/CAMetalLayer.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <mach/mach_time.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

extern void pg3d_presentation_log(const char *message);

enum { kRingSize = 1024 };

static _Atomic uint64_t g_ring[kRingSize];
static _Atomic unsigned g_ring_write;
static _Atomic unsigned g_sample_total;
static _Atomic unsigned g_sample_stutters;
static _Atomic uint64_t g_last_abs;
static double g_abs_to_ns;
static bool g_installed;

// Shared with pg3d_fps_unlock.c for adaptive capping decisions.
double g_present_stddev_ms;
double g_present_p99_ms;

static IMP g_original_nextDrawable;

static void record_drawable_present(uint64_t now) {
    uint64_t prev = atomic_exchange(&g_last_abs, now);
    if (prev == 0) return;

    uint64_t delta_ns = (uint64_t)((double)(now - prev) * g_abs_to_ns);
    unsigned slot = atomic_fetch_add(&g_ring_write, 1) % kRingSize;
    atomic_store(&g_ring[slot], delta_ns);
    atomic_fetch_add(&g_sample_total, 1);

    // >22 ms between presents is a visible stutter at any modern refresh rate.
    if (delta_ns > 22000000ULL)
        atomic_fetch_add(&g_sample_stutters, 1);
}

static id swizzled_nextDrawable(id self, SEL _cmd) {
    id drawable = ((id (*)(id, SEL))g_original_nextDrawable)(self, _cmd);
    if (drawable) {
        @try {
            // addPresentedHandler fires on the GPU completion thread when the
            // drawable is actually composited onto the display.  Available
            // macOS 10.15.4+; PG3D requires macOS 12+.
            [(id<MTLDrawable>)drawable addPresentedHandler:^(id<MTLDrawable> __unused d) {
                record_drawable_present(mach_absolute_time());
            }];
        } @catch (...) {
            // Fallback: record CPU-side acquire time (less accurate but safe).
            record_drawable_present(mach_absolute_time());
        }
    }
    return drawable;
}

void pg3d_presentation_start(void) {
    if (g_installed) return;

    mach_timebase_info_data_t tb;
    mach_timebase_info(&tb);
    g_abs_to_ns = (double)tb.numer / (double)tb.denom;

    Class cls = objc_getClass("CAMetalLayer");
    if (!cls) {
        pg3d_presentation_log("presentation probe: CAMetalLayer not found.");
        return;
    }
    Method m = class_getInstanceMethod(cls, @selector(nextDrawable));
    if (!m) {
        pg3d_presentation_log("presentation probe: nextDrawable not found.");
        return;
    }
    g_original_nextDrawable = method_setImplementation(m, (IMP)swizzled_nextDrawable);
    g_installed = true;
    pg3d_presentation_log("presentation probe active: Metal frame timing enabled.");
}

static int cmp_u64(const void *a, const void *b) {
    uint64_t va = *(const uint64_t *)a, vb = *(const uint64_t *)b;
    return (va > vb) - (va < vb);
}

void pg3d_presentation_sample(double elapsed) {
    (void)elapsed;
    unsigned total = atomic_exchange(&g_sample_total, 0);
    unsigned stutters = atomic_exchange(&g_sample_stutters, 0);
    if (total < 20) {
        g_present_stddev_ms = 0;
        g_present_p99_ms = 0;
        return;
    }

    unsigned count = total < kRingSize ? total : kRingSize;
    uint64_t sorted[kRingSize];
    unsigned cursor = atomic_load(&g_ring_write);
    for (unsigned i = 0; i < count; i++)
        sorted[i] = atomic_load(&g_ring[(cursor - count + i) % kRingSize]);
    qsort(sorted, count, sizeof(uint64_t), cmp_u64);

    double sum = 0;
    for (unsigned i = 0; i < count; i++) sum += (double)sorted[i];
    double mean = sum / count;
    double avg_ms = mean / 1e6;

    double variance = 0;
    for (unsigned i = 0; i < count; i++) {
        double d = (double)sorted[i] - mean;
        variance += d * d;
    }
    double stddev_ms = sqrt(variance / count) / 1e6;

    double p01_ms  = (double)sorted[(unsigned)(count * 0.01)] / 1e6;
    double p50_ms  = (double)sorted[count / 2] / 1e6;
    double p99_ms  = (double)sorted[(unsigned)(count * 0.99)] / 1e6;
    double max_ms  = (double)sorted[count - 1] / 1e6;

    // Publish for the adaptive capper in pg3d_fps_unlock.c.
    g_present_stddev_ms = stddev_ms;
    g_present_p99_ms = p99_ms;

    char buf[400];
    snprintf(buf, sizeof(buf),
             "present: %.2fms avg  %.2fms p50  %.2fms p99  %.2fms max  "
             "%.2fms stddev  %u stutters/%u frames (%.0f%% clean)  1%%low=%.1f FPS.",
             avg_ms, p50_ms, p99_ms, max_ms, stddev_ms,
             stutters, total,
             total > 0 ? 100.0 * (1.0 - (double)stutters / total) : 0.0,
             p01_ms > 0 ? 1000.0 / p01_ms : 0.0);
    pg3d_presentation_log(buf);
}
