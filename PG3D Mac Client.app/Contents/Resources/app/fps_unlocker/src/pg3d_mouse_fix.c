// PG3D mouse fix. PG3D Mac Client loads this into Pixel Gun 3D (DYLD_INSERT_LIBRARIES).
// It works around macOS input bugs in the game itself:
//   1. PG3D locks the system cursor in the middle of the screen and draws its own cursor,
//      but macOS sometimes keeps showing the system arrow there.
//   2. Clicks only register on the second press when the game window has lost key status.
// It changes process memory only; nothing in the app bundle is touched. Set
// PG3D_MOUSEFIX=0 to load it without doing anything.
//
// Built without the macOS SDK (zig cc, -undefined dynamic_lookup), so the few AppKit,
// CoreGraphics and libdispatch entry points it needs are declared here by hand.

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct objc_object *id;
typedef struct objc_selector *SEL;
typedef struct objc_class *Class;
typedef void (*IMP)(void);
typedef signed char BOOL;

extern Class objc_getClass(const char *name);
extern SEL sel_registerName(const char *name);
extern void objc_msgSend(void);
extern Class object_getClass(id object);
extern IMP class_replaceMethod(Class cls, SEL name, IMP imp, const char *types);

extern void *dlsym(void *handle, const char *symbol);
#define RTLD_DEFAULT_HANDLE ((void *)-2)
struct timespec_ { long tv_sec; long tv_nsec; };
extern int clock_gettime(int clock_id, struct timespec_ *tp);
#define CLOCK_MONOTONIC_ 6

extern int32_t CGAssociateMouseAndMouseCursorPosition(uint32_t connected);
extern int32_t CGDisplayHideCursor(uint32_t display);
extern int32_t CGCursorIsVisible(void);
extern int32_t CGDisplayShowCursor(uint32_t display);
extern uint32_t CGMainDisplayID(void);
typedef void *CGEventSourceRef;
extern CGEventSourceRef CGEventSourceCreate(int32_t stateID);
extern void CGEventSourceSetLocalEventsSuppressionInterval(CGEventSourceRef source, double seconds);

extern char _dispatch_main_q;
extern void dispatch_async_f(void *queue, void *context, void (*work)(void *));
extern void dispatch_after_f(uint64_t when, void *queue, void *context, void (*work)(void *));
extern uint64_t dispatch_time(uint64_t when, int64_t delta);

#define MAIN_QUEUE ((void *)&_dispatch_main_q)
#define SEL_(name) sel_registerName(name)
#define MSG(ret, ...) ((ret (*)(id, SEL, ##__VA_ARGS__))objc_msgSend)

enum { kTickMs = 100 };
static const uint64_t kMouseDownMask = (1ULL << 1) | (1ULL << 3) | (1ULL << 25);
// mouseMoved events let us force-hide the cursor whenever the mouse moves while locked
static const uint64_t kMouseMovedMask = (1ULL << 5) | (1ULL << 6) | (1ULL << 27);

static volatile bool g_locked;       // the game disassociated the mouse (cursor locked)
static bool g_we_hid;                // we hold CGDisplayHideCursor calls
static unsigned g_cg_hide_depth;     // how many unbalanced CGDisplayHideCursor calls we hold
static bool g_was_active;
static bool g_monitor_installed;
static bool g_move_monitor_installed;
static unsigned g_click_fixes;
static unsigned g_kicks;
static unsigned g_force_hides;

static void note(const char *format, unsigned value) {
    fprintf(stderr, "[PG3D Mouse Fix pid=%d] ", getpid());
    fprintf(stderr, format, value);
    fputc('\n', stderr);
    fflush(stderr);
}

static id shared_app(void) {
    Class cls = objc_getClass("NSApplication");
    return cls ? MSG(id)((id)cls, SEL_("sharedApplication")) : NULL;
}

// Hide the cursor using both NSCursor and CGDisplay APIs.
// NSCursor's hide is a separate counter from CGDisplay's; both need to agree
// for the cursor to actually be invisible.
static void force_hide_cursor(void) {
    uint32_t display = CGMainDisplayID();
    // CG-level: ensure at least one hide call is outstanding
    if (!g_we_hid) {
        CGDisplayHideCursor(display);
        g_we_hid = true;
        g_cg_hide_depth = 1;
    }
    // If the cursor is still showing despite our hide, WindowServer's internal
    // show count is ahead of us.  Push extra hides to catch up.
    if (CGCursorIsVisible()) {
        CGDisplayHideCursor(display);
        g_cg_hide_depth++;
    }
    // NSCursor-level: [NSCursor hide] and setHiddenUntilMouseMoves: are the
    // Cocoa-layer APIs that WindowServer also respects.
    Class nscursor = objc_getClass("NSCursor");
    if (nscursor) {
        MSG(void)((id)nscursor, SEL_("hide"));
        MSG(void, BOOL)((id)nscursor, SEL_("setHiddenUntilMouseMoves:"), 1);
    }
}

// Undo all our hides so the cursor comes back when the game unlocks or PG3D
// goes to the background.
static void force_show_cursor(void) {
    uint32_t display = CGMainDisplayID();
    for (; g_cg_hide_depth > 0; --g_cg_hide_depth)
        CGDisplayShowCursor(display);
    g_we_hid = false;
    // Balance our NSCursor hides
    Class nscursor = objc_getClass("NSCursor");
    if (nscursor) {
        MSG(void)((id)nscursor, SEL_("unhide"));
        MSG(void, BOOL)((id)nscursor, SEL_("setHiddenUntilMouseMoves:"), 0);
    }
}

static void sync_cursor(void *unused) {
    (void)unused;
    id app = shared_app();
    bool active = app && MSG(BOOL)(app, SEL_("isActive"));
    bool want = g_locked && active;
    if (want) {
        force_hide_cursor();
    } else if (!want && g_we_hid) {
        force_show_cursor();
    }
}

static void kick_cursor(void) {
    force_show_cursor();
    force_hide_cursor();
    if (++g_kicks <= 3) note("re-hid the system cursor after PG3D became active (%u)", g_kicks);
}

static int32_t fixed_associate(uint32_t connected) {
    bool was_locked = g_locked;
    g_locked = !connected;
    int32_t result = CGAssociateMouseAndMouseCursorPosition(connected);
    if (g_locked != was_locked) dispatch_async_f(MAIN_QUEUE, NULL, sync_cursor);
    return result;
}

typedef struct { const void *replacement; const void *original; } Interpose;
__attribute__((used)) static const Interpose g_interpose[] __attribute__((section("__DATA,__interpose"))) = {
    {(const void *)fixed_associate, (const void *)CGAssociateMouseAndMouseCursorPosition},
};

static BOOL accepts_first_mouse(id self, SEL cmd, id event) {
    (void)self; (void)cmd; (void)event;
    return 1;
}

static bool g_class_patched;

static void patch_view_class(void) {
    if (g_class_patched) return;
    Class cls = objc_getClass("NSView");
    if (cls) {
        class_replaceMethod(cls, SEL_("acceptsFirstMouse:"), (IMP)accepts_first_mouse, "c@:@");
        g_class_patched = true;
    }
}

static void install_click_fix(id app) {
    id windows = MSG(id)(app, SEL_("windows"));
    unsigned long count = windows ? MSG(unsigned long)(windows, SEL_("count")) : 0;
    if (count == 0) return;

    patch_view_class();

    id window = MSG(id)(app, SEL_("keyWindow"));
    if (!window) window = MSG(id, unsigned long)(windows, SEL_("objectAtIndex:"), 0);
    id view = window ? MSG(id)(window, SEL_("contentView")) : NULL;
    if (view) {
        class_replaceMethod(object_getClass(view), SEL_("acceptsFirstMouse:"), (IMP)accepts_first_mouse, "c@:@");
        Class viewClass = MSG(Class)(view, SEL_("class"));
        if (viewClass) class_replaceMethod(viewClass, SEL_("acceptsFirstMouse:"), (IMP)accepts_first_mouse, "c@:@");
    }

    Class event_class = objc_getClass("NSEvent");
    if (!event_class) return;

    // Click monitor: make the window key before the click reaches AppKit's
    // normal first-responder handling.
    id (^click_handler)(id) = ^id(id event) {
        id target = MSG(id)(event, SEL_("window"));
        if (target && !MSG(BOOL)(target, SEL_("isKeyWindow"))) {
            MSG(void)(target, SEL_("makeKeyAndOrderFront:"), (id)0);
            if (++g_click_fixes <= 5) note("made the game window key before a click (%u)", g_click_fixes);
        }
        return event;
    };
    MSG(id, uint64_t, id)((id)event_class, SEL_("addLocalMonitorForEventsMatchingMask:handler:"),
                          kMouseDownMask, (id)click_handler);
    g_monitor_installed = true;
    note("click fix installed (%u windows)", (unsigned)count);

    // Move monitor: every time the mouse moves while locked, force-hide again.
    // This catches the case where macOS re-shows the cursor on mouse movement
    // (some drivers / accessibility features do this).
    if (!g_move_monitor_installed) {
        id (^move_handler)(id) = ^id(id event) {
            if (g_locked && g_we_hid) {
                Class nscursor = objc_getClass("NSCursor");
                if (nscursor) {
                    MSG(void)((id)nscursor, SEL_("hide"));
                    MSG(void, BOOL)((id)nscursor, SEL_("setHiddenUntilMouseMoves:"), 1);
                }
                if (CGCursorIsVisible()) {
                    CGDisplayHideCursor(CGMainDisplayID());
                    g_cg_hide_depth++;
                    if (++g_force_hides <= 10) note("force-hid cursor on mouse move (%u)", g_force_hides);
                }
            }
            return event;
        };
        MSG(id, uint64_t, id)((id)event_class, SEL_("addLocalMonitorForEventsMatchingMask:handler:"),
                              kMouseMovedMask, (id)move_handler);
        g_move_monitor_installed = true;
    }
}

// Frame-rate readout for the client's overlay when the FPS unlocker isn't loaded
// (it logs the same line itself). Reads Unity's own frame counter; changes nothing.
static int (*g_frame_count)(void);
static bool g_measure_off;
static double g_last_t;
static double g_started;
static int g_last_frames;

static double now_seconds(void) {
    struct timespec_ ts;
    clock_gettime(CLOCK_MONOTONIC_, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static void measure_fps(void) {
    if (g_measure_off) return;
    if (g_frame_count == NULL) {
        void *(*corlib)(void) = (void *(*)(void))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_get_corlib");
        if (corlib == NULL || corlib() == NULL || now_seconds() - g_started < 15.0) return;
        void *(*resolve)(const char *) = (void *(*)(const char *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_resolve_icall");
        if (resolve == NULL) return;
        g_frame_count = (int (*)(void))resolve("UnityEngine.Time::get_frameCount");
        if (g_frame_count == NULL) { g_measure_off = true; return; }
        g_last_t = now_seconds();
        g_last_frames = g_frame_count();
        return;
    }
    double t = now_seconds();
    if (t - g_last_t < 5.0) return;
    int frames = g_frame_count();
    fprintf(stderr, "[PG3D Mouse Fix pid=%d] engine measurement: rendered=%.1f FPS, window=%.2fs.\n",
            getpid(), (double)(frames - g_last_frames) / (t - g_last_t), t - g_last_t);
    fflush(stderr);
    g_last_t = t;
    g_last_frames = frames;
}

static void tick(void *unused) {
    (void)unused;
    patch_view_class();
    id app = shared_app();
    if (app) {
        if (!g_monitor_installed) install_click_fix(app);
        bool active = MSG(BOOL)(app, SEL_("isActive"));
        if (active && !g_was_active && g_locked) kick_cursor();
        // While the game is active with cursor locked, force-hide on every tick.
        // Don't wait for CGCursorIsVisible() — it lies sometimes, and the cost of
        // a redundant NSCursor hide call is near zero.
        else if (active && g_locked && g_we_hid) {
            force_hide_cursor();
        }
        g_was_active = active;
        sync_cursor(NULL);
        measure_fps();
    }
    dispatch_after_f(dispatch_time(0, (int64_t)kTickMs * 1000000), MAIN_QUEUE, NULL, tick);
}

__attribute__((constructor)) static void start(void) {
    const char *flag = getenv("PG3D_MOUSEFIX");
    if (flag && strcmp(flag, "0") == 0) return;
    note("loaded (%u)", 1);
    CGEventSourceRef source = CGEventSourceCreate(0);
    if (source) {
        CGEventSourceSetLocalEventsSuppressionInterval(source, 0.0);
        note("event suppression interval zeroed (%u)", 1);
    }
    g_started = now_seconds();
    dispatch_after_f(dispatch_time(0, 1000LL * 1000000), MAIN_QUEUE, NULL, tick);
}
