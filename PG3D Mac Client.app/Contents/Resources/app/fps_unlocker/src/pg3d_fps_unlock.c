// Pixel Gun 3D macOS 26.11.0 / build 151027 (x86_64) frame pacing override.
// This library changes process memory only; it never writes into the app bundle.

#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

enum {
    kTargetFrameRateWrapperRva = 0x5009370,
    kVSyncCountWrapperRva = 0x500E7C0,
    kRetryIntervalMilliseconds = 250,
    kRetryLimit = 480,
};

typedef void (*UnityIntSetter)(int value);

typedef struct {
    const char *description;
    uintptr_t wrapper_rva;
    const char *binding_name;
    int forced_value;
    UnityIntSetter hook;
    UnityIntSetter original;
    void *volatile *cache_slot;
    bool installed;
    bool binding_was_invalid;
} Override;

static uintptr_t g_image_slide;
static uintptr_t g_image_start;
static uintptr_t g_image_end;
static int g_requested_frame_rate = -1;
static dispatch_source_t g_retry_timer;
static unsigned int g_retry_count;
static FILE *g_log_file;
static bool g_observe_only;
static bool g_active;
static void *(*g_resolve_icall)(const char *);
static int (*g_get_target)(void);
static int (*g_get_vsync)(void);
static int (*g_get_frames)(void);
static int (*g_get_rendered)(void);
static bool (*g_get_focused)(void);
static struct timespec g_last_sample;
static unsigned int g_last_frames;
static unsigned int g_last_rendered;
extern void pg3d_presentation_start(void);
extern void pg3d_presentation_sample(double elapsed);

static void log_message(const char *format, ...) {
    va_list arguments;
    va_start(arguments, format);
    FILE *destination = g_log_file != NULL ? g_log_file : stderr;
    fprintf(destination, "[PG3D FPS Unlock pid=%d] ", getpid());
    vfprintf(destination, format, arguments);
    fputc('\n', destination);
    fflush(destination);
    va_end(arguments);
}

void pg3d_presentation_log(const char *message) {
    log_message("%s", message);
}

static bool range_is_inside_image(uintptr_t address, size_t length) {
    if (address < g_image_start || address > g_image_end) {
        return false;
    }
    return length <= g_image_end - address;
}

static bool find_game_assembly(void) {
    const uint32_t image_count = _dyld_image_count();
    for (uint32_t index = 0; index < image_count; ++index) {
        const char *image_name = _dyld_get_image_name(index);
        if (image_name == NULL || strstr(image_name, "/GameAssembly.dylib") == NULL) {
            continue;
        }

        const struct mach_header_64 *header =
            (const struct mach_header_64 *)_dyld_get_image_header(index);
        if (header == NULL || header->magic != MH_MAGIC_64 ||
            header->cputype != CPU_TYPE_X86_64) {
            log_message("GameAssembly is not a supported x86_64 Mach-O image.");
            return false;
        }

        const uint8_t *command_bytes = (const uint8_t *)(header + 1);
        uintptr_t text_vmaddr = 0;
        uintptr_t lowest_vmaddr = UINTPTR_MAX;
        uintptr_t highest_vmaddr = 0;
        bool saw_text_segment = false;

        for (uint32_t command_index = 0; command_index < header->ncmds; ++command_index) {
            const struct load_command *command = (const struct load_command *)command_bytes;
            if (command->cmdsize < sizeof(struct load_command)) {
                return false;
            }
            if (command->cmd == LC_SEGMENT_64 && command->cmdsize >= sizeof(struct segment_command_64)) {
                const struct segment_command_64 *segment =
                    (const struct segment_command_64 *)command;
                if (segment->vmsize != 0) {
                    if (segment->vmaddr < lowest_vmaddr) {
                        lowest_vmaddr = (uintptr_t)segment->vmaddr;
                    }
                    const uintptr_t segment_end = (uintptr_t)(segment->vmaddr + segment->vmsize);
                    if (segment_end > highest_vmaddr) {
                        highest_vmaddr = segment_end;
                    }
                }
                if (strncmp(segment->segname, SEG_TEXT, sizeof(segment->segname)) == 0) {
                    text_vmaddr = (uintptr_t)segment->vmaddr;
                    saw_text_segment = true;
                }
            }
            command_bytes += command->cmdsize;
        }

        if (!saw_text_segment || lowest_vmaddr == UINTPTR_MAX || highest_vmaddr <= lowest_vmaddr) {
            return false;
        }

        g_image_slide = (uintptr_t)header - text_vmaddr;
        g_image_start = g_image_slide + lowest_vmaddr;
        g_image_end = g_image_slide + highest_vmaddr;
        void *handle = dlopen(image_name, RTLD_NOW | RTLD_NOLOAD);
        if (handle == NULL) {
            return false;
        }
        g_resolve_icall = (void *(*)(const char *))dlsym(handle, "il2cpp_resolve_icall");
        dlclose(handle);
        log_message("attached to GameAssembly (%s)", image_name);
        return true;
    }
    return false;
}

static bool wrapper_matches_binding(const uint8_t *wrapper, const char *binding_name) {
    // All Unity IL2CPP icall wrappers in this supported build begin with a lazy
    // cached function-pointer lookup. Validate it and then validate the exact
    // resolver string before redirecting the cache slot.
    static const uint8_t prefix[] = {
        0x48, 0x8B, 0x05, 0x00, 0x00, 0x00, 0x00,
        0x48, 0x85, 0xC0, 0x74, 0x02, 0xFF, 0xE0,
        0x55, 0x48, 0x89, 0xE5, 0x53, 0x50, 0x48, 0x8D, 0x05,
    };
    if (memcmp(wrapper, prefix, 3) != 0 ||
        memcmp(wrapper + 7, prefix + 7, sizeof(prefix) - 7) != 0) {
        return false;
    }

    int32_t string_displacement;
    memcpy(&string_displacement, wrapper + 23, sizeof(string_displacement));
    const char *resolved_name = (const char *)(wrapper + 27 + string_displacement);
    const size_t expected_length = strlen(binding_name);
    if (!range_is_inside_image((uintptr_t)resolved_name, expected_length + 1)) {
        return false;
    }
    return memcmp(resolved_name, binding_name, expected_length + 1) == 0;
}

static void target_frame_rate_hook(int ignored_value);
static void vsync_count_hook(int ignored_value);

static Override g_target_frame_rate = {
    .description = "target frame rate",
    .wrapper_rva = kTargetFrameRateWrapperRva,
    .binding_name = "UnityEngine.Application::set_targetFrameRate(System.Int32)",
    .forced_value = -1,
    .hook = target_frame_rate_hook,
};

static Override g_vsync_count = {
    .description = "v-sync",
    .wrapper_rva = kVSyncCountWrapperRva,
    .binding_name = "UnityEngine.QualitySettings::set_vSyncCount(System.Int32)",
    .forced_value = 0,
    .hook = vsync_count_hook,
};

static void target_frame_rate_hook(int ignored_value) {
    (void)ignored_value;
    if (g_target_frame_rate.original != NULL) {
        g_target_frame_rate.original(g_target_frame_rate.forced_value);
    }
}

static void vsync_count_hook(int ignored_value) {
    (void)ignored_value;
    if (g_vsync_count.original != NULL) {
        g_vsync_count.original(g_vsync_count.forced_value);
    }
}

static bool prepare_override(Override *override) {
    if (override->cache_slot != NULL) {
        return true;
    }

    const uintptr_t wrapper_address = g_image_slide + override->wrapper_rva;
    if (!range_is_inside_image(wrapper_address, 32)) {
        if (!override->binding_was_invalid) {
            log_message("%s wrapper RVA is outside GameAssembly; unsupported build.",
                        override->description);
            override->binding_was_invalid = true;
        }
        return false;
    }

    const uint8_t *wrapper = (const uint8_t *)wrapper_address;
    if (!wrapper_matches_binding(wrapper, override->binding_name)) {
        if (!override->binding_was_invalid) {
            log_message("%s binding does not match this game build; no override applied.",
                        override->description);
            override->binding_was_invalid = true;
        }
        return false;
    }

    int32_t cache_displacement;
    memcpy(&cache_displacement, wrapper + 3, sizeof(cache_displacement));
    void *volatile *cache_slot = (void *volatile *)(wrapper + 7 + cache_displacement);
    if (!range_is_inside_image((uintptr_t)cache_slot, sizeof(*cache_slot))) {
        log_message("%s cache slot is outside GameAssembly; no override applied.",
                    override->description);
        return false;
    }

    override->cache_slot = cache_slot;
    return true;
}

static void install_override(Override *override) {
    __atomic_store_n(override->cache_slot, (void *)override->hook, __ATOMIC_RELEASE);
    override->installed = true;
    override->original(override->forced_value);

    if (override == &g_target_frame_rate && override->forced_value == -1) {
        log_message("target frame rate override installed: uncapped (-1).");
    } else {
        log_message("%s override installed: %d.", override->description,
                    override->forced_value);
    }
}

static double seconds_between(struct timespec start, struct timespec end) {
    return (double)(end.tv_sec - start.tv_sec) +
           (double)(end.tv_nsec - start.tv_nsec) / 1000000000.0;
}

static bool initialize_bindings(void) {
    // Validate BOTH wrappers before modifying either. A warm target cache proves
    // Unity registered its icalls, even when the game never calls the v-sync
    // wrapper. Resolve that cold callback explicitly after this readiness check.
    if (!prepare_override(&g_target_frame_rate) || !prepare_override(&g_vsync_count)) {
        return false;
    }
    if (__atomic_load_n(g_target_frame_rate.cache_slot, __ATOMIC_ACQUIRE) == NULL ||
        g_resolve_icall == NULL) {
        return false;
    }

    g_get_target = (int (*)(void))g_resolve_icall("UnityEngine.Application::get_targetFrameRate");
    g_get_vsync = (int (*)(void))g_resolve_icall("UnityEngine.QualitySettings::get_vSyncCount");
    g_get_frames = (int (*)(void))g_resolve_icall("UnityEngine.Time::get_frameCount");
    g_get_rendered = (int (*)(void))g_resolve_icall("UnityEngine.Time::get_renderedFrameCount");
    g_get_focused = (bool (*)(void))g_resolve_icall("UnityEngine.Application::get_isFocused");
    g_target_frame_rate.original = (UnityIntSetter)g_resolve_icall(
        "UnityEngine.Application::set_targetFrameRate");
    g_vsync_count.original = (UnityIntSetter)g_resolve_icall(
        "UnityEngine.QualitySettings::set_vSyncCount");
    if (g_get_target == NULL || g_get_vsync == NULL || g_get_frames == NULL ||
        g_get_rendered == NULL || g_get_focused == NULL ||
        g_target_frame_rate.original == NULL || g_vsync_count.original == NULL) {
        log_message("required Unity binding unavailable; no override applied.");
        dispatch_source_cancel(g_retry_timer);
        return false;
    }
    if (g_get_frames() < 3) {
        return false;
    }
    log_message("before override: target=%d, vSyncCount=%d, v-sync cache=%s.",
                g_get_target(), g_get_vsync(),
                *g_vsync_count.cache_slot == NULL ? "cold" : "initialized");
    if (!g_observe_only) {
        install_override(&g_vsync_count);
        install_override(&g_target_frame_rate);
        if (g_get_target() != g_target_frame_rate.forced_value || g_get_vsync() != 0) {
            log_message("Unity readback did not match requested values; override unverified.");
            return false;
        }
        log_message("render pacing override is active: target=%d, vSyncCount=%d (read back from Unity).",
                    g_get_target(), g_get_vsync());
    } else {
        log_message("frame pacing observation is active: no values changed.");
    }
    pg3d_presentation_start();
    clock_gettime(CLOCK_MONOTONIC, &g_last_sample);
    g_last_frames = (unsigned int)g_get_frames();
    g_last_rendered = (unsigned int)g_get_rendered();
    return true;
}

static void measure_frames(void) {
    if (!g_observe_only) {
        // The game can bypass a wrapper through an inlined icall site or change
        // quality levels. Read back pacing values and reapply only if necessary.
        if (g_get_vsync() != 0) {
            log_message("game changed vSyncCount to %d; restoring 0.", g_get_vsync());
            g_vsync_count.original(0);
        }
        if (g_get_target() != g_requested_frame_rate) {
            log_message("game changed target to %d; restoring %d.", g_get_target(), g_requested_frame_rate);
            g_target_frame_rate.original(g_requested_frame_rate);
        }
    }
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    const double elapsed = seconds_between(g_last_sample, now);
    if (elapsed < 5.0) {
        return;
    }
    const unsigned int frames = (unsigned int)g_get_frames();
    const unsigned int rendered = (unsigned int)g_get_rendered();
    log_message("engine measurement: rendered=%.1f FPS, loop=%.1f FPS, window=%.2fs, target=%d, vSyncCount=%d, focused=%s.",
                (double)(rendered - g_last_rendered) / elapsed,
                (double)(frames - g_last_frames) / elapsed, elapsed,
                g_get_target(), g_get_vsync(), g_get_focused() ? "yes" : "no");
    pg3d_presentation_sample(elapsed);
    g_last_sample = now;
    g_last_frames = frames;
    g_last_rendered = rendered;
}

static void retry_installation(void) {
    if (g_active) {
        measure_frames();
        return;
    }
    ++g_retry_count;
    if ((g_image_start != 0 || find_game_assembly()) && initialize_bindings()) {
        g_active = true;
        return;
    }
    if (g_target_frame_rate.binding_was_invalid || g_vsync_count.binding_was_invalid) {
        dispatch_source_cancel(g_retry_timer);
    } else if (g_retry_count >= kRetryLimit) {
        log_message("timed out waiting for Unity initialization; override not active.");
        dispatch_source_cancel(g_retry_timer);
    }
}

static void parse_requested_frame_rate(void) {
    const char *value = getenv("PG3D_UNLOCK_FPS");
    if (value == NULL || *value == '\0' || strcmp(value, "uncapped") == 0) {
        return;
    }

    char *end = NULL;
    const long parsed = strtol(value, &end, 10);
    if (*value != '\0' && end != NULL && *end == '\0' &&
        ((parsed == -1) || (parsed >= 30 && parsed <= 1000))) {
        g_requested_frame_rate = (int)parsed;
    } else {
        log_message("invalid PG3D_UNLOCK_FPS value '%s'; using uncapped mode.", value);
    }
}

__attribute__((constructor))
static void start_unlocker(void) {
    const char *log_path = getenv("PG3D_UNLOCK_LOG");
    if (log_path != NULL && *log_path != '\0') {
        g_log_file = fopen(log_path, "a");
        if (g_log_file == NULL) {
            fputs("[PG3D FPS Unlock] cannot open dedicated log; using stderr.\n", stderr);
        }
    }
    const char *observe = getenv("PG3D_UNLOCK_OBSERVE");
    g_observe_only = observe != NULL && strcmp(observe, "1") == 0;
    parse_requested_frame_rate();
    g_target_frame_rate.forced_value = g_requested_frame_rate;
    log_message("loaded (requested frame rate: %s).",
                g_requested_frame_rate == -1 ? "uncapped" : getenv("PG3D_UNLOCK_FPS"));

    // Run on the application's main queue, after the normal Unity startup path
    // has had an opportunity to resolve its native IL2CPP callbacks.
    dispatch_async(dispatch_get_main_queue(), ^{
        g_retry_timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,
                                               dispatch_get_main_queue());
        if (g_retry_timer == NULL) {
            log_message("could not create startup timer; no override applied.");
            return;
        }
        dispatch_source_set_timer(g_retry_timer,
                                  dispatch_time(DISPATCH_TIME_NOW,
                                                (int64_t)kRetryIntervalMilliseconds * NSEC_PER_MSEC),
                                  (uint64_t)kRetryIntervalMilliseconds * NSEC_PER_MSEC,
                                  25 * NSEC_PER_MSEC);
        dispatch_source_set_event_handler(g_retry_timer, ^{
            retry_installation();
        });
        dispatch_resume(g_retry_timer);
    });
}
