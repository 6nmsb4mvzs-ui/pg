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

extern int32_t CGAssociateMouseAndMouseCursorPosition(uint32_t connected);
extern int32_t CGDisplayHideCursor(uint32_t display);
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
static bool g_was_active;
static bool g_monitor_installed;
static unsigned g_click_fixes;
static unsigned g_kicks;

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

static void tick(void *unused) {
    (void)unused;
    id app = shared_app();
    if (app) {
        if (!g_monitor_installed) install_click_fix(app);
        bool active = MSG(BOOL)(app, SEL_("isActive"));
        if (active && !g_was_active && g_locked) kick_cursor();
        g_was_active = active;
        sync_cursor(NULL);
    }
    dispatch_after_f(dispatch_time(0, (int64_t)kTickMs * 1000000), MAIN_QUEUE, NULL, tick);
}

__attribute__((constructor)) static void start(void) {
    const char *flag = getenv("PG3D_MOUSEFIX");
    if (flag && strcmp(flag, "0") == 0) return;
    note("loaded (%u)", 1);
    // The main queue only runs once the app's run loop is up.
    dispatch_after_f(dispatch_time(0, 1000LL * 1000000), MAIN_QUEUE, NULL, tick);
}
