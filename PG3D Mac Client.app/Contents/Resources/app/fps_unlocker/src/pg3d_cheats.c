// PG3D aimbot + triggerbot. Injected via DYLD_INSERT_LIBRARIES alongside the
// FPS unlocker and mouse fix. Reads PG3D_CHEATS_AIMBOT, PG3D_CHEATS_TRIGGERBOT
// (each "1" to enable) and PG3D_CHEATS_AIMBOT_KEY / PG3D_CHEATS_TRIGGERBOT_KEY
// (CGKeyCode or "mouse4"/"mouse5"). Toggle in-game with the bound key.
//
// Built without the macOS SDK (zig cc, -undefined dynamic_lookup).
// Uses a background pthread instead of GCD — avoids block-runtime issues
// in cross-compiled dylibs where dispatch timers silently fail to fire.

#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <math.h>
#include <pthread.h>

// ── ObjC / CoreGraphics externs (no SDK) ──

typedef struct objc_object *id;
typedef struct objc_selector *SEL;
typedef struct objc_class *Class;
typedef void (*IMP)(void);
typedef signed char BOOL;

extern Class objc_getClass(const char *name);
extern SEL sel_registerName(const char *name);
extern void objc_msgSend(void);
extern Class object_getClass(id object);

extern void *dlsym(void *handle, const char *symbol);
#define RTLD_DEFAULT_HANDLE ((void *)-2)

extern bool CGEventSourceKeyState(int stateID, uint16_t keyCode);
extern bool CGEventSourceButtonState(int stateID, uint32_t button);
#define kCGEventSourceStateCombinedSessionState 0

#define SEL_(name) sel_registerName(name)
#define MSG(ret, ...) ((ret (*)(id, SEL, ##__VA_ARGS__))objc_msgSend)

// ── Mach-O / dyld ──

extern uint32_t _dyld_image_count(void);
extern const void *_dyld_get_image_header(uint32_t image_index);
extern const char *_dyld_get_image_name(uint32_t image_index);
extern intptr_t _dyld_get_image_vmaddr_slide(uint32_t image_index);

// ── IL2CPP types ──

typedef void Il2CppObject;
typedef void Il2CppClass;
typedef void Il2CppImage;
typedef void Il2CppDomain;
typedef void Il2CppAssembly;
typedef void Il2CppMethodInfo;
typedef void Il2CppType;
typedef void Il2CppArray;

// ── IL2CPP API function pointers ──

static void *(*il2cpp_resolve_icall)(const char *name);
static void *(*il2cpp_get_corlib)(void);
static Il2CppDomain *(*il2cpp_domain_get)(void);
static Il2CppAssembly **(*il2cpp_domain_get_assemblies)(Il2CppDomain *domain, size_t *count);
static Il2CppImage *(*il2cpp_assembly_get_image)(Il2CppAssembly *assembly);
static Il2CppClass *(*il2cpp_class_from_name)(Il2CppImage *image, const char *ns, const char *name);
static const Il2CppMethodInfo *(*il2cpp_class_get_method_from_name)(Il2CppClass *klass, const char *name, int param_count);
static Il2CppObject *(*il2cpp_runtime_invoke)(const Il2CppMethodInfo *method, void *obj, void **params, Il2CppObject **exc);
static Il2CppType *(*il2cpp_class_get_type)(Il2CppClass *klass);
static Il2CppClass *(*il2cpp_object_get_class)(Il2CppObject *obj);
static const char *(*il2cpp_class_get_name)(Il2CppClass *klass);
static Il2CppClass *(*il2cpp_class_get_parent)(Il2CppClass *klass);
static size_t (*il2cpp_image_get_class_count)(Il2CppImage *image);
static Il2CppClass *(*il2cpp_image_get_class)(Il2CppImage *image, size_t index);
static const char *(*il2cpp_class_get_namespace)(Il2CppClass *klass);

// Unity icall function pointers
static Il2CppObject *(*Camera_get_main)(void);
static Il2CppObject *(*Component_get_transform)(Il2CppObject *self);
static void (*Transform_get_position)(Il2CppObject *self, float *out);
static void (*Transform_set_position)(Il2CppObject *self, float *value);
static void (*Transform_get_eulerAngles)(Il2CppObject *self, float *out);
static void (*Transform_set_eulerAngles)(Il2CppObject *self, float *value);
static void (*Transform_get_forward)(Il2CppObject *self, float *out);
static int (*Time_get_frameCount)(void);
static float (*Time_get_deltaTime)(void);

// ── config ──

enum {
    kTickUs          = 16000,   // ~60 Hz poll (microseconds)
    kRetryUs         = 500000,  // 500 ms retry interval
    kInitDelayUs     = 2000000, // 2 s initial wait
    kRetryLimit      = 240,     // 2 minutes of retries
    kMaxPlayers      = 32,
    kAimSmoothing    = 8,       // lower = snappier
};

static bool g_aimbot_enabled;
static bool g_triggerbot_enabled;
static volatile bool g_aimbot_active;
static volatile bool g_triggerbot_active;
static volatile bool g_il2cpp_ready;

static int g_aimbot_key_type;    // 0 = keyboard, 1 = mouse
static int g_aimbot_key_code;
static int g_triggerbot_key_type;
static int g_triggerbot_key_code;

// ── logging ──

static void note(const char *fmt, ...) {
    fprintf(stderr, "[PG3D Cheats pid=%d] ", getpid());
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
}

// ── macOS notification (same pattern as the client's launch.sh notify) ──

static void toast(const char *message) {
    char cmd[512];
    snprintf(cmd, sizeof(cmd),
        "/usr/bin/osascript -e 'display notification \"%s\" with title \"PG3D Cheats\"'"
        " >/dev/null 2>&1 &", message);
    system(cmd);
}

// ── key parsing ──

static void parse_key(const char *str, int *out_type, int *out_code) {
    *out_type = 0;
    *out_code = -1;
    if (!str || !*str) return;

    if (strcmp(str, "mouse4") == 0)      { *out_type = 1; *out_code = 3; return; }
    if (strcmp(str, "mouse5") == 0)      { *out_type = 1; *out_code = 4; return; }
    if (strcmp(str, "mouse3") == 0)      { *out_type = 1; *out_code = 2; return; }
    if (strcmp(str, "mouse1") == 0)      { *out_type = 1; *out_code = 0; return; }
    if (strcmp(str, "mouse2") == 0)      { *out_type = 1; *out_code = 1; return; }

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
    if (strcmp(str, "space") == 0)       { *out_type = 0; *out_code = 49; return; }
    if (strcmp(str, "shift") == 0)       { *out_type = 0; *out_code = 56; return; }
    if (strcmp(str, "control") == 0)     { *out_type = 0; *out_code = 59; return; }
    if (strcmp(str, "alt") == 0)         { *out_type = 0; *out_code = 58; return; }
    if (strcmp(str, "capslock") == 0)    { *out_type = 0; *out_code = 57; return; }
    if (strcmp(str, "tab") == 0)         { *out_type = 0; *out_code = 48; return; }
}

static bool key_is_down(int type, int code) {
    if (code < 0) return false;
    if (type == 1) {
        // [NSEvent pressedMouseButtons] — works without Accessibility permission
        Class cls = objc_getClass("NSEvent");
        if (cls) {
            unsigned long buttons = ((unsigned long (*)(id, SEL))objc_msgSend)(
                (id)cls, sel_registerName("pressedMouseButtons"));
            return (buttons >> code) & 1;
        }
        return CGEventSourceButtonState(kCGEventSourceStateCombinedSessionState, (uint32_t)code);
    }
    // Keyboard keys still need Accessibility; mouse-button toggles preferred
    return CGEventSourceKeyState(kCGEventSourceStateCombinedSessionState, (uint16_t)code);
}

// ── IL2CPP bootstrap ──

static bool find_game_assembly(void) {
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; i++) {
        const char *name = _dyld_get_image_name(i);
        if (name && strstr(name, "/GameAssembly.dylib")) {
            void *handle = dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_resolve_icall");
            if (!handle) return false;
            il2cpp_resolve_icall = (void *(*)(const char *))handle;
            il2cpp_get_corlib = (void *(*)(void))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_get_corlib");
            il2cpp_domain_get = (Il2CppDomain *(*)(void))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_domain_get");
            il2cpp_domain_get_assemblies = (Il2CppAssembly **(*)(Il2CppDomain *, size_t *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_domain_get_assemblies");
            il2cpp_assembly_get_image = (Il2CppImage *(*)(Il2CppAssembly *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_assembly_get_image");
            il2cpp_class_from_name = (Il2CppClass *(*)(Il2CppImage *, const char *, const char *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_class_from_name");
            il2cpp_class_get_method_from_name = (const Il2CppMethodInfo *(*)(Il2CppClass *, const char *, int))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_class_get_method_from_name");
            il2cpp_runtime_invoke = (Il2CppObject *(*)(const Il2CppMethodInfo *, void *, void **, Il2CppObject **))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_runtime_invoke");
            il2cpp_class_get_type = (Il2CppType *(*)(Il2CppClass *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_class_get_type");
            il2cpp_object_get_class = (Il2CppClass *(*)(Il2CppObject *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_object_get_class");
            il2cpp_class_get_name = (const char *(*)(Il2CppClass *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_class_get_name");
            il2cpp_class_get_parent = (Il2CppClass *(*)(Il2CppClass *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_class_get_parent");
            il2cpp_image_get_class_count = (size_t (*)(Il2CppImage *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_image_get_class_count");
            il2cpp_image_get_class = (Il2CppClass *(*)(Il2CppImage *, size_t))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_image_get_class");
            il2cpp_class_get_namespace = (const char *(*)(Il2CppClass *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_class_get_namespace");
            note("GameAssembly found: %s", name);
            return true;
        }
    }
    return false;
}

static bool resolve_unity_icalls(void) {
    if (!il2cpp_resolve_icall) return false;
    if (il2cpp_get_corlib && il2cpp_get_corlib() == NULL) return false;

    if (!Camera_get_main)
        Camera_get_main = (Il2CppObject *(*)(void))il2cpp_resolve_icall("UnityEngine.Camera::get_main()");
    if (!Camera_get_main)
        Camera_get_main = (Il2CppObject *(*)(void))il2cpp_resolve_icall("UnityEngine.Camera::get_main");
    Component_get_transform = (Il2CppObject *(*)(Il2CppObject *))il2cpp_resolve_icall("UnityEngine.Component::get_transform");
    Time_get_frameCount = (int (*)(void))il2cpp_resolve_icall("UnityEngine.Time::get_frameCount");
    Time_get_deltaTime = (float (*)(void))il2cpp_resolve_icall("UnityEngine.Time::get_deltaTime");

    Transform_get_position = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::get_position_Injected(UnityEngine.Vector3&)");
    Transform_set_position = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::set_position_Injected(UnityEngine.Vector3&)");
    Transform_get_eulerAngles = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::get_eulerAngles_Injected(UnityEngine.Vector3&)");
    Transform_set_eulerAngles = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::set_eulerAngles_Injected(UnityEngine.Vector3&)");
    Transform_get_forward = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::get_forward_Injected(UnityEngine.Vector3&)");

    if (!Camera_get_main || !Component_get_transform || !Time_get_frameCount)
        return false;
    if (!Transform_get_position || !Transform_get_eulerAngles || !Transform_set_eulerAngles) {
        if (!Transform_get_position)
            Transform_get_position = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::INTERNAL_get_position(UnityEngine.Vector3&)");
        if (!Transform_get_eulerAngles)
            Transform_get_eulerAngles = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::INTERNAL_get_eulerAngles(UnityEngine.Vector3&)");
        if (!Transform_set_eulerAngles)
            Transform_set_eulerAngles = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::INTERNAL_set_eulerAngles(UnityEngine.Vector3&)");
        if (!Transform_get_forward)
            Transform_get_forward = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::INTERNAL_get_forward(UnityEngine.Vector3&)");
    }
    if (!Transform_get_position || !Transform_get_eulerAngles || !Transform_set_eulerAngles)
        return false;
    return true;
}

// ── PG3D player discovery ──

static Il2CppClass *g_player_class;
static Il2CppImage *g_game_image;

static bool inherits_from(Il2CppClass *cls, const char *ancestor_name) {
    if (!il2cpp_class_get_parent || !il2cpp_class_get_name) return false;
    for (int depth = 0; depth < 16 && cls; depth++) {
        const char *name = il2cpp_class_get_name(cls);
        if (name && strcmp(name, ancestor_name) == 0) return true;
        cls = il2cpp_class_get_parent(cls);
    }
    return false;
}

static bool find_player_class(void) {
    if (g_player_class) return true;
    if (!il2cpp_domain_get || !il2cpp_domain_get_assemblies ||
        !il2cpp_assembly_get_image || !il2cpp_class_from_name)
        return false;

    Il2CppDomain *domain = il2cpp_domain_get();
    if (!domain) return false;
    size_t count = 0;
    Il2CppAssembly **assemblies = il2cpp_domain_get_assemblies(domain, &count);
    if (!assemblies) return false;

    // 1. Try known PG3D class names (pre-obfuscation builds)
    static const char *class_names[] = {
        "Player_move_c", "PlayerMovC", "PlayerMoveC", "Player_Move_C", NULL
    };
    static const char *namespaces[] = { "", "Rilisoft", NULL };

    for (size_t i = 0; i < count; i++) {
        Il2CppImage *img = il2cpp_assembly_get_image(assemblies[i]);
        if (!img) continue;
        for (const char **ns = namespaces; *ns; ns++) {
            for (const char **cn = class_names; *cn; cn++) {
                Il2CppClass *cls = il2cpp_class_from_name(img, *ns, *cn);
                if (cls) {
                    g_player_class = cls;
                    g_game_image = img;
                    note("found player class: %s::%s", *ns[0] ? *ns : "(global)", *cn);
                    return true;
                }
            }
        }
    }

    // 2. Fallback: CharacterController — standard Unity player component,
    //    never obfuscated since it's engine code, not game code
    for (size_t i = 0; i < count; i++) {
        Il2CppImage *img = il2cpp_assembly_get_image(assemblies[i]);
        if (!img) continue;
        Il2CppClass *cls = il2cpp_class_from_name(img, "UnityEngine", "CharacterController");
        if (cls) {
            g_player_class = cls;
            g_game_image = img;
            note("using CharacterController as player proxy (obfuscated game classes)");
            return true;
        }
    }

    // 3. Enumerate all classes, find MonoBehaviour subclasses with player-like traits
    if (il2cpp_image_get_class_count && il2cpp_image_get_class &&
        il2cpp_class_get_name && il2cpp_class_get_method_from_name) {
        for (size_t i = 0; i < count; i++) {
            Il2CppImage *img = il2cpp_assembly_get_image(assemblies[i]);
            if (!img) continue;
            size_t cls_count = il2cpp_image_get_class_count(img);
            for (size_t j = 0; j < cls_count; j++) {
                Il2CppClass *cls = il2cpp_image_get_class(img, j);
                if (!cls) continue;
                if (!inherits_from(cls, "MonoBehaviour")) continue;
                // Look for classes with characteristic player methods
                if (il2cpp_class_get_method_from_name(cls, "Move", -1) ||
                    il2cpp_class_get_method_from_name(cls, "OnDamage", -1) ||
                    il2cpp_class_get_method_from_name(cls, "TakeDamage", -1) ||
                    il2cpp_class_get_method_from_name(cls, "Die", -1)) {
                    const char *name = il2cpp_class_get_name(cls);
                    g_player_class = cls;
                    g_game_image = img;
                    note("found player class via enumeration: %s", name ? name : "?");
                    return true;
                }
            }
        }
    }

    return false;
}

// ── Vector math ──

typedef struct { float x, y, z; } Vec3;

static float vec3_dist_sq(Vec3 a, Vec3 b) {
    float dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    return dx*dx + dy*dy + dz*dz;
}

static Vec3 vec3_sub(Vec3 a, Vec3 b) {
    return (Vec3){ a.x - b.x, a.y - b.y, a.z - b.z };
}

static float deg(float r) { return r * (180.0f / 3.14159265f); }

static Vec3 calc_aim_angles(Vec3 cam_pos, Vec3 target_pos) {
    Vec3 delta = vec3_sub(target_pos, cam_pos);
    float dist_xz = sqrtf(delta.x * delta.x + delta.z * delta.z);
    float yaw = deg(atan2f(delta.x, delta.z));
    float pitch = -deg(atan2f(delta.y, dist_xz));
    return (Vec3){ pitch, yaw, 0.0f };
}

static float lerp_angle(float from, float to, float t) {
    float diff = to - from;
    while (diff > 180.0f) diff -= 360.0f;
    while (diff < -180.0f) diff += 360.0f;
    return from + diff * t;
}

// ── FindObjectsOfType wrapper ──

static const Il2CppMethodInfo *g_find_objects_method;

static bool resolve_find_objects(void) {
    if (g_find_objects_method) return true;
    if (!il2cpp_class_from_name || !il2cpp_class_get_method_from_name) return false;

    Il2CppDomain *domain = il2cpp_domain_get();
    if (!domain) return false;
    size_t count = 0;
    Il2CppAssembly **assemblies = il2cpp_domain_get_assemblies(domain, &count);

    for (size_t i = 0; i < count; i++) {
        Il2CppImage *img = il2cpp_assembly_get_image(assemblies[i]);
        if (!img) continue;
        Il2CppClass *obj_class = il2cpp_class_from_name(img, "UnityEngine", "Object");
        if (!obj_class) continue;
        g_find_objects_method = il2cpp_class_get_method_from_name(obj_class, "FindObjectsOfType", 1);
        if (g_find_objects_method) {
            note("resolved Object.FindObjectsOfType");
            break;
        }
    }
    return g_find_objects_method != NULL;
}

static Il2CppObject **get_player_objects(int *out_count) {
    *out_count = 0;
    if (!g_player_class || !il2cpp_class_get_type || !il2cpp_resolve_icall)
        return NULL;

    Il2CppType *player_type = il2cpp_class_get_type(g_player_class);
    if (!player_type) return NULL;

    static void *(*find_of_type_icall)(void *type) = NULL;
    if (!find_of_type_icall) {
        find_of_type_icall = (void *(*)(void *))il2cpp_resolve_icall(
            "UnityEngine.Object::FindObjectsOfType(System.Type)");
        if (!find_of_type_icall)
            find_of_type_icall = (void *(*)(void *))il2cpp_resolve_icall(
                "UnityEngine.Object::FindObjectsOfType");
        if (!find_of_type_icall) return NULL;
    }

    static void *(*type_get_object)(void *type) = NULL;
    if (!type_get_object) {
        type_get_object = (void *(*)(void *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_type_get_object");
        if (!type_get_object) return NULL;
    }

    void *reflection_type = type_get_object(player_type);
    if (!reflection_type) return NULL;

    void *result = find_of_type_icall(reflection_type);
    if (!result) return NULL;

    // IL2CPP array layout: header(16) + bounds(8) + length(8) + elements
    uint8_t *arr = (uint8_t *)result;
    size_t length = *(size_t *)(arr + 24);
    if (length == 0 || length > kMaxPlayers) return NULL;

    *out_count = (int)length;
    return (Il2CppObject **)(arr + 32);
}

static Il2CppObject *get_transform(Il2CppObject *component) {
    if (!Component_get_transform || !component) return NULL;
    return Component_get_transform(component);
}

static bool get_position(Il2CppObject *transform, Vec3 *out) {
    if (!Transform_get_position || !transform) return false;
    Transform_get_position(transform, (float *)out);
    return true;
}

static bool is_local_player(Il2CppObject *player, Vec3 cam_pos) {
    Il2CppObject *tf = get_transform(player);
    if (!tf) return true;
    Vec3 pos;
    if (!get_position(tf, &pos)) return true;
    return vec3_dist_sq(pos, cam_pos) < 4.0f;
}

// ── Physics raycast (triggerbot) ──

static bool (*g_physics_raycast)(float *origin, float *direction, float maxDist, int layerMask);

static bool resolve_physics(void) {
    if (g_physics_raycast) return true;
    if (!il2cpp_resolve_icall) return false;
    g_physics_raycast = (bool (*)(float *, float *, float, int))il2cpp_resolve_icall(
        "UnityEngine.Physics::Internal_RaycastTest(UnityEngine.Ray,System.Single,System.Int32)");
    if (!g_physics_raycast)
        g_physics_raycast = (bool (*)(float *, float *, float, int))il2cpp_resolve_icall(
            "UnityEngine.Physics::Raycast(UnityEngine.Ray,System.Single,System.Int32)");
    return g_physics_raycast != NULL;
}

// ── Mouse click simulation (triggerbot) ──

typedef struct { double x; double y; } CGPoint_t;

extern void *CGEventCreateMouseEvent(void *source, uint32_t mouseType, CGPoint_t mouseCursorPosition, uint32_t mouseButton);
extern void CGEventPost(uint32_t tap, void *event);
extern void CFRelease(void *cf);

#define kCGEventLeftMouseDown  1
#define kCGEventLeftMouseUp    2
#define kCGHIDEventTap         0

static void simulate_click(void) {
    CGPoint_t zero = {0, 0};
    void *down = CGEventCreateMouseEvent(NULL, kCGEventLeftMouseDown, zero, 0);
    void *up   = CGEventCreateMouseEvent(NULL, kCGEventLeftMouseUp,   zero, 0);
    if (down) { CGEventPost(kCGHIDEventTap, down); CFRelease(down); }
    if (up)   { CGEventPost(kCGHIDEventTap, up);   CFRelease(up);   }
}

// ── cheats thread ──
// Runs on a background pthread. Handles init polling, then the ~60 Hz cheat tick.
// IL2CPP native function pointers are safe to call from any thread; the worst case
// for Transform writes is a single-frame glitch from racing the render thread.

static void *cheats_thread(void *arg) {
    (void)arg;

    usleep(kInitDelayUs);
    note("cheats thread started — waiting for GameAssembly");

    // ── init phase: find IL2CPP, resolve symbols ──
    bool game_found = false;
    for (unsigned attempt = 1; attempt <= kRetryLimit; attempt++) {
        if (!game_found) {
            game_found = find_game_assembly();
            if (!game_found) {
                if (attempt == 1)
                    note("init try %u: GameAssembly not loaded yet", attempt);
                usleep(kRetryUs);
                continue;
            }
        }
        if (!resolve_unity_icalls()) {
            if (attempt <= 3 || attempt % 20 == 0)
                note("init try %u: icalls not ready (cam=%p tf=%p pos=%p euler=%p set_euler=%p)",
                     attempt,
                     (void *)Camera_get_main, (void *)Component_get_transform,
                     (void *)Transform_get_position, (void *)Transform_get_eulerAngles,
                     (void *)Transform_set_eulerAngles);
            usleep(kRetryUs);
            continue;
        }
        if (!find_player_class()) {
            if (attempt <= 3 || attempt % 20 == 0)
                note("init try %u: player class not found yet", attempt);
            usleep(kRetryUs);
            continue;
        }

        resolve_find_objects();
        resolve_physics();
        g_il2cpp_ready = true;
        note("IL2CPP ready — aimbot:%s triggerbot:%s (find_objects=%p physics=%p)",
             g_aimbot_active ? "ON" : "off",
             g_triggerbot_active ? "ON" : "off",
             (void *)g_find_objects_method, (void *)g_physics_raycast);
        {
            char tmsg[80];
            snprintf(tmsg, sizeof(tmsg), "Cheats ON — aim:%s trig:%s",
                     g_aimbot_active ? "ON" : "off", g_triggerbot_active ? "ON" : "off");
            toast(tmsg);
        }
        break;
    }

    if (!g_il2cpp_ready) {
        note("timed out waiting for IL2CPP after %d retries; cheats inactive", kRetryLimit);
        toast("Cheats failed — IL2CPP not found");
        return NULL;
    }

    // ── tick phase: hotkey toggles + aimbot + triggerbot ──
    bool aim_was_down = false;
    bool trig_was_down = false;

    for (;;) {
        // Toggle detection (rising edge)
        if (g_aimbot_enabled) {
            bool down = key_is_down(g_aimbot_key_type, g_aimbot_key_code);
            if (down && !aim_was_down) {
                g_aimbot_active = !g_aimbot_active;
                note("aimbot %s", g_aimbot_active ? "ON" : "OFF");
                toast(g_aimbot_active ? "Aimbot ON" : "Aimbot OFF");
            }
            aim_was_down = down;
        }
        if (g_triggerbot_enabled) {
            bool down = key_is_down(g_triggerbot_key_type, g_triggerbot_key_code);
            if (down && !trig_was_down) {
                g_triggerbot_active = !g_triggerbot_active;
                note("triggerbot %s", g_triggerbot_active ? "ON" : "OFF");
                toast(g_triggerbot_active ? "Triggerbot ON" : "Triggerbot OFF");
            }
            trig_was_down = down;
        }

        if (!g_aimbot_active && !g_triggerbot_active) {
            usleep(kTickUs);
            continue;
        }

        // Camera
        Il2CppObject *cam = Camera_get_main ? Camera_get_main() : NULL;
        if (!cam) { usleep(kTickUs); continue; }
        Il2CppObject *cam_tf = Component_get_transform ? Component_get_transform(cam) : NULL;
        if (!cam_tf) { usleep(kTickUs); continue; }
        Vec3 cam_pos;
        if (!get_position(cam_tf, &cam_pos)) { usleep(kTickUs); continue; }

        // ── aimbot ──
        if (g_aimbot_active) {
            int player_count = 0;
            Il2CppObject **players = get_player_objects(&player_count);
            if (players && player_count > 0) {
                float best_dist = 1e30f;
                Vec3 best_pos = {0};
                bool found = false;

                for (int i = 0; i < player_count; i++) {
                    if (!players[i]) continue;
                    if (is_local_player(players[i], cam_pos)) continue;
                    Il2CppObject *tf = get_transform(players[i]);
                    if (!tf) continue;
                    Vec3 pos;
                    if (!get_position(tf, &pos)) continue;
                    pos.y += 1.5f; // head height
                    float d = vec3_dist_sq(pos, cam_pos);
                    if (d < best_dist) { best_dist = d; best_pos = pos; found = true; }
                }

                if (found && Transform_set_eulerAngles && Transform_get_eulerAngles) {
                    Vec3 target = calc_aim_angles(cam_pos, best_pos);
                    Vec3 current;
                    Transform_get_eulerAngles(cam_tf, (float *)&current);
                    float dt = Time_get_deltaTime ? Time_get_deltaTime() : 0.016f;
                    float s = 1.0f - expf(-dt * kAimSmoothing);
                    Vec3 aim = {
                        lerp_angle(current.x, target.x, s),
                        lerp_angle(current.y, target.y, s),
                        0.0f
                    };
                    Transform_set_eulerAngles(cam_tf, (float *)&aim);
                }
            }
        }

        // ── triggerbot ──
        if (g_triggerbot_active && Transform_get_forward && g_physics_raycast) {
            Vec3 forward;
            Transform_get_forward(cam_tf, (float *)&forward);
            int player_layers = (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 12);
            if (g_physics_raycast((float *)&cam_pos, (float *)&forward, 200.0f, player_layers))
                simulate_click();
        }

        usleep(kTickUs);
    }

    return NULL;
}

// ── entry ──

__attribute__((constructor))
static void start_cheats(void) {
    const char *flag = getenv("PG3D_CHEATS");
    if (!flag || strcmp(flag, "0") == 0) return;

    const char *aim = getenv("PG3D_CHEATS_AIMBOT");
    const char *trig = getenv("PG3D_CHEATS_TRIGGERBOT");
    g_aimbot_enabled = aim && strcmp(aim, "1") == 0;
    g_triggerbot_enabled = trig && strcmp(trig, "1") == 0;
    if (!g_aimbot_enabled && !g_triggerbot_enabled) return;
    g_aimbot_active = g_aimbot_enabled;
    g_triggerbot_active = g_triggerbot_enabled;

    parse_key(getenv("PG3D_CHEATS_AIMBOT_KEY"), &g_aimbot_key_type, &g_aimbot_key_code);
    parse_key(getenv("PG3D_CHEATS_TRIGGERBOT_KEY"), &g_triggerbot_key_type, &g_triggerbot_key_code);
    if (g_aimbot_key_code < 0)    { g_aimbot_key_type = 1; g_aimbot_key_code = 3; }
    if (g_triggerbot_key_code < 0) { g_triggerbot_key_type = 1; g_triggerbot_key_code = 4; }

    note("loaded — aimbot:%s (key type=%d code=%d) triggerbot:%s (key type=%d code=%d) — active by default",
         g_aimbot_enabled ? "ON" : "off", g_aimbot_key_type, g_aimbot_key_code,
         g_triggerbot_enabled ? "ON" : "off", g_triggerbot_key_type, g_triggerbot_key_code);
    note("mouse-button toggles use NSEvent (no Accessibility needed); keyboard toggles need Accessibility");

    pthread_t tid;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    int err = pthread_create(&tid, &attr, cheats_thread, NULL);
    pthread_attr_destroy(&attr);
    if (err != 0)
        note("pthread_create failed: %d", err);
}
