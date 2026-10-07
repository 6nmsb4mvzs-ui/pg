// PG3D aimbot + triggerbot. Injected via DYLD_INSERT_LIBRARIES alongside the
// FPS unlocker and mouse fix. Reads PG3D_CHEATS_AIMBOT, PG3D_CHEATS_TRIGGERBOT
// (each "1" to enable) and PG3D_CHEATS_AIMBOT_KEY / PG3D_CHEATS_TRIGGERBOT_KEY
// (CGKeyCode or "mouse4"/"mouse5"). Toggle in-game with the bound key.
//
// Built without the macOS SDK (zig cc, -undefined dynamic_lookup).

#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <math.h>

// ── ObjC / CoreGraphics / GCD externs (no SDK) ──

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

struct timespec_ { long tv_sec; long tv_nsec; };
extern int clock_gettime(int clock_id, struct timespec_ *tp);
#define CLOCK_MONOTONIC_ 6

extern bool CGEventSourceKeyState(int stateID, uint16_t keyCode);
extern bool CGEventSourceButtonState(int stateID, uint32_t button);
#define kCGEventSourceStateCombinedSessionState 0

extern char _dispatch_main_q;
extern void dispatch_async_f(void *queue, void *context, void (*work)(void *));
extern void dispatch_after_f(uint64_t when, void *queue, void *context, void (*work)(void *));
extern uint64_t dispatch_time(uint64_t when, int64_t delta);

#define MAIN_QUEUE ((void *)&_dispatch_main_q)
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

// Unity icall function pointers
static Il2CppObject *(*Camera_get_main)(void);
static Il2CppObject *(*Component_get_transform)(Il2CppObject *self);
static void (*Transform_get_position)(Il2CppObject *self, float *out);
static void (*Transform_set_position)(Il2CppObject *self, float *value);
static void (*Transform_get_eulerAngles)(Il2CppObject *self, float *out);
static void (*Transform_set_eulerAngles)(Il2CppObject *self, float *value);
static void (*Transform_get_forward)(Il2CppObject *self, float *out);
static Il2CppObject *(*Camera_get_transform)(Il2CppObject *self);
static Il2CppObject *(*Object_FindObjectsOfType)(Il2CppType *type);
static int (*Object_FindObjectsOfType_count)(Il2CppArray *arr);

// We'll resolve these via il2cpp_resolve_icall
static int (*Time_get_frameCount)(void);
static float (*Time_get_deltaTime)(void);

// ── config ──

enum {
    kTickMs          = 16,     // ~60 Hz poll
    kRetryMs         = 500,
    kRetryLimit      = 240,    // 2 minutes
    kMaxPlayers      = 32,
    kAimSmoothing    = 8,      // lower = snappier
};

static bool g_aimbot_enabled;
static bool g_triggerbot_enabled;
static bool g_aimbot_active;     // toggled state
static bool g_triggerbot_active;

// key config: -1 = mouse button, >= 0 = keyboard CGKeyCode
static int g_aimbot_key_type;    // 0 = keyboard, 1 = mouse
static int g_aimbot_key_code;
static int g_triggerbot_key_type;
static int g_triggerbot_key_code;

static bool g_il2cpp_ready;
static unsigned g_retry_count;
static bool g_game_assembly_found;

// debounce: track previous key state for toggle
static bool g_aim_key_was_down;
static bool g_trig_key_was_down;

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

    // single char → CGKeyCode (approximate for common keys)
    if (strlen(str) == 1) {
        char c = str[0];
        // rough qwerty map
        static const uint16_t alpha[] = {
            0,11,8,2,14,3,5,4,34,38,40,37,46,45,31,35,12,15,1,17,32,9,13,7,16,6
        };
        if (c >= 'a' && c <= 'z') { *out_type = 0; *out_code = alpha[c - 'a']; return; }
        if (c >= 'A' && c <= 'Z') { *out_type = 0; *out_code = alpha[c - 'A']; return; }
    }
    // F-keys
    if (str[0] == 'f' || str[0] == 'F') {
        int n = atoi(str + 1);
        static const uint16_t fkeys[] = {0,122,120,99,118,96,97,98,100,101,109,103,111,105,107};
        if (n >= 1 && n <= 14) { *out_type = 0; *out_code = fkeys[n]; return; }
    }
    // space, shift, etc.
    if (strcmp(str, "space") == 0)       { *out_type = 0; *out_code = 49; return; }
    if (strcmp(str, "shift") == 0)       { *out_type = 0; *out_code = 56; return; }
    if (strcmp(str, "control") == 0)     { *out_type = 0; *out_code = 59; return; }
    if (strcmp(str, "alt") == 0)         { *out_type = 0; *out_code = 58; return; }
    if (strcmp(str, "capslock") == 0)    { *out_type = 0; *out_code = 57; return; }
    if (strcmp(str, "tab") == 0)         { *out_type = 0; *out_code = 48; return; }
}

static bool key_is_down(int type, int code) {
    if (code < 0) return false;
    if (type == 1) return CGEventSourceButtonState(kCGEventSourceStateCombinedSessionState, (uint32_t)code);
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
            note("GameAssembly found: %s", name);
            return true;
        }
    }
    return false;
}

static bool resolve_unity_icalls(void) {
    if (!il2cpp_resolve_icall) return false;
    if (il2cpp_get_corlib && il2cpp_get_corlib() == NULL) return false;

    Camera_get_main = (Il2CppObject *(*)(void))il2cpp_resolve_icall("UnityEngine.Camera::get_main");
    Component_get_transform = (Il2CppObject *(*)(Il2CppObject *))il2cpp_resolve_icall("UnityEngine.Component::get_transform");
    Time_get_frameCount = (int (*)(void))il2cpp_resolve_icall("UnityEngine.Time::get_frameCount");
    Time_get_deltaTime = (float (*)(void))il2cpp_resolve_icall("UnityEngine.Time::get_deltaTime");

    // Transform icalls — Unity uses internal calls with explicit Vector3 out params
    Transform_get_position = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::get_position_Injected(UnityEngine.Vector3&)");
    Transform_set_position = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::set_position_Injected(UnityEngine.Vector3&)");
    Transform_get_eulerAngles = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::get_eulerAngles_Injected(UnityEngine.Vector3&)");
    Transform_set_eulerAngles = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::set_eulerAngles_Injected(UnityEngine.Vector3&)");
    Transform_get_forward = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::get_forward_Injected(UnityEngine.Vector3&)");

    if (!Camera_get_main || !Component_get_transform || !Time_get_frameCount) {
        return false;
    }
    if (!Transform_get_position || !Transform_get_eulerAngles || !Transform_set_eulerAngles) {
        // Try the non-injected variant names (older Unity / IL2CPP builds)
        if (!Transform_get_position)
            Transform_get_position = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::INTERNAL_get_position(UnityEngine.Vector3&)");
        if (!Transform_get_eulerAngles)
            Transform_get_eulerAngles = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::INTERNAL_get_eulerAngles(UnityEngine.Vector3&)");
        if (!Transform_set_eulerAngles)
            Transform_set_eulerAngles = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::INTERNAL_set_eulerAngles(UnityEngine.Vector3&)");
        if (!Transform_get_forward)
            Transform_get_forward = (void (*)(Il2CppObject *, float *))il2cpp_resolve_icall("UnityEngine.Transform::INTERNAL_get_forward(UnityEngine.Vector3&)");
    }
    if (!Transform_get_position || !Transform_get_eulerAngles || !Transform_set_eulerAngles) {
        return false;
    }
    return true;
}

// ── PG3D player discovery ──
// PG3D uses Player_move_c (MonoBehaviour on each player). We find them by walking
// the scene's active objects. Since FindObjectsOfType from C is fragile across builds,
// we use a simpler approach: resolve the static "LocalPlayer_move_C" reference and
// enumerate network players via the game's own player list.

static Il2CppClass *g_player_class;
static Il2CppImage *g_game_image;

static Il2CppImage *find_assembly_image(const char *assembly_name) {
    if (!il2cpp_domain_get || !il2cpp_domain_get_assemblies || !il2cpp_assembly_get_image)
        return NULL;
    Il2CppDomain *domain = il2cpp_domain_get();
    if (!domain) return NULL;
    size_t count = 0;
    Il2CppAssembly **assemblies = il2cpp_domain_get_assemblies(domain, &count);
    if (!assemblies) return NULL;
    for (size_t i = 0; i < count; i++) {
        Il2CppImage *img = il2cpp_assembly_get_image(assemblies[i]);
        if (!img) continue;
        // We just try the class lookup on every image since we can't reliably
        // get the image name without additional API
        (void)assembly_name;
        return img;
    }
    return NULL;
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

    // PG3D class names to search for (different builds use different names)
    static const char *class_names[] = {
        "Player_move_c",
        "PlayerMovC",
        "PlayerMoveC",
        "Player_Move_C",
        NULL
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
    return false;
}

// ── Unity Object.FindObjectsOfType via il2cpp_runtime_invoke ──
// Returns a managed C# array. We read its length and elements to get player transforms.

typedef struct {
    float x, y, z;
} Vec3;

static float vec3_dist_sq(Vec3 a, Vec3 b) {
    float dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    return dx*dx + dy*dy + dz*dz;
}

static float vec3_length(Vec3 v) {
    return sqrtf(v.x*v.x + v.y*v.y + v.z*v.z);
}

static Vec3 vec3_sub(Vec3 a, Vec3 b) {
    return (Vec3){ a.x - b.x, a.y - b.y, a.z - b.z };
}

static float deg(float rad) { return rad * (180.0f / 3.14159265f); }
static float rad(float d) { return d * (3.14159265f / 180.0f); }

// ── core aimbot logic ──

// Calculate angles from camera position toward a target position
static Vec3 calc_aim_angles(Vec3 cam_pos, Vec3 target_pos) {
    Vec3 delta = vec3_sub(target_pos, cam_pos);
    float dist_xz = sqrtf(delta.x * delta.x + delta.z * delta.z);
    float yaw = deg(atan2f(delta.x, delta.z));
    float pitch = -deg(atan2f(delta.y, dist_xz));
    return (Vec3){ pitch, yaw, 0.0f };
}

// Smooth interpolation for aim angles
static float lerp_angle(float from, float to, float t) {
    float diff = to - from;
    while (diff > 180.0f) diff -= 360.0f;
    while (diff < -180.0f) diff += 360.0f;
    return from + diff * t;
}

// ── FindObjectsOfType wrapper ──
// PG3D IL2CPP: we invoke Object.FindObjectsOfType(typeof(Player_move_c)) to get all
// player instances. The managed array has a known IL2CPP layout:
//   [Il2CppObject header] [Il2CppArrayBounds*] [size_t length] [element0] [element1] ...

static const Il2CppMethodInfo *g_find_objects_method;
static const Il2CppMethodInfo *g_get_transform_method;

// Generic FindObjectsOfType — resolved once
static bool resolve_find_objects(void) {
    if (g_find_objects_method) return true;
    if (!il2cpp_class_from_name || !il2cpp_class_get_method_from_name) return false;

    // Find UnityEngine.Object class
    Il2CppDomain *domain = il2cpp_domain_get();
    if (!domain) return false;
    size_t count = 0;
    Il2CppAssembly **assemblies = il2cpp_domain_get_assemblies(domain, &count);

    for (size_t i = 0; i < count; i++) {
        Il2CppImage *img = il2cpp_assembly_get_image(assemblies[i]);
        if (!img) continue;
        Il2CppClass *obj_class = il2cpp_class_from_name(img, "UnityEngine", "Object");
        if (!obj_class) continue;

        // FindObjectsOfType(System.Type) — 1 param
        g_find_objects_method = il2cpp_class_get_method_from_name(obj_class, "FindObjectsOfType", 1);
        if (g_find_objects_method) {
            note("resolved Object.FindObjectsOfType");
            break;
        }
    }
    return g_find_objects_method != NULL;
}

// Get all instances of the player class
static Il2CppObject **get_player_objects(int *out_count) {
    *out_count = 0;
    if (!g_find_objects_method || !g_player_class || !il2cpp_class_get_type || !il2cpp_runtime_invoke)
        return NULL;

    Il2CppType *player_type = il2cpp_class_get_type(g_player_class);
    if (!player_type) return NULL;

    // Call Type.GetType() to get a System.Type from Il2CppType
    // Actually: il2cpp provides il2cpp_type_get_object for this, but we can use
    // Object.FindObjectsOfType with the Il2CppType directly since the managed
    // method expects System.Type which maps to Il2CppType* in the icall layer.

    // FindObjectsOfType icall: "UnityEngine.Object::FindObjectsOfType(System.Type)"
    static void *(*find_of_type_icall)(void *type) = NULL;
    if (!find_of_type_icall) {
        find_of_type_icall = (void *(*)(void *))il2cpp_resolve_icall(
            "UnityEngine.Object::FindObjectsOfType(System.Type)");
        if (!find_of_type_icall) {
            // Try alternate signature
            find_of_type_icall = (void *(*)(void *))il2cpp_resolve_icall(
                "UnityEngine.Object::FindObjectsOfType");
            if (!find_of_type_icall) return NULL;
        }
    }

    // We need to convert Il2CppType* to a System.Type (Il2CppReflectionType*)
    static void *(*type_get_object)(void *type) = NULL;
    if (!type_get_object) {
        type_get_object = (void *(*)(void *))dlsym(RTLD_DEFAULT_HANDLE, "il2cpp_type_get_object");
        if (!type_get_object) return NULL;
    }

    void *reflection_type = type_get_object(player_type);
    if (!reflection_type) return NULL;

    // Call the icall
    void *result = find_of_type_icall(reflection_type);
    if (!result) return NULL;

    // IL2CPP array layout: after the object header and bounds pointer comes length
    // Standard IL2CPP array: header(16) + bounds(8) + length(8) + elements
    // We read the length as size_t at offset 24 from the array pointer
    uint8_t *arr = (uint8_t *)result;
    size_t length = *(size_t *)(arr + 24);
    if (length == 0 || length > kMaxPlayers) {
        *out_count = 0;
        return NULL;
    }

    // Elements start at offset 32
    Il2CppObject **elements = (Il2CppObject **)(arr + 32);
    *out_count = (int)length;
    return elements;
}

// Get the Transform component from a player object (Component.get_transform icall)
static Il2CppObject *get_transform(Il2CppObject *component) {
    if (!Component_get_transform || !component) return NULL;
    return Component_get_transform(component);
}

// Get world position from a transform
static bool get_position(Il2CppObject *transform, Vec3 *out) {
    if (!Transform_get_position || !transform) return false;
    Transform_get_position(transform, (float *)out);
    return true;
}

// ── Determine if a player object is the local player ──
// PG3D marks the local player with isOurPlayer or isLocal. We check by comparing
// its transform to the camera's parent transform. The local player's camera is
// parented to them, so the positions will be very close.

static bool is_local_player(Il2CppObject *player, Vec3 cam_pos) {
    Il2CppObject *tf = get_transform(player);
    if (!tf) return true; // assume local if we can't tell
    Vec3 pos;
    if (!get_position(tf, &pos)) return true;
    float dist = vec3_dist_sq(pos, cam_pos);
    return dist < 4.0f; // within 2 units of camera = local player
}

// ── triggerbot: check if crosshair is on an enemy ──
// Uses Physics.Raycast icall

static bool (*g_physics_raycast)(float *origin, float *direction, float maxDist, int layerMask);

static bool resolve_physics(void) {
    if (g_physics_raycast) return true;
    if (!il2cpp_resolve_icall) return false;

    // Try different raycast icall signatures
    g_physics_raycast = (bool (*)(float *, float *, float, int))il2cpp_resolve_icall(
        "UnityEngine.Physics::Internal_RaycastTest(UnityEngine.Ray,System.Single,System.Int32)");
    if (!g_physics_raycast)
        g_physics_raycast = (bool (*)(float *, float *, float, int))il2cpp_resolve_icall(
            "UnityEngine.Physics::Raycast(UnityEngine.Ray,System.Single,System.Int32)");
    return g_physics_raycast != NULL;
}

// ── simulate mouse click via CGEvent ──

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

// ── main tick ──

static int g_last_log_frame;

static void cheat_tick(void *unused) {
    (void)unused;

    if (!g_il2cpp_ready) goto reschedule;

    // Toggle check: detect rising edge of hotkey
    if (g_aimbot_enabled) {
        bool down = key_is_down(g_aimbot_key_type, g_aimbot_key_code);
        if (down && !g_aim_key_was_down) {
            g_aimbot_active = !g_aimbot_active;
            note("aimbot %s", g_aimbot_active ? "ON" : "OFF");
        }
        g_aim_key_was_down = down;
    }
    if (g_triggerbot_enabled) {
        bool down = key_is_down(g_triggerbot_key_type, g_triggerbot_key_code);
        if (down && !g_trig_key_was_down) {
            g_triggerbot_active = !g_triggerbot_active;
            note("triggerbot %s", g_triggerbot_active ? "ON" : "OFF");
        }
        g_trig_key_was_down = down;
    }

    if (!g_aimbot_active && !g_triggerbot_active) goto reschedule;

    // Get main camera
    Il2CppObject *cam = Camera_get_main ? Camera_get_main() : NULL;
    if (!cam) goto reschedule;

    Il2CppObject *cam_tf = Component_get_transform ? Component_get_transform(cam) : NULL;
    if (!cam_tf) goto reschedule;

    Vec3 cam_pos;
    if (!get_position(cam_tf, &cam_pos)) goto reschedule;

    // ── aimbot ──
    if (g_aimbot_active) {
        int player_count = 0;
        Il2CppObject **players = get_player_objects(&player_count);
        if (players && player_count > 0) {
            float best_dist = 1e30f;
            Vec3 best_pos = {0};
            bool found_target = false;

            for (int i = 0; i < player_count; i++) {
                if (!players[i]) continue;
                if (is_local_player(players[i], cam_pos)) continue;

                Il2CppObject *tf = get_transform(players[i]);
                if (!tf) continue;
                Vec3 pos;
                if (!get_position(tf, &pos)) continue;

                // Aim at head height (approx +1.5 units above transform origin)
                pos.y += 1.5f;

                float dist = vec3_dist_sq(pos, cam_pos);
                if (dist < best_dist) {
                    best_dist = dist;
                    best_pos = pos;
                    found_target = true;
                }
            }

            if (found_target && Transform_set_eulerAngles && Transform_get_eulerAngles) {
                Vec3 target_angles = calc_aim_angles(cam_pos, best_pos);
                Vec3 current_angles;
                Transform_get_eulerAngles(cam_tf, (float *)&current_angles);

                float dt = Time_get_deltaTime ? Time_get_deltaTime() : 0.016f;
                float smooth = 1.0f - expf(-dt * kAimSmoothing);

                Vec3 new_angles = {
                    lerp_angle(current_angles.x, target_angles.x, smooth),
                    lerp_angle(current_angles.y, target_angles.y, smooth),
                    0.0f
                };
                Transform_set_eulerAngles(cam_tf, (float *)&new_angles);
            }
        }
    }

    // ── triggerbot ──
    if (g_triggerbot_active && Transform_get_forward) {
        Vec3 forward;
        Transform_get_forward(cam_tf, (float *)&forward);

        if (g_physics_raycast) {
            // Raycast from camera forward, 200 units, default layers
            // Check if hitting anything on the player layer (typically layer 8 or 9 in PG3D)
            int player_layers = (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 12);
            if (g_physics_raycast((float *)&cam_pos, (float *)&forward, 200.0f, player_layers)) {
                simulate_click();
            }
        }
    }

reschedule:
    dispatch_after_f(dispatch_time(0, (int64_t)kTickMs * 1000000), MAIN_QUEUE, NULL, cheat_tick);
}

// ── initialization retry loop ──

static void init_tick(void *unused) {
    (void)unused;
    if (g_il2cpp_ready) {
        // Start the cheat tick
        dispatch_after_f(dispatch_time(0, (int64_t)kTickMs * 1000000), MAIN_QUEUE, NULL, cheat_tick);
        return;
    }

    g_retry_count++;
    if (g_retry_count > kRetryLimit) {
        note("timed out waiting for IL2CPP; cheats inactive.");
        return;
    }

    if (!g_game_assembly_found) {
        g_game_assembly_found = find_game_assembly();
        if (!g_game_assembly_found) goto retry;
    }

    if (!resolve_unity_icalls()) goto retry;
    if (!find_player_class()) goto retry;
    resolve_find_objects();
    resolve_physics();

    g_il2cpp_ready = true;
    note("IL2CPP ready — aimbot:%s triggerbot:%s",
         g_aimbot_enabled ? "enabled" : "off",
         g_triggerbot_enabled ? "enabled" : "off");

    // Start the cheat tick immediately
    dispatch_after_f(dispatch_time(0, (int64_t)kTickMs * 1000000), MAIN_QUEUE, NULL, cheat_tick);
    return;

retry:
    dispatch_after_f(dispatch_time(0, (int64_t)kRetryMs * 1000000), MAIN_QUEUE, NULL, init_tick);
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

    parse_key(getenv("PG3D_CHEATS_AIMBOT_KEY"), &g_aimbot_key_type, &g_aimbot_key_code);
    parse_key(getenv("PG3D_CHEATS_TRIGGERBOT_KEY"), &g_triggerbot_key_type, &g_triggerbot_key_code);

    // Default keys if none configured
    if (g_aimbot_key_code < 0)    { g_aimbot_key_type = 1; g_aimbot_key_code = 3; }    // mouse4
    if (g_triggerbot_key_code < 0) { g_triggerbot_key_type = 1; g_triggerbot_key_code = 4; } // mouse5

    note("loaded — aimbot:%s (key type=%d code=%d) triggerbot:%s (key type=%d code=%d)",
         g_aimbot_enabled ? "on" : "off", g_aimbot_key_type, g_aimbot_key_code,
         g_triggerbot_enabled ? "on" : "off", g_triggerbot_key_type, g_triggerbot_key_code);

    dispatch_after_f(dispatch_time(0, 2000LL * 1000000), MAIN_QUEUE, NULL, init_tick);
}
