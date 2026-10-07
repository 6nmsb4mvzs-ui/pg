// PG3D aimbot + triggerbot with in-game overlay.
// Injected via DYLD_INSERT_LIBRARIES alongside the FPS unlocker.
//
// v2 rewrite — the pthread in v1 silently died before logging. This version
// uses GCD (dispatch), the same timer mechanism the FPS unlocker uses
// successfully. Targets are found via CharacterController (a Unity engine
// type that is never obfuscated) instead of game-specific class names.
// Toggle feedback is shown in a transparent floating NSWindow overlay.
//
// Built without the macOS SDK (zig cc, -undefined dynamic_lookup, -fblocks).

#include <dispatch/dispatch.h>
#include <math.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

// ── ObjC runtime (no SDK) ──

typedef struct objc_object *id;
typedef struct objc_selector *SEL;
typedef struct objc_class *Class;

extern Class objc_getClass(const char *name);
extern SEL sel_registerName(const char *name);
extern void objc_msgSend(void);
extern void objc_msgSend_stret(void);

extern void *dlsym(void *handle, const char *symbol);
#define RTLD_DEFAULT_HANDLE ((void *)-2)

#define SEL_(n) sel_registerName(n)
#define MSG(ret, ...) ((ret (*)(id, SEL, ##__VA_ARGS__))objc_msgSend)
#define STRET(ret, ...) ((void (*)(ret *, id, SEL, ##__VA_ARGS__))objc_msgSend_stret)

// ── CoreGraphics ──

typedef struct { double x, y; } CGPoint_t;

extern bool CGEventSourceKeyState(int stateID, uint16_t keyCode);
extern bool CGEventSourceButtonState(int stateID, uint32_t button);
extern void *CGEventCreateMouseEvent(void *source, uint32_t mouseType,
                                     CGPoint_t cursorPos, uint32_t mouseButton);
extern void CGEventPost(uint32_t tap, void *event);
extern void CFRelease(void *cf);
extern unsigned char AXIsProcessTrusted(void);

#define kCGEventSourceStateCombinedSessionState 0
#define kCGEventLeftMouseDown 1
#define kCGEventLeftMouseUp   2
#define kCGHIDEventTap        0

// ── dyld ──

extern uint32_t _dyld_image_count(void);
extern const char *_dyld_get_image_name(uint32_t image_index);

// ── IL2CPP types ──

typedef void Il2CppObject;
typedef void Il2CppClass;
typedef void Il2CppImage;
typedef void Il2CppDomain;
typedef void Il2CppAssembly;
typedef void Il2CppType;

// ── IL2CPP API function pointers ──

static void  *(*p_resolve_icall)(const char *name);
static void  *(*p_get_corlib)(void);
static Il2CppDomain     *(*p_domain_get)(void);
static Il2CppAssembly  **(*p_domain_get_assemblies)(Il2CppDomain *, size_t *);
static Il2CppImage      *(*p_assembly_get_image)(Il2CppAssembly *);
static Il2CppClass      *(*p_class_from_name)(Il2CppImage *, const char *, const char *);
static Il2CppType       *(*p_class_get_type)(Il2CppClass *);

// ── Unity icall function pointers ──

static Il2CppObject *(*Camera_get_main)(void);
static Il2CppObject *(*Component_get_transform)(Il2CppObject *);
static void  (*Transform_get_position)(Il2CppObject *, float *);
static void  (*Transform_get_eulerAngles)(Il2CppObject *, float *);
static void  (*Transform_set_eulerAngles)(Il2CppObject *, float *);
static void  (*Transform_get_forward)(Il2CppObject *, float *);
static float (*Time_get_deltaTime)(void);

// ── Geometry ──

typedef struct { float x, y, z; } Vec3;
typedef struct { double x, y, w, h; } NSRect_t;

// ── Config ──

enum {
    kInitRetryMs   = 500,
    kInitMaxRetries = 120,     // 60 s of retries
    kTickHz         = 60,
    kMaxTargets     = 32,
    kAimSmoothing   = 8,
    kOverlayMs      = 2000,
    kTriggerDot     = 998,     // cos(~3.6 deg) × 1000
};

// ── State ──

static bool g_aimbot_enabled;
static bool g_triggerbot_enabled;
static volatile bool g_aimbot_active;
static volatile bool g_triggerbot_active;

static int g_aim_key_type, g_aim_key_code;
static int g_trig_key_type, g_trig_key_code;

static bool g_ga_found;
static bool g_icalls_ready;
static bool g_ready;
static bool g_has_accessibility;
static unsigned g_init_attempts;

static Il2CppClass *g_target_class;
static void *(*g_type_get_object)(void *);
static void *(*g_find_of_type)(void *);

static dispatch_source_t g_tick_timer;
static dispatch_source_t g_dismiss_timer;
static id g_overlay_window;
static id g_overlay_label;
static bool g_overlay_created;
static bool g_overlay_failed;

// ── Logging ──

static void note(const char *fmt, ...) {
    fprintf(stderr, "[PG3D Cheats pid=%d] ", getpid());
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
}

// ── Key parsing ──

static void parse_key(const char *str, int *out_type, int *out_code) {
    *out_type = 0;
    *out_code = -1;
    if (!str || !*str) return;
    if (strcmp(str, "mouse4") == 0)  { *out_type = 1; *out_code = 3; return; }
    if (strcmp(str, "mouse5") == 0)  { *out_type = 1; *out_code = 4; return; }
    if (strcmp(str, "mouse3") == 0)  { *out_type = 1; *out_code = 2; return; }
    if (strcmp(str, "mouse1") == 0)  { *out_type = 1; *out_code = 0; return; }
    if (strcmp(str, "mouse2") == 0)  { *out_type = 1; *out_code = 1; return; }
    if (strlen(str) == 1) {
        char c = str[0];
        static const uint16_t alpha[] = {
            0,11,8,2,14,3,5,4,34,38,40,37,46,45,31,35,12,15,1,17,32,9,13,7,16,6
        };
        if (c >= 'a' && c <= 'z') { *out_type = 0; *out_code = alpha[c - 'a']; return; }
        if (c >= 'A' && c <= 'Z') { *out_type = 0; *out_code = alpha[c - 'A']; return; }
    }
    if (str[0] == 'f' || str[0] == 'F') {
        int n = atoi(str + 1);
        static const uint16_t fkeys[] = {0,122,120,99,118,96,97,98,100,101,109,103,111,105,107};
        if (n >= 1 && n <= 14) { *out_type = 0; *out_code = fkeys[n]; return; }
    }
    if (strcmp(str, "space")    == 0) { *out_type = 0; *out_code = 49; return; }
    if (strcmp(str, "shift")    == 0) { *out_type = 0; *out_code = 56; return; }
    if (strcmp(str, "control")  == 0) { *out_type = 0; *out_code = 59; return; }
    if (strcmp(str, "alt")      == 0) { *out_type = 0; *out_code = 58; return; }
    if (strcmp(str, "capslock") == 0) { *out_type = 0; *out_code = 57; return; }
    if (strcmp(str, "tab")      == 0) { *out_type = 0; *out_code = 48; return; }
}

static bool key_is_down(int type, int code) {
    if (code < 0) return false;
    if (type == 1) {
        Class cls = objc_getClass("NSEvent");
        if (cls) {
            unsigned long buttons = ((unsigned long (*)(id, SEL))objc_msgSend)(
                (id)cls, sel_registerName("pressedMouseButtons"));
            return (buttons >> code) & 1;
        }
        return CGEventSourceButtonState(kCGEventSourceStateCombinedSessionState, (uint32_t)code);
    }
    return CGEventSourceKeyState(kCGEventSourceStateCombinedSessionState, (uint16_t)code);
}

// ── In-game overlay ──

static void dismiss_overlay(void) {
    if (g_overlay_window)
        MSG(void, id)(g_overlay_window, SEL_("orderOut:"), (id)0);
}

static void ensure_overlay(void) {
    if (g_overlay_created || g_overlay_failed) return;

    Class WinCls  = objc_getClass("NSWindow");
    Class TFCls   = objc_getClass("NSTextField");
    Class ColCls  = objc_getClass("NSColor");
    Class FntCls  = objc_getClass("NSFont");
    Class ScrCls  = objc_getClass("NSScreen");
    if (!WinCls || !TFCls || !ColCls || !FntCls) {
        g_overlay_failed = true;
        return;
    }

    NSRect_t scr = {0, 0, 1920, 1080};
    if (ScrCls) {
        id ms = MSG(id)((id)ScrCls, SEL_("mainScreen"));
        if (ms) STRET(NSRect_t)(&scr, ms, SEL_("frame"));
    }

    double ww = 260, wh = 34;
    NSRect_t wf = { (scr.w - ww) / 2, scr.y + scr.h - wh - 80, ww, wh };

    id w = MSG(id)((id)WinCls, SEL_("alloc"));
    w = ((id (*)(id, SEL, NSRect_t, unsigned long, unsigned long, int))objc_msgSend)(
        w, SEL_("initWithContentRect:styleMask:backing:defer:"), wf, 0UL, 2UL, 0);
    if (!w) { g_overlay_failed = true; return; }

    id bg = MSG(id, double, double, double, double)(
        (id)ColCls, SEL_("colorWithCalibratedRed:green:blue:alpha:"),
        0.05, 0.05, 0.1, 0.82);
    MSG(void, id)(w, SEL_("setBackgroundColor:"), bg);
    MSG(void, int)(w, SEL_("setOpaque:"), 0);
    MSG(void, long)(w, SEL_("setLevel:"), 1000);
    MSG(void, int)(w, SEL_("setIgnoresMouseEvents:"), 1);
    MSG(void, int)(w, SEL_("setHasShadow:"), 0);
    // CanJoinAllSpaces | Transient | IgnoresCycle | FullScreenAuxiliary
    MSG(void, unsigned long)(w, SEL_("setCollectionBehavior:"), 1UL | 8UL | 64UL | 256UL);

    NSRect_t lf = {0, 2, ww, wh - 2};
    id lbl = MSG(id)((id)TFCls, SEL_("alloc"));
    lbl = ((id (*)(id, SEL, NSRect_t))objc_msgSend)(lbl, SEL_("initWithFrame:"), lf);
    MSG(void, int)(lbl, SEL_("setEditable:"), 0);
    MSG(void, int)(lbl, SEL_("setBezeled:"), 0);
    MSG(void, int)(lbl, SEL_("setDrawsBackground:"), 0);
    MSG(void, long)(lbl, SEL_("setAlignment:"), 2); // center
    id font = MSG(id, double)((id)FntCls, SEL_("boldSystemFontOfSize:"), 15.0);
    MSG(void, id)(lbl, SEL_("setFont:"), font);

    id cv = MSG(id)(w, SEL_("contentView"));
    MSG(void, id)(cv, SEL_("addSubview:"), lbl);

    g_overlay_window = w;
    g_overlay_label = lbl;
    g_overlay_created = true;
}

static void overlay_set_text(const char *text, bool on) {
    if (!g_overlay_label) return;
    Class SCls = objc_getClass("NSString");
    Class CCls = objc_getClass("NSColor");
    if (!SCls || !CCls) return;

    id str = MSG(id, const char *)((id)SCls, SEL_("stringWithUTF8String:"), text);
    MSG(void, id)(g_overlay_label, SEL_("setStringValue:"), str);

    id col = on
        ? MSG(id, double, double, double, double)(
              (id)CCls, SEL_("colorWithCalibratedRed:green:blue:alpha:"),
              0.3, 1.0, 0.4, 1.0)
        : MSG(id, double, double, double, double)(
              (id)CCls, SEL_("colorWithCalibratedRed:green:blue:alpha:"),
              1.0, 0.35, 0.3, 1.0);
    MSG(void, id)(g_overlay_label, SEL_("setTextColor:"), col);
}

static void show_game_msg(const char *text, bool on) {
    dispatch_async(dispatch_get_main_queue(), ^{
        ensure_overlay();
        if (g_overlay_failed) {
            char cmd[256];
            snprintf(cmd, sizeof(cmd),
                "/usr/bin/osascript -e 'display notification \"%s\" with title \"PG3D\"'"
                " >/dev/null 2>&1 &", text);
            system(cmd);
            return;
        }
        overlay_set_text(text, on);
        MSG(void)(g_overlay_window, SEL_("orderFrontRegardless"));

        if (g_dismiss_timer) dispatch_source_cancel(g_dismiss_timer);
        g_dismiss_timer = dispatch_source_create(
            DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
        dispatch_source_set_timer(g_dismiss_timer,
            dispatch_time(DISPATCH_TIME_NOW, (int64_t)kOverlayMs * NSEC_PER_MSEC),
            DISPATCH_TIME_FOREVER, 0);
        dispatch_source_set_event_handler(g_dismiss_timer, ^{ dismiss_overlay(); });
        dispatch_resume(g_dismiss_timer);
    });
}

// ── IL2CPP bootstrap ──

static bool find_game_assembly(void) {
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; i++) {
        const char *name = _dyld_get_image_name(i);
        if (!name || !strstr(name, "/GameAssembly.dylib")) continue;

        p_resolve_icall = (void *(*)(const char *))
            dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_resolve_icall");
        if (!p_resolve_icall) return false;

        p_get_corlib            = dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_get_corlib");
        p_domain_get            = dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_domain_get");
        p_domain_get_assemblies = dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_domain_get_assemblies");
        p_assembly_get_image    = dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_assembly_get_image");
        p_class_from_name       = dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_class_from_name");
        p_class_get_type        = dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_class_get_type");
        g_type_get_object       = dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_type_get_object");

        note("GameAssembly found: %s", name);
        note("  resolve_icall=%p domain_get=%p class_from_name=%p type_get_object=%p",
             (void *)p_resolve_icall, (void *)p_domain_get,
             (void *)p_class_from_name, (void *)g_type_get_object);
        return true;
    }
    return false;
}

// ── Unity icall resolution ──

static void *try_icall(const char *a, const char *b) {
    void *r = p_resolve_icall(a);
    if (!r && b) r = p_resolve_icall(b);
    return r;
}

static bool resolve_unity_icalls(void) {
    if (!p_resolve_icall) return false;
    if (p_get_corlib && p_get_corlib() == NULL) return false;

    Camera_get_main = (void *)try_icall(
        "UnityEngine.Camera::get_main()",
        "UnityEngine.Camera::get_main");
    Component_get_transform = (void *)try_icall(
        "UnityEngine.Component::get_transform()",
        "UnityEngine.Component::get_transform");
    Time_get_deltaTime = (void *)try_icall(
        "UnityEngine.Time::get_deltaTime()",
        "UnityEngine.Time::get_deltaTime");

    Transform_get_position = (void *)try_icall(
        "UnityEngine.Transform::get_position_Injected(UnityEngine.Vector3&)",
        "UnityEngine.Transform::INTERNAL_get_position(UnityEngine.Vector3&)");
    Transform_get_eulerAngles = (void *)try_icall(
        "UnityEngine.Transform::get_eulerAngles_Injected(UnityEngine.Vector3&)",
        "UnityEngine.Transform::INTERNAL_get_eulerAngles(UnityEngine.Vector3&)");
    Transform_set_eulerAngles = (void *)try_icall(
        "UnityEngine.Transform::set_eulerAngles_Injected(UnityEngine.Vector3&)",
        "UnityEngine.Transform::INTERNAL_set_eulerAngles(UnityEngine.Vector3&)");
    Transform_get_forward = (void *)try_icall(
        "UnityEngine.Transform::get_forward_Injected(UnityEngine.Vector3&)",
        "UnityEngine.Transform::INTERNAL_get_forward(UnityEngine.Vector3&)");

    g_find_of_type = (void *)try_icall(
        "UnityEngine.Object::FindObjectsOfType(System.Type)",
        "UnityEngine.Object::FindObjectsOfType(System.Type,System.Boolean)");

    bool ok = Camera_get_main && Component_get_transform
           && Transform_get_position && Transform_get_eulerAngles
           && Transform_set_eulerAngles;

    note("icalls: cam=%p tf=%p pos=%p euler=%p set_euler=%p fwd=%p fot=%p → %s",
         (void *)Camera_get_main, (void *)Component_get_transform,
         (void *)Transform_get_position, (void *)Transform_get_eulerAngles,
         (void *)Transform_set_eulerAngles, (void *)Transform_get_forward,
         (void *)g_find_of_type, ok ? "OK" : "incomplete");
    return ok;
}

// ── Target class discovery ──
// CharacterController is a Unity engine type in UnityEngine.PhysicsModule — it
// is never obfuscated regardless of game build.  Every player character in PG3D
// (and most Unity FPS games) carries one.  We search every loaded assembly image
// so we hit the engine module, not just the game assembly.

static bool find_target_class(void) {
    if (g_target_class) return true;
    if (!p_domain_get || !p_domain_get_assemblies || !p_assembly_get_image ||
        !p_class_from_name)
        return false;

    Il2CppDomain *dom = p_domain_get();
    if (!dom) return false;
    size_t asm_count = 0;
    Il2CppAssembly **asms = p_domain_get_assemblies(dom, &asm_count);
    if (!asms) return false;

    static const char *types[][2] = {
        { "UnityEngine", "CharacterController" },
        { "UnityEngine", "Animator" },
        { NULL, NULL },
    };

    for (const char *(*t)[2] = types; (*t)[0]; t++) {
        for (size_t i = 0; i < asm_count; i++) {
            Il2CppImage *img = p_assembly_get_image(asms[i]);
            if (!img) continue;
            Il2CppClass *cls = p_class_from_name(img, (*t)[0], (*t)[1]);
            if (cls) {
                g_target_class = cls;
                note("target class: %s.%s (asm %zu/%zu)", (*t)[0], (*t)[1], i, asm_count);
                return true;
            }
        }
    }
    note("target class not found in %zu assemblies", asm_count);
    return false;
}

// ── Target enumeration ──
// Returns world-space positions of enemy characters, with the local player
// filtered out (closest to camera, within 3 m).

static int get_targets(Vec3 cam_pos, Vec3 *out, int max_n) {
    if (!g_target_class || !p_class_get_type || !g_type_get_object || !g_find_of_type)
        return 0;
    Il2CppType *type = p_class_get_type(g_target_class);
    if (!type) return 0;
    void *rtype = g_type_get_object(type);
    if (!rtype) return 0;
    void *arr = g_find_of_type(rtype);
    if (!arr) return 0;

    // IL2CPP array: 16-byte header + 8-byte bounds + 8-byte length + elements
    uint8_t *raw = (uint8_t *)arr;
    size_t len = *(size_t *)(raw + 24);
    if (len == 0 || len > (size_t)kMaxTargets * 2) return 0;
    Il2CppObject **elems = (Il2CppObject **)(raw + 32);

    int n = 0;
    for (size_t i = 0; i < len && n < max_n; i++) {
        if (!elems[i]) continue;
        Il2CppObject *tf = Component_get_transform(elems[i]);
        if (!tf) continue;
        Vec3 pos;
        Transform_get_position(tf, (float *)&pos);
        float dx = pos.x - cam_pos.x, dy = pos.y - cam_pos.y, dz = pos.z - cam_pos.z;
        if (dx*dx + dy*dy + dz*dz < 9.0f) continue; // local player (<3 m)
        pos.y += 1.5f; // head height
        out[n++] = pos;
    }
    return n;
}

// ── Vector math ──

static float vec3_dist_sq(Vec3 a, Vec3 b) {
    float dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    return dx*dx + dy*dy + dz*dz;
}

static Vec3 vec3_sub(Vec3 a, Vec3 b) {
    return (Vec3){ a.x - b.x, a.y - b.y, a.z - b.z };
}

static Vec3 vec3_norm(Vec3 v) {
    float len = sqrtf(v.x*v.x + v.y*v.y + v.z*v.z);
    if (len < 0.0001f) return (Vec3){0, 0, 1};
    return (Vec3){ v.x/len, v.y/len, v.z/len };
}

static float vec3_dot(Vec3 a, Vec3 b) {
    return a.x*b.x + a.y*b.y + a.z*b.z;
}

static float deg(float r) { return r * (180.0f / 3.14159265f); }

static Vec3 aim_angles(Vec3 from, Vec3 to) {
    Vec3 d = vec3_sub(to, from);
    float h = sqrtf(d.x*d.x + d.z*d.z);
    return (Vec3){ -deg(atan2f(d.y, h)), deg(atan2f(d.x, d.z)), 0 };
}

static float lerp_angle(float from, float to, float t) {
    float d = to - from;
    while (d >  180.0f) d -= 360.0f;
    while (d < -180.0f) d += 360.0f;
    return from + d * t;
}

// ── Click simulation ──

static void simulate_click(void) {
    CGPoint_t c = {0, 0};
    void *down = CGEventCreateMouseEvent(NULL, kCGEventLeftMouseDown, c, 0);
    void *up   = CGEventCreateMouseEvent(NULL, kCGEventLeftMouseUp,   c, 0);
    if (down) { CGEventPost(kCGHIDEventTap, down); CFRelease(down); }
    if (up)   { CGEventPost(kCGHIDEventTap, up);   CFRelease(up);   }
}

// ── Tick (runs ~60 Hz on a GCD global queue) ──

static bool g_aim_was_down;
static bool g_trig_was_down;
static bool g_logged_first_tick;

static void cheats_tick(void) {
    // ── toggle detection (rising edge) ──
    if (g_aimbot_enabled) {
        bool down = key_is_down(g_aim_key_type, g_aim_key_code);
        if (down && !g_aim_was_down) {
            g_aimbot_active = !g_aimbot_active;
            note("aimbot %s", g_aimbot_active ? "ON" : "OFF");
            show_game_msg(g_aimbot_active ? "AIMBOT ON" : "AIMBOT OFF", g_aimbot_active);
        }
        g_aim_was_down = down;
    }
    if (g_triggerbot_enabled) {
        bool down = key_is_down(g_trig_key_type, g_trig_key_code);
        if (down && !g_trig_was_down) {
            g_triggerbot_active = !g_triggerbot_active;
            note("triggerbot %s", g_triggerbot_active ? "ON" : "OFF");
            show_game_msg(g_triggerbot_active ? "TRIGGERBOT ON" : "TRIGGERBOT OFF",
                          g_triggerbot_active);
        }
        g_trig_was_down = down;
    }

    if (!g_aimbot_active && !g_triggerbot_active) return;

    // ── camera ──
    Il2CppObject *cam = Camera_get_main ? Camera_get_main() : NULL;
    if (!cam) return;
    Il2CppObject *cam_tf = Component_get_transform ? Component_get_transform(cam) : NULL;
    if (!cam_tf) return;

    Vec3 cam_pos;
    Transform_get_position(cam_tf, (float *)&cam_pos);

    if (!g_logged_first_tick) {
        note("first tick — camera at (%.1f, %.1f, %.1f)", cam_pos.x, cam_pos.y, cam_pos.z);
        g_logged_first_tick = true;
    }

    // ── get enemy positions ──
    Vec3 targets[kMaxTargets];
    int target_count = get_targets(cam_pos, targets, kMaxTargets);

    // ── aimbot: smooth aim at nearest target ──
    if (g_aimbot_active && target_count > 0 && Transform_set_eulerAngles) {
        float best_dist = 1e30f;
        int best = -1;
        for (int i = 0; i < target_count; i++) {
            float d = vec3_dist_sq(cam_pos, targets[i]);
            if (d < best_dist) { best_dist = d; best = i; }
        }
        if (best >= 0) {
            Vec3 goal = aim_angles(cam_pos, targets[best]);
            Vec3 cur;
            Transform_get_eulerAngles(cam_tf, (float *)&cur);
            float dt = Time_get_deltaTime ? Time_get_deltaTime() : 0.016f;
            float s = 1.0f - expf(-dt * kAimSmoothing);
            Vec3 aim = {
                lerp_angle(cur.x, goal.x, s),
                lerp_angle(cur.y, goal.y, s),
                0
            };
            Transform_set_eulerAngles(cam_tf, (float *)&aim);
        }
    }

    // ── triggerbot: fire when aiming near a target ──
    if (g_triggerbot_active && target_count > 0 && Transform_get_forward) {
        Vec3 fwd;
        Transform_get_forward(cam_tf, (float *)&fwd);
        for (int i = 0; i < target_count; i++) {
            Vec3 dir = vec3_norm(vec3_sub(targets[i], cam_pos));
            float dot = vec3_dot(fwd, dir);
            float dist_sq = vec3_dist_sq(cam_pos, targets[i]);
            if (dot > (float)kTriggerDot / 1000.0f && dist_sq < 10000.0f) {
                simulate_click();
                break;
            }
        }
    }
}

// ── Init (GCD-based retry, fires every 500 ms until ready) ──

static void start_tick_timer(void) {
    g_tick_timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,
        dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_HIGH, 0));
    dispatch_source_set_timer(g_tick_timer, DISPATCH_TIME_NOW,
        (uint64_t)(NSEC_PER_SEC / kTickHz), NSEC_PER_MSEC);
    dispatch_source_set_event_handler(g_tick_timer, ^{ cheats_tick(); });
    dispatch_resume(g_tick_timer);
    note("tick timer started (%d Hz)", kTickHz);
}

static void cheats_init_attempt(void);

static void schedule_retry(void) {
    dispatch_after(
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)kInitRetryMs * NSEC_PER_MSEC),
        dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0),
        ^{ cheats_init_attempt(); });
}

static void cheats_init_attempt(void) {
    g_init_attempts++;

    if (g_init_attempts > (unsigned)kInitMaxRetries) {
        note("gave up after %u init attempts — cheats inactive", g_init_attempts);
        show_game_msg("CHEATS FAILED", false);
        return;
    }

    if (!g_ga_found) {
        g_ga_found = find_game_assembly();
        if (!g_ga_found) {
            if (g_init_attempts <= 3 || g_init_attempts % 20 == 0)
                note("init %u: GameAssembly not loaded yet", g_init_attempts);
            schedule_retry();
            return;
        }
    }

    if (!g_icalls_ready) {
        g_icalls_ready = resolve_unity_icalls();
        if (!g_icalls_ready) {
            if (g_init_attempts <= 3 || g_init_attempts % 20 == 0)
                note("init %u: icalls not ready", g_init_attempts);
            schedule_retry();
            return;
        }
    }

    if (!g_target_class) {
        find_target_class();
        if (!g_target_class) {
            if (g_init_attempts <= 5 || g_init_attempts % 20 == 0)
                note("init %u: target class not found", g_init_attempts);
            schedule_retry();
            return;
        }
    }

    g_ready = true;
    g_has_accessibility = AXIsProcessTrusted();

    note("READY — aim:%s trig:%s targets:%s accessibility:%s",
         g_aimbot_active  ? "ON" : "off",
         g_triggerbot_active ? "ON" : "off",
         g_target_class ? "CharacterController" : "none",
         g_has_accessibility ? "yes" : "no (triggerbot click may not work)");

    show_game_msg("CHEATS READY", true);
    start_tick_timer();
}

// ── Entry ──

__attribute__((constructor))
static void start_cheats(void) {
    const char *flag = getenv("PG3D_CHEATS");
    if (!flag || strcmp(flag, "0") == 0) return;

    const char *aim  = getenv("PG3D_CHEATS_AIMBOT");
    const char *trig = getenv("PG3D_CHEATS_TRIGGERBOT");
    g_aimbot_enabled    = aim  && strcmp(aim,  "1") == 0;
    g_triggerbot_enabled = trig && strcmp(trig, "1") == 0;
    if (!g_aimbot_enabled && !g_triggerbot_enabled) return;

    g_aimbot_active    = g_aimbot_enabled;
    g_triggerbot_active = g_triggerbot_enabled;

    parse_key(getenv("PG3D_CHEATS_AIMBOT_KEY"),    &g_aim_key_type,  &g_aim_key_code);
    parse_key(getenv("PG3D_CHEATS_TRIGGERBOT_KEY"), &g_trig_key_type, &g_trig_key_code);
    if (g_aim_key_code  < 0) { g_aim_key_type  = 1; g_aim_key_code  = 3; }
    if (g_trig_key_code < 0) { g_trig_key_type = 1; g_trig_key_code = 4; }

    note("loaded — aimbot:%s (type=%d code=%d) triggerbot:%s (type=%d code=%d)",
         g_aimbot_enabled  ? "ON" : "off", g_aim_key_type,  g_aim_key_code,
         g_triggerbot_enabled ? "ON" : "off", g_trig_key_type, g_trig_key_code);

    // Schedule init with 2 s delay (same GCD pattern the FPS unlocker uses)
    dispatch_after(
        dispatch_time(DISPATCH_TIME_NOW, 2LL * NSEC_PER_SEC),
        dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0),
        ^{ cheats_init_attempt(); });
}
