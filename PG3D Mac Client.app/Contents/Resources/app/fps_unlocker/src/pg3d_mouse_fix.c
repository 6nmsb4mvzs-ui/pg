// PG3D mouse fix. PG3D Mac Client loads this into Pixel Gun 3D (DYLD_INSERT_LIBRARIES).
// It works around two macOS input bugs in the game itself:
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
extern int32_t CGCursorIsVisible(void);  // deprecated but still answers on current macOS
extern int32_t CGDisplayShowCursor(uint32_t display);
extern uint32_t CGMainDisplayID(void);

extern char _dispatch_main_q;
extern void dispatch_async_f(void *queue, void *context, void (*work)(void *));
extern void dispatch_after_f(uint64_t when, void *queue, void *context, void (*work)(void *));
extern uint64_t dispatch_time(uint64_t when, int64_t delta);

#define MAIN_QUEUE ((void *)&_dispatch_main_q)
#define SEL_(name) sel_registerName(name)
#define MSG(ret, ...) ((ret (*)(id, SEL, ##__VA_ARGS__))objc_msgSend)

enum { kTickMs = 300 };
static const uint64_t kMouseDownMask = (1ULL << 1) | (1ULL << 3) | (1ULL << 25);

static volatile bool g_locked;       // the game disassociated the mouse (cursor locked)
static bool g_we_hid;                // we hold one CGDisplayHideCursor of our own
static unsigned g_extra_hides;       // extra hides needed to beat a stuck show count
static bool g_was_active;
static bool g_monitor_installed;
static unsigned g_click_fixes;
static unsigned g_kicks;
static unsigned g_mid_game_fixes;

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

// Keep the system cursor hidden while the game has it locked and PG3D is in front.
static void sync_cursor(void *unused) {
    (void)unused;
    id app = shared_app();
    bool active = app && MSG(BOOL)(app, SEL_("isActive"));
    bool want = g_locked && active;
    if (want && !g_we_hid) {
        CGDisplayHideCursor(CGMainDisplayID());
        g_we_hid = true;
    } else if (!want && g_we_hid) {
        CGDisplayShowCursor(CGMainDisplayID());
        for (; g_extra_hides > 0; --g_extra_hides) CGDisplayShowCursor(CGMainDisplayID());
        g_we_hid = false;
    }
}

// Re-sync WindowServer's hide state when PG3D comes back to the front: a balanced
// show+hide pair, which is what Cmd-Tab out and in does for the user.
static void kick_cursor(void) {
    uint32_t display = CGMainDisplayID();
    CGDisplayShowCursor(display);
    CGDisplayHideCursor(display);
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

// A click on a window that lost key status first makes it key, then goes through.
static void install_click_fix(id app) {
    id windows = MSG(id)(app, SEL_("windows"));
    unsigned long count = windows ? MSG(unsigned long)(windows, SEL_("count")) : 0;
    if (count == 0) return;
    id window = MSG(id)(app, SEL_("keyWindow"));
    if (!window) window = MSG(id, unsigned long)(windows, SEL_("objectAtIndex:"), 0);
    id view = window ? MSG(id)(window, SEL_("contentView")) : NULL;
    if (view) class_replaceMethod(object_getClass(view), SEL_("acceptsFirstMouse:"), (IMP)accepts_first_mouse, "c@:@");

    Class event_class = objc_getClass("NSEvent");
    if (!event_class) return;
    id (^handler)(id) = ^id(id event) {
        id target = MSG(id)(event, SEL_("window"));
        if (target && !MSG(BOOL)(target, SEL_("isKeyWindow"))) {
            MSG(void)(target, SEL_("makeKeyWindow"));
            if (++g_click_fixes <= 5) note("made the game window key before a click (%u)", g_click_fixes);
        }
        return event;
    };
    MSG(id, uint64_t, id)((id)event_class, SEL_("addLocalMonitorForEventsMatchingMask:handler:"),
                          kMouseDownMask, (id)handler);
    g_monitor_installed = true;
    note("click fix installed (%u windows)", (unsigned)count);
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
        // Never call into IL2CPP before it's initialised (that crashes the game at
        // launch): its core library pointer stays NULL until then.
        void *(*corlib)(void) = (void *(*)(void))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_get_corlib");
        if (corlib == NULL || corlib() == NULL || now_seconds() - g_started < 15.0) return;
        void *(*resolve)(const char *) = (void *(*)(const char *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_resolve_icall");
        if (resolve == NULL) return;  // GameAssembly not loaded yet
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
    id app = shared_app();
    if (app) {
        if (!g_monitor_installed) install_click_fix(app);
        bool active = MSG(BOOL)(app, SEL_("isActive"));
        if (active && !g_was_active && g_locked) kick_cursor();
        // Mid-game, without any app switch, macOS sometimes shows its arrow over the
        // locked game (it isn't in recordings: it's the system cursor). Re-hide it.
        else if (active && g_locked && g_we_hid && CGCursorIsVisible()) {
            uint32_t display = CGMainDisplayID();
            CGDisplayShowCursor(display);
            CGDisplayHideCursor(display);
            if (!CGCursorIsVisible() && ++g_mid_game_fixes <= 20) {
                note("re-hid the system cursor that appeared mid-game (%u)", g_mid_game_fixes);
            } else if (CGCursorIsVisible() && g_extra_hides < 3) {
                CGDisplayHideCursor(display);  // one more hide, balanced when the lock ends
                g_extra_hides++;
            }
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
    // With the FPS unlocker loaded too, it reports the frame rate itself.
    const char *unlock = getenv("PG3D_UNLOCK_FPS");
    g_measure_off = unlock != NULL && strcmp(unlock, "-1") != 0;
    g_started = now_seconds();
    // The main queue only runs once the app's run loop is up.
    dispatch_after_f(dispatch_time(0, 1000LL * 1000000), MAIN_QUEUE, NULL, tick);
}
