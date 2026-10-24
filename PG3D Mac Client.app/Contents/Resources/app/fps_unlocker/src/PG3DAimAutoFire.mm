// Objective-C++17 · PG3DAimAutoFire.mm · macOS x86_64 · Apple Clang
/*
 Single-file integration; no local headers or third-party libraries required.

 Build:
 xcrun clang++ -std=c++17 -arch x86_64 -mmacosx-version-min=11.0 \
   -dynamiclib -fblocks -fobjc-arc -O2 -Wall -Wextra -Werror \
   -framework AppKit -framework ApplicationServices PG3DAimAutoFire.mm -o libPG3DAimAutoFire.dylib
 codesign --force --sign - libPG3DAimAutoFire.dylib

 Include this file once in an Objective-C++ target. Keep x86_64 for the
 installed Intel macOS game, including on Apple Silicon. Default autostart
 recognizes the Pixel Gun 3D bundle; define PG3D_NO_AUTOSTART for manual use.
 Manual API: pg3d_aim_start(logPath, optionalConfigPath); pg3d_aim_stop().
 Call start on the main thread. A null logPath uses a per-process temporary log.
 stop is synchronous and may be called from any thread; keep the module loaded
 until the process exits because libdispatch blocks retain code references.

 Controls: - opens/closes the in-window menu; F6 toggles aimbot,
 F7 toggles triggerbot, F8 shows/hides feedback. The menu includes legitimacy
 (1–10), pull radius (angular degrees), Head/Whole Player mode, 3-cat toggle,
 and three weapon-key capture buttons. Arrow-key legitimacy changes are disabled.
 Aim applies at most once per rendered frame. Outside 5 degrees it approaches
 smoothly without reducing strength for manual mouse movement. Pitch is applied
 directly to the camera, leaving its parent container unchanged. Inside 5 degrees
 level 1 locks and suppresses mouse-look; levels 2–10 track progressively more gently.
 Lock ends on target loss, menu/focus loss, death, or disabling aim.
 Trigger acquisition is immediate; repeat pulses require a living enemy under
 the crosshair. Weapon fire rate, cooldowns and ammo still limit actual shots.
 3-cat follows a Unity-observed fire press and generated release, cycling
 1→2→3→1 with a configurable 100–1000 ms pause before switching and 180 ms equip delays. Manual fire/release is also supported.
 This is input-based cycling, not confirmation that a projectile was emitted.
 Slots use keys/buttons already assigned in the game.

 Inputs use the Quartz HID stream, guarded by game focus and hidden cursor.
 Features pause while the controls/game menu is open or focus is lost.
 Mouse buttons can be captured by the aim/trigger and weapon binding buttons.
 The menu draws an explicit arrow; cursor capture/warping is suppressed while open;
 cursor state is changed once on open/close instead of on every timer tick.

 Optional config (read at startup; the controls menu saves changes):
 enabled=1
 aim_enabled=0
 fov=12
 smoothing=14
 max_degrees_per_second=240
 auto_fire=0
 fire_acquire_ms=0
 fire_hold_ms=50
 fire_gap_ms=75
 fire_max_distance=500
 legitimacy=1
 fps_unlock=1
 fps_target=-1

 Without explicit paths, the log and saved controls use NSTemporaryDirectory().
 Define PG3D_NO_AUTOSTART to use only the exported manual lifecycle functions.
 Optional deterministic tests (native architecture, no game interaction):
 xcrun clang++ -std=c++17 -fobjc-arc -fblocks -DPG3D_AUTOFIRE_TEST \
   -framework AppKit -framework ApplicationServices PG3DAimAutoFire.mm -o pg3d-tests && ./pg3d-tests

 Legitimacy 1 preserves maximum aim; 2–10 progressively reduce tracking strength
 and allow mouse input. This controls assistance, not a guaranteed hit percentage. FPS unlocking is integrated (fps_unlock=1, fps_target=-1).
 Runtime readback verifies frame-rate and VSync settings, not achieved FPS.
 Match reacquisition and FPS settings were verified live. The revised HID
 firing backend still needs gameplay verification; unity_held_samples in the
 log measures whether Unity reads the generated left-button state.
*/
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <initializer_list>
#include <array>
#include <chrono>
#include <atomic>
#include <unistd.h>
// C++17 · aim_math.hpp · macOS · Apple Clang
#include <algorithm>
#include <cmath>
namespace aim {
bool shouldLock(float error,bool sameTarget,bool wasLocked){return error<=5.f||(sameTarget&&wasLocked);}
float legitimacyScale(int level) { return 2.f-1.3f*(std::clamp(level,1,10)-1)/9.f; }
float closeTrackingAmount(int level,float dt) {
    level=std::clamp(level,1,10);
    if(level==1)return 1.f;
    float rate=85.f*std::pow(6.f/85.f,(level-2)/8.f);
    return 1.f-std::exp(-rate*std::max(dt,0.f));
}
constexpr float pi = 3.14159265358979323846f;
struct Vec { float x=0, y=0, z=0; };
struct Quat { float x=0, y=0, z=0, w=1; };
inline Vec operator-(Vec a, Vec b) { return {a.x-b.x,a.y-b.y,a.z-b.z}; }
inline float dot(Vec a, Vec b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
inline float length(Vec a) { return std::sqrt(dot(a,a)); }
inline Vec unit(Vec a) { float n=length(a); return n>1e-6f ? Vec{a.x/n,a.y/n,a.z/n}:Vec{}; }
inline bool finite(Vec a) { return std::isfinite(a.x)&&std::isfinite(a.y)&&std::isfinite(a.z); }
inline Quat unit(Quat q) {
    float n=std::sqrt(q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w);
    return n>1e-6f ? Quat{q.x/n,q.y/n,q.z/n,q.w/n}:Quat{};
}
inline Quat inverse(Quat q) { q=unit(q); return {-q.x,-q.y,-q.z,q.w}; }
inline Quat operator*(Quat a, Quat b) {
    return {a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,
            a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,
            a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w,
            a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z};
}
inline Quat yaw(float radians) { return {0,std::sin(radians/2),0,std::cos(radians/2)}; }
inline Vec forward(Quat q) { q=unit(q); return {2*(q.x*q.z+q.w*q.y),2*(q.y*q.z-q.w*q.x),1-2*(q.x*q.x+q.y*q.y)}; }
inline Quat look(Vec d) {
    d=unit(d);
    float y=std::atan2(d.x,d.z), p=-std::asin(std::clamp(d.y,-1.f,1.f));
    return unit(yaw(y)*Quat{std::sin(p/2),0,0,std::cos(p/2)});
}
inline float angle(Vec a, Vec b) {
    if (length(a)<1e-6f||length(b)<1e-6f) return 180;
    return std::acos(std::clamp(dot(unit(a),unit(b)),-1.f,1.f))*180/pi;
}
inline Quat slerp(Quat a, Quat b, float t) {
    a=unit(a); b=unit(b); t=std::clamp(t,0.f,1.f);
    float d=a.x*b.x+a.y*b.y+a.z*b.z+a.w*b.w;
    if(d<0){b={-b.x,-b.y,-b.z,-b.w};d=-d;}
    float x=1-t,y=t;
    if(d<.9995f){float theta=std::acos(std::clamp(d,-1.f,1.f)),s=std::sin(theta);x=std::sin((1-t)*theta)/s;y=std::sin(t*theta)/s;}
    return unit(Quat{x*a.x+y*b.x,x*a.y+y*b.y,x*a.z+y*b.z,x*a.w+y*b.w});
}
}

// The trigger owns only synthetic presses. Physical input always takes priority.
namespace pg3d_fire {
struct AmmoObserver {
    uintptr_t weapon=0;
    int ammo=-1;
    void reset(){weapon=0;ammo=-1;}
    bool observe(uintptr_t current,int count) {
        if(!current||count<0){reset();return false;}
        bool shot=current==weapon&&ammo>=0&&count<ammo;
        weapon=current;ammo=count;return shot;
    }
};
struct WeaponCycle {
    int slot=0;
    bool pending=false;
    double readyAt=0,switchAt=0;
    double switchDelaySeconds=.100; // Pause after fire release, persisted by the GUI.
    void setDelayMs(double ms){if(std::isfinite(ms))switchDelaySeconds=std::clamp(ms,100.,1000.)/1000.;}
    void reset(){slot=0;pending=false;readyAt=0;switchAt=0;}
    void selected(double now){pending=false;readyAt=now+.180;}
    void fireReleased(double now){if(!pending){pending=true;switchAt=now+switchDelaySeconds;}}
    bool canSwitch(double now)const{return pending&&now>=switchAt;}
    // A pending weapon swap does not block firing with the equipped weapon.
    bool ready(double now)const{return now>=readyAt;}
    int nextSlot()const{return (slot+1)%3;}
};
// Physics returns hits in unspecified order. A solid obstruction wins ties.
struct CrosshairHits {
    float enemyDistance=1e30f,blockDistance=1e30f;
    uintptr_t target=0;
    void add(float distance,uintptr_t candidate,bool trigger,bool local) {
        if(local||!std::isfinite(distance)||distance<0)return;
        if(candidate){if(distance<enemyDistance){enemyDistance=distance;target=candidate;}}
        else if(!trigger)blockDistance=std::min(blockDistance,distance);
    }
    uintptr_t result()const{return enemyDistance<blockDistance?target:0;}
};
struct PulseTrigger {
    uintptr_t target=0;
    bool pressed=false;
    double acquiredAt=0,releaseAt=0,nextPressAt=0;
    // Return +1 for press, -1 for release, and 0 for no event.
    int update(double now,uintptr_t candidate,bool allowed,bool manual,
               double acquire,double hold,double gap) {
        if(manual){target=0;pressed=false;return 0;}
        if(!allowed||!candidate){target=0;if(pressed){pressed=false;return -1;}return 0;}
        if(candidate!=target){
            target=candidate;acquiredAt=now;nextPressAt=now+acquire;
            if(pressed){pressed=false;return -1;}
        }
        if(pressed){
            if(now>=releaseAt){pressed=false;nextPressAt=now+gap;return -1;}
            return 0;
        }
        if(now>=nextPressAt&&now-acquiredAt>=acquire){pressed=true;releaseAt=now+hold;return 1;}
        return 0;
    }
};
}
// PG3D_AUTOFIRE_TEST builds the deterministic input-state tests.
#ifndef PG3D_AUTOFIRE_TEST

// UnityPlayer imports these Quartz functions. While the native menu owns
// the cursor, keep Unity from warping, hiding or disconnecting the pointer.
// The guard is process-local and is armed only after unlocking the cursor.
static std::atomic<bool> menuOwnsCursor{false},aimOwnsMouse{false};

static CGError menuWarpCursor(CGPoint point) {
    if(menuOwnsCursor.load(std::memory_order_relaxed))return kCGErrorSuccess;
    return CGWarpMouseCursorPosition(point);
}
static CGError menuAssociateCursor(boolean_t connected) {
    if(menuOwnsCursor.load(std::memory_order_relaxed)&&!connected)return kCGErrorSuccess;
    return CGAssociateMouseAndMouseCursorPosition(connected);
}
static CGError menuHideCursor(CGDirectDisplayID display) {
    if(menuOwnsCursor.load(std::memory_order_relaxed))return kCGErrorSuccess;
    return CGDisplayHideCursor(display);
}
static CGError menuShowCursor(CGDirectDisplayID display) {
    if(menuOwnsCursor.load(std::memory_order_relaxed))return kCGErrorSuccess;
    return CGDisplayShowCursor(display);
}
static void menuMouseDelta(int32_t *x,int32_t *y) {
    if(menuOwnsCursor.load(std::memory_order_relaxed)||aimOwnsMouse.load(std::memory_order_relaxed)){if(x)*x=0;if(y)*y=0;return;}
    CGGetLastMouseDelta(x,y);
}
// dyld keeps calls from the replacement image bound to the original symbols.
__attribute__((used,section("__DATA,__interpose")))
static const struct {const void *replacement;const void *original;} cursorInterposes[]={
    {reinterpret_cast<const void *>(&menuWarpCursor),reinterpret_cast<const void *>(&CGWarpMouseCursorPosition)},
    {reinterpret_cast<const void *>(&menuAssociateCursor),reinterpret_cast<const void *>(&CGAssociateMouseAndMouseCursorPosition)},
    {reinterpret_cast<const void *>(&menuHideCursor),reinterpret_cast<const void *>(&CGDisplayHideCursor)},
    {reinterpret_cast<const void *>(&menuShowCursor),reinterpret_cast<const void *>(&CGDisplayShowCursor)},
    {reinterpret_cast<const void *>(&menuMouseDelta),reinterpret_cast<const void *>(&CGGetLastMouseDelta)},
};

// HOTKEY ENDPOINTS — macOS physical key codes. Press once to toggle.
// F6=97, F7=98, F8=100, F9=101, F10=109. Keep all three distinct.
// Saved aim_key/trigger_key settings override these defaults; remove those
// entries from PG3DControls.conf to apply edited defaults.
static constexpr unsigned short HOTKEY_AIMBOT = 97;     // F6: aimbot ON/OFF
static constexpr unsigned short HOTKEY_TRIGGERBOT = 98; // F7: triggerbot ON/OFF
static constexpr unsigned short HOTKEY_LEGITIMACY_DOWN = 65535; // disabled: legitimacy is in the GUI
static constexpr unsigned short HOTKEY_LEGITIMACY_UP = 65535;   // disabled: legitimacy is in the GUI
static constexpr unsigned short HOTKEY_FEEDBACK = 100;  // F8: feedback SHOW/HIDE
static_assert(HOTKEY_AIMBOT!=HOTKEY_TRIGGERBOT && HOTKEY_AIMBOT!=HOTKEY_FEEDBACK &&
              HOTKEY_TRIGGERBOT!=HOTKEY_FEEDBACK, "Hotkeys must be distinct");

@interface PG3DFeedback : NSTextField
@end
@implementation PG3DFeedback
- (NSView *)hitTest:(NSPoint)point { (void)point; return nil; }
@end

@interface PG3DMenuPointer : NSImageView
@end
@implementation PG3DMenuPointer
- (NSView *)hitTest:(NSPoint)point {(void)point;return nil;}
@end

@interface PG3DControls : NSObject <NSWindowDelegate>
- (void)changeAim:(id)sender;
- (void)changeTrigger:(id)sender;
- (void)bindAim:(id)sender;
- (void)bindTrigger:(id)sender;
- (void)changeLegitimacy:(id)sender;
- (void)changeRadius:(id)sender;
- (void)changeSwitchDelay:(id)sender;
- (void)changeTargetMode:(id)sender;
- (void)changeCategory:(id)sender;
- (void)bindCategory:(id)sender;
@end

namespace {
using P=void *;
using Clock=std::chrono::steady_clock;
FILE *logFile;
dispatch_source_t timer;
bool ready=false, disabled=false, enabled=true;
bool aimEnabled=false,autoFire=false,fireBackendFailed=false,stopped=false,menuVisible=false;
float fireAcquireMs=0,fireHoldMs=50,fireGapMs=15,fireMaxDistance=500;
pg3d_fire::PulseTrigger fireTrigger;
unsigned long firePresses=0,fireReleases=0,unityHeldSamples=0;
bool physicalLeftHeld=false;
static constexpr int64_t generatedMouseTag=0x5047334454524947;
unsigned attempts=0, errors=0;
uint32_t playersHandle=0;
float cone=12, smoothing=6, speed=90;
Clock::time_point lastTick, lastScan, lastStatus;
char configPath[4096]{};
#define API(result,name,...) result (*name)(__VA_ARGS__)=nullptr
API(P,domain_get);
API(const P *,domain_get_assemblies,P,size_t *);
API(P,assembly_get_image,P);
API(P,class_from_name,P,const char *,const char *);
API(P,class_get_parent,P);
API(P,class_get_methods,P,P *);
API(P,class_get_fields,P,P *);
API(bool,class_is_assignable_from,P,P);
API(P,class_get_field_from_name,P,const char *);
API(P,class_get_type,P);
API(int32_t,class_value_size,P,uint32_t *);
API(const char *,method_get_name,P);
API(uint32_t,method_get_param_count,P);
API(P,method_get_param,P,uint32_t);
API(P,method_get_return_type,P);
API(char *,type_get_name,P);
API(P,type_get_object,P);
API(void,free,P);
API(P,thread_current);
API(P,resolve_icall,const char *);
API(P,runtime_invoke,P,P,P *,P *);
API(P,object_unbox,P);
API(P,object_get_class,P);
API(void,field_get_value,P,P,P);
API(P,field_get_type,P);
API(uint32_t,field_get_flags,P);
API(uint32_t,array_length,P);
API(uint32_t,array_object_header_size);
API(uint32_t,gchandle_new,P,bool);
API(P,gchandle_get_target,uint32_t);
API(void,gchandle_free,uint32_t);
#undef API
P playerClass,damageClass;
P findObjects,aliveMethod,componentTransform,cameraMain,allCameras,cameraEnabled;
int (*fpsGet)(),(*vsyncGet)();
void (*fpsSet)(int),(*vsyncSet)(int);
P transformPosition,transformRotation,setRotation,isChildOf,deadMethod,mineMethod,enemyMethod;
P linecast,hitTransform,raycastAll,hitCollider,hitDistance,colliderIsTrigger;
int (*arrayElementSize)(P);
P focusedMethod,mouseMethod,menuKeyMethod;
P cursorVisibleMethod;
P cursorSetVisibleMethod,cursorLockMethod,cursorSetLockMethod;
P photonField,headField,rootField,viewField,damageField;
uint32_t arrayHeader=0;
NSPanel *statusPanel;
NSTextField *statusLabel;
bool reportNow=false,gameFocused=false,rightHeld=false,localFound=false;
size_t playerCount=0;
unsigned enemyCount=0,coneCount=0,visibleCount=0;
unsigned livingCount=0,damageCount=0;
const char *lastState=nullptr;
NSPanel *controlsPanel;
NSBox *controlsRoot;
PG3DMenuPointer *menuPointer;
bool menuPointerHideBalanced=false;
NSSlider *legitimacySlider,*radiusSlider,*switchDelaySlider;
NSTextField *legitimacyValue,*radiusValue,*switchDelayValue;
NSSegmentedControl *targetModeControl;
NSButton *categorySwitch,*categoryButtons[3];
bool wholePlayer=false,categoryEnabled=false,categorySynced=false;
unsigned short categoryKeys[3]={19,20,23}; // defaults: 2, 3, 5
char categoryLabels[3][80]={"2","3","5"};
pg3d_fire::WeaponCycle categoryCycle;
bool pulseSeenByUnity=false,manualCycleSawUnity=false;
uintptr_t lastCategoryLocal=0,retainedTarget=0;
aim::Quat previousAppliedCamera{};
bool havePreviousApplied=false;
Clock::time_point lastActualAimUpdate;
const char *categoryState="Off";
P frameCountMethod;
int lastAimFrame=-1;
NSButton *aimSwitch,*triggerSwitch,*aimBindButton,*triggerBindButton;
NSTextField *bindingHint;
PG3DControls *controlsOwner;
id keyMonitor,focusObserver,terminationObserver;
NSInteger gameWindowNumber=0;
unsigned short aimKey=HOTKEY_AIMBOT,triggerKey=HOTKEY_TRIGGERBOT;
bool feedbackVisible=true;
bool featureKeysDown[5]{};
int legitimacy=1;
int requestedFPS=-1;
bool fpsUnlockEnabled=true;
Clock::time_point lastFPSCheck,retryRuntimeAfter;
unsigned consecutiveErrors=0;
Clock::time_point lastFeatureToggle[5];
NSEventModifierFlags aimModifiers=0,triggerModifiers=0;
char aimKeyLabel[80]="F6",triggerKeyLabel[80]="F7";
int bindingTarget=0,savedCursorLock=0;
bool savedCursorVisible=false,cursorSaved=false,cursorRestorePending=false;
bool menuBracketWasDown=false;
Clock::time_point lastMenuToggle;
void refreshMenu();
void saveConfig();
void closeMenu(bool activateGame=true);
void stopModule(bool restoreCursor=true);
void installControls();
void updateMenuPointer();
void finishMenuPointer();
void pollMenuShortcut();
bool isGameHost() {
    return [NSBundle.mainBundle.bundleIdentifier isEqualToString:@"com.pgcompany.pixelgun3d"];
}
bool isFocusedGame() {
    return isGameHost()&&NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier==getpid();
}
NSWindow *gameWindow() {
    NSWindow *window=NSApp.mainWindow;
    if(window&&window!=controlsPanel&&window!=statusPanel&&window.contentView)return window;
    for(NSWindow *candidate in NSApp.windows)
        if(candidate!=controlsPanel&&candidate!=statusPanel&&candidate.contentView&&candidate.visible&&[candidate.title containsString:@"Pixel Gun 3D"])
            return candidate;
    return nil;
}

void status(const char *message) {
    if(std::strcmp(message,"Tracking")!=0)aimOwnsMouse.store(false,std::memory_order_relaxed);
    NSWindow *game=gameWindow();
    if(!statusLabel) {
        statusLabel=[[PG3DFeedback alloc] initWithFrame:NSMakeRect(16,16,640,32)];
        statusLabel.editable=NO;statusLabel.selectable=NO;statusLabel.bezeled=NO;
        statusLabel.drawsBackground=YES;statusLabel.wantsLayer=YES;
        statusLabel.backgroundColor=[NSColor colorWithCalibratedWhite:.06 alpha:.88];
        statusLabel.font=[NSFont monospacedSystemFontOfSize:14 weight:NSFontWeightBold];
    }
    if(game && statusLabel.superview!=game.contentView)
        [game.contentView addSubview:statusLabel positioned:NSWindowAbove relativeTo:nil];
    NSView *host=statusLabel.superview;
    if(host) {
        CGFloat y=host.isFlipped?16:NSHeight(host.bounds)-48;
        statusLabel.frame=NSMakeRect(16,y,640,32);
    }
    static int renderedState=-1;
    int displayState=(aimEnabled?1:0)|(autoFire?2:0)|(legitimacy<<2)|(fireBackendFailed?64:0);
    if(renderedState!=displayState) {
    NSString *aimText=[NSString stringWithFormat:@"  Aimbot %@",aimEnabled?@"ON":@"OFF"];
    NSString *triggerText=[NSString stringWithFormat:@"   Triggerbot %@%@",autoFire?@"ON":@"OFF",fireBackendFailed?@" (blocked)":@""];
    NSMutableAttributedString *text=[[NSMutableAttributedString alloc] initWithString:aimText
        attributes:@{NSFontAttributeName:statusLabel.font,NSForegroundColorAttributeName:aimEnabled?NSColor.systemGreenColor:NSColor.systemRedColor}];
    [text appendAttributedString:[[NSAttributedString alloc] initWithString:triggerText
        attributes:@{NSFontAttributeName:statusLabel.font,NSForegroundColorAttributeName:autoFire?NSColor.systemGreenColor:NSColor.systemRedColor}]];
    statusLabel.attributedStringValue=text;renderedState=displayState;
    }
    statusLabel.hidden=!feedbackVisible||!isFocusedGame()||menuVisible;
    lastState=message;
    if(reportNow) {
        fprintf(logFile,"CONTROLS aim=%d trigger=%d category=%d radius=%.2f legitimacy=%d category_state=%s\n",aimEnabled,autoFire,categoryEnabled,cone,legitimacy,categoryState);
        fprintf(logFile,"STATUS pid=%d players=%zu local=%d focus=%d right=%d living=%u damage=%u enemies=%u in_cone=%u visible=%u fire_presses=%lu fire_releases=%lu unity_held_samples=%lu state=%s\n",
            getpid(),playerCount,localFound,gameFocused,rightHeld,livingCount,damageCount,enemyCount,coneCount,visibleCount,firePresses,fireReleases,unityHeldSamples,message);
        fflush(logFile);
    }
}

void log(const char *message) { fprintf(logFile,"%s\n",message);fflush(logFile); }
bool bind() {
#define BIND(name) name=reinterpret_cast<decltype(name)>(dlsym(RTLD_DEFAULT,"il2cpp_" #name));if(!name)return false
    BIND(resolve_icall);BIND(domain_get);BIND(domain_get_assemblies);BIND(assembly_get_image);BIND(class_from_name);
    BIND(class_get_parent);BIND(class_get_methods);BIND(class_get_field_from_name);BIND(class_get_type);
    BIND(class_get_fields);BIND(class_is_assignable_from);BIND(field_get_flags);
    BIND(class_value_size);BIND(method_get_name);BIND(method_get_param_count);BIND(method_get_param);
    BIND(method_get_return_type);BIND(type_get_name);BIND(type_get_object);BIND(free);BIND(thread_current);
    BIND(runtime_invoke);BIND(object_unbox);BIND(object_get_class);BIND(field_get_value);BIND(field_get_type);
    BIND(array_length);BIND(array_object_header_size);BIND(gchandle_new);BIND(gchandle_get_target);BIND(gchandle_free);
#undef BIND
    return true;
}
std::string typeName(P type) {
    char *s=type_get_name(type);std::string result=s?s:"";if(s)free(s);return result;
}
P cls(const char *ns,const char *name) {
    size_t count=0;const P *assemblies=domain_get_assemblies(domain_get(),&count);
    if(!assemblies)return nullptr;
    for(size_t i=0;i<count;++i)
        if(P c=class_from_name(assembly_get_image(assemblies[i]),ns,name))return c;
    return nullptr;
}
P method(P c,const char *name,const char *result,std::initializer_list<const char *> args) {
    for(;c;c=class_get_parent(c)) {
        P it=nullptr;
        while(P m=class_get_methods(c,&it)) {
            if(std::strcmp(method_get_name(m),name)||method_get_param_count(m)!=args.size())continue;
            if(typeName(method_get_return_type(m))!=result)continue;
            bool matches=true;uint32_t i=0;
            for(const char *a:args)if(typeName(method_get_param(m,i++))!=a)matches=false;
            if(matches)return m;
        }
    }
    fprintf(logFile,"Missing method: %s\n",name);fflush(logFile);return nullptr;
}
P field(P c,const char *name,const char *type) {
    P f=class_get_field_from_name(c,name);
    if(f&&typeName(field_get_type(f))==type)return f;
    fprintf(logFile,"Missing or changed field: %s\n",name);fflush(logFile);return nullptr;
}
P uniqueInstanceField(P c,const char *type) {
    P found=nullptr;
    for(;c;c=class_get_parent(c)) {
        P iterator=nullptr;
        while(P f=class_get_fields(c,&iterator)) {
            if((field_get_flags(f)&0x10)||typeName(field_get_type(f))!=type)continue;
            if(found){fprintf(logFile,"Ambiguous instance fields of type %s\n",type);fflush(logFile);return nullptr;}
            found=f;
        }
    }
    if(!found){fprintf(logFile,"Missing instance field of type %s\n",type);fflush(logFile);}
    return found;
}
P invoke(P m,P object,P *args=nullptr) {
    if(!m)return nullptr;
    P exception=nullptr;P result=runtime_invoke(m,object,args,&exception);
    if(exception) {
        if(errors++<10){fprintf(logFile,"Managed exception in %s\n",method_get_name(m));fflush(logFile);}
        if(++consecutiveErrors>=10){
            retryRuntimeAfter=Clock::now()+std::chrono::seconds(1);
            consecutiveErrors=0;log("Runtime objects changing; rescanning after a short pause.");
        }
        return nullptr;
    }
    consecutiveErrors=0;
    return result;
}
template<class T> bool value(P m,P object,T &out,P *args=nullptr) {
    P boxed=invoke(m,object,args);if(!boxed)return false;
    P data=object_unbox(boxed);if(!data)return false;
    std::memcpy(&out,data,sizeof(out));return true;
}
bool alive(P o) {if(!o)return false;bool yes=false;P args[]={o};return value(aliveMethod,nullptr,yes,args)&&yes;}
P reference(P o,P f){P r=nullptr;if(o&&f)field_get_value(o,f,&r);return r;}
P transform(P component){return alive(component)?invoke(componentTransform,component):nullptr;}

void loadConfig() {
    FILE *f=fopen(configPath,"r");if(!f)return;
    char line[256];
    while(fgets(line,sizeof(line),f)) {
        float n;
        if(sscanf(line,"enabled=%f",&n)==1)enabled=n!=0;
        else if(sscanf(line,"aim_enabled=%f",&n)==1&&std::isfinite(n))aimEnabled=n!=0;
        else if(sscanf(line,"fov=%f",&n)==1&&std::isfinite(n))cone=std::clamp(n,1.f,90.f);
        else if(sscanf(line,"smoothing=%f",&n)==1&&std::isfinite(n))smoothing=std::clamp(n,1.f,60.f);
        else if(sscanf(line,"max_degrees_per_second=%f",&n)==1&&std::isfinite(n))speed=std::clamp(n,10.f,720.f);
        else if(sscanf(line,"auto_fire=%f",&n)==1&&std::isfinite(n))autoFire=n!=0;
        else if(sscanf(line,"fire_acquire_ms=%f",&n)==1&&std::isfinite(n))fireAcquireMs=std::clamp(n,0.f,1000.f);
        else if(sscanf(line,"fire_hold_ms=%f",&n)==1&&std::isfinite(n))fireHoldMs=std::clamp(n,25.f,500.f);
        else if(sscanf(line,"fire_gap_ms=%f",&n)==1&&std::isfinite(n))fireGapMs=std::clamp(n,25.f,1000.f);
        else if(sscanf(line,"fire_max_distance=%f",&n)==1&&std::isfinite(n))fireMaxDistance=std::clamp(n,1.f,500.f);
        else if(sscanf(line,"whole_player=%f",&n)==1&&std::isfinite(n))wholePlayer=n!=0;
        else if(sscanf(line,"category_switch_delay_ms=%f",&n)==1&&std::isfinite(n))categoryCycle.setDelayMs(n);
        else if(sscanf(line,"category_enabled=%f",&n)==1&&std::isfinite(n))categoryEnabled=n!=0;
        else if(sscanf(line,"category_key_1=%f",&n)==1&&((n>=0&&n<128)||(n>=256&&n<288)))categoryKeys[0]=int(n);
        else if(sscanf(line,"category_key_2=%f",&n)==1&&((n>=0&&n<128)||(n>=256&&n<288)))categoryKeys[1]=int(n);
        else if(sscanf(line,"category_key_3=%f",&n)==1&&((n>=0&&n<128)||(n>=256&&n<288)))categoryKeys[2]=int(n);
        else if(!strncmp(line,"category_label_1=",17)){std::snprintf(categoryLabels[0],80,"%s",line+17);categoryLabels[0][strcspn(categoryLabels[0],"\r\n")]=0;}
        else if(!strncmp(line,"category_label_2=",17)){std::snprintf(categoryLabels[1],80,"%s",line+17);categoryLabels[1][strcspn(categoryLabels[1],"\r\n")]=0;}
        else if(!strncmp(line,"category_label_3=",17)){std::snprintf(categoryLabels[2],80,"%s",line+17);categoryLabels[2][strcspn(categoryLabels[2],"\r\n")]=0;}
        else if(sscanf(line,"intensity=%f",&n)==1&&std::isfinite(n))legitimacy=11-int(std::clamp(n,1.f,10.f)); // Legacy controls migration.
        else if(sscanf(line,"legitimacy=%f",&n)==1&&std::isfinite(n))legitimacy=int(std::clamp(n,1.f,10.f));
        else if(sscanf(line,"fps_unlock=%f",&n)==1&&std::isfinite(n))fpsUnlockEnabled=n!=0;
        else if(sscanf(line,"fps_target=%f",&n)==1&&std::isfinite(n))requestedFPS=n<0?-1:int(std::clamp(n,30.f,1000.f));
        else if(sscanf(line,"feedback_visible=%f",&n)==1&&std::isfinite(n))feedbackVisible=n!=0;
        else if(sscanf(line,"aim_key=%f",&n)==1&&((n>=0&&n<=127)||(n>=256&&n<288)))aimKey=static_cast<unsigned short>(n);
        else if(sscanf(line,"trigger_key=%f",&n)==1&&((n>=0&&n<=127)||(n>=256&&n<288)))triggerKey=static_cast<unsigned short>(n);
        else if(sscanf(line,"aim_modifiers=%f",&n)==1&&std::isfinite(n)&&n>=0&&n<=0x1f0000)aimModifiers=static_cast<NSEventModifierFlags>(n);
        else if(sscanf(line,"trigger_modifiers=%f",&n)==1&&std::isfinite(n)&&n>=0&&n<=0x1f0000)triggerModifiers=static_cast<NSEventModifierFlags>(n);
        else if(!strncmp(line,"aim_label=",10)){std::snprintf(aimKeyLabel,sizeof(aimKeyLabel),"%s",line+10);aimKeyLabel[strcspn(aimKeyLabel,"\r\n")]=0;}
        else if(!strncmp(line,"trigger_label=",14)){std::snprintf(triggerKeyLabel,sizeof(triggerKeyLabel),"%s",line+14);triggerKeyLabel[strcspn(triggerKeyLabel,"\r\n")]=0;}
    }
    fclose(f);
    // Immediate acquisition replaces the old saved delay.
    fireAcquireMs=0;
    // Reserve feedback shortcut even if an older config assigned it elsewhere.
    if(aimKey==HOTKEY_FEEDBACK||triggerKey==HOTKEY_FEEDBACK||triggerKey==aimKey||
       aimKey==HOTKEY_LEGITIMACY_DOWN||aimKey==HOTKEY_LEGITIMACY_UP||
       triggerKey==HOTKEY_LEGITIMACY_DOWN||triggerKey==HOTKEY_LEGITIMACY_UP) {
        aimKey=HOTKEY_AIMBOT;triggerKey=HOTKEY_TRIGGERBOT;aimModifiers=0;triggerModifiers=0;
    }
}
bool setup() {
    playerClass=cls("","Player_move_c");damageClass=cls("","PlayerDamageable");
    if(!playerClass||!damageClass)return false;
    P object=cls("UnityEngine","Object"),component=cls("UnityEngine","Component");
    P camera=cls("UnityEngine","Camera"),tr=cls("UnityEngine","Transform");
    P photon=cls("","PhotonView"),physics=cls("UnityEngine","Physics"),hit=cls("UnityEngine","RaycastHit");
    P application=cls("UnityEngine","Application"),input=cls("UnityEngine","Input");
    P cursor=cls("UnityEngine","Cursor");
    if(!object||!component||!camera||!tr||!photon||!physics||!hit||!application||!input||!cursor)return false;
    cursorVisibleMethod=method(cursor,"get_visible","System.Boolean",{});
    cursorSetVisibleMethod=method(cursor,"set_visible","System.Void",{"System.Boolean"});
    cursorLockMethod=method(cursor,"get_lockState","UnityEngine.CursorLockMode",{});
    cursorSetLockMethod=method(cursor,"set_lockState","System.Void",{"UnityEngine.CursorLockMode"});
    focusedMethod=method(application,"get_isFocused","System.Boolean",{});
    mouseMethod=method(input,"GetMouseButton","System.Boolean",{"System.Int32"});
    menuKeyMethod=method(input,"GetKey","System.Boolean",{"UnityEngine.KeyCode"});
    findObjects=method(object,"FindObjectsOfType","UnityEngine.Object[]",{"System.Type"});
    aliveMethod=method(object,"op_Implicit","System.Boolean",{"UnityEngine.Object"});
    componentTransform=method(component,"get_transform","UnityEngine.Transform",{});
    cameraMain=method(camera,"get_main","UnityEngine.Camera",{});
    allCameras=method(camera,"get_allCameras","UnityEngine.Camera[]",{});
    P behaviour=cls("UnityEngine","Behaviour");
    cameraEnabled=method(behaviour,"get_isActiveAndEnabled","System.Boolean",{});
    fpsGet=reinterpret_cast<decltype(fpsGet)>(resolve_icall("UnityEngine.Application::get_targetFrameRate"));
    fpsSet=reinterpret_cast<decltype(fpsSet)>(resolve_icall("UnityEngine.Application::set_targetFrameRate"));
    vsyncGet=reinterpret_cast<decltype(vsyncGet)>(resolve_icall("UnityEngine.QualitySettings::get_vSyncCount"));
    vsyncSet=reinterpret_cast<decltype(vsyncSet)>(resolve_icall("UnityEngine.QualitySettings::set_vSyncCount"));
    if(!fpsGet||!fpsSet||!vsyncGet||!vsyncSet)log("FPS: one or more native pacing bindings are unavailable.");
    transformPosition=method(tr,"get_position","UnityEngine.Vector3",{});
    transformRotation=method(tr,"get_rotation","UnityEngine.Quaternion",{});
    setRotation=method(tr,"set_rotation","System.Void",{"UnityEngine.Quaternion"});
    isChildOf=method(tr,"IsChildOf","System.Boolean",{"UnityEngine.Transform"});
    deadMethod=method(playerClass,"get_isTargetDead","System.Boolean",{});
    mineMethod=method(photon,"get_isMine","System.Boolean",{});
    enemyMethod=method(damageClass,"IsEnemyTo","System.Boolean",{"Player_move_c"});
    linecast=method(physics,"Linecast","System.Boolean",{"UnityEngine.Vector3","UnityEngine.Vector3","UnityEngine.RaycastHit&","System.Int32","UnityEngine.QueryTriggerInteraction"});
    hitTransform=method(hit,"get_transform","UnityEngine.Transform",{});
    raycastAll=method(physics,"RaycastAll","UnityEngine.RaycastHit[]",{"UnityEngine.Vector3","UnityEngine.Vector3","System.Single","System.Int32","UnityEngine.QueryTriggerInteraction"});
    hitCollider=method(hit,"get_collider","UnityEngine.Collider",{});
    hitDistance=method(hit,"get_distance","System.Single",{});
    P colliderClass=cls("UnityEngine","Collider");
    if(colliderClass)colliderIsTrigger=method(colliderClass,"get_isTrigger","System.Boolean",{});
    arrayElementSize=reinterpret_cast<decltype(arrayElementSize)>(dlsym(RTLD_DEFAULT,"il2cpp_array_element_size"));
    log(raycastAll&&hitCollider&&hitDistance&&colliderIsTrigger&&arrayElementSize?"TRIGGER: multi-hit crosshair detection available.":"TRIGGER: using legacy single-hit detection; optional physics binding unavailable.");
    photonField=field(playerClass,"photonView","PhotonView");
    headField=field(playerClass,"PlayerHeadTransform","UnityEngine.Transform");
    rootField=field(playerClass,"myPlayerTransform","UnityEngine.Transform");
    viewField=field(playerClass,"myCameraContainer","UnityEngine.Transform");
    damageField=uniqueInstanceField(playerClass,"PlayerDamageable");
    frameCountMethod=method(cls("UnityEngine","Time"),"get_frameCount","System.Int32",{});
    arrayHeader=array_object_header_size();
    uint32_t alignment=0;int32_t hitSize=class_value_size(hit,&alignment);
    if(arrayHeader<16||arrayHeader>128||hitSize<1||hitSize>256||alignment>8){log("Unsupported runtime layout.");disabled=true;return false;}
    for(P required:{findObjects,aliveMethod,componentTransform,cameraMain,allCameras,cameraEnabled,transformPosition,transformRotation,setRotation,isChildOf,deadMethod,mineMethod,enemyMethod,linecast,hitTransform,photonField,headField,rootField,viewField,damageField,focusedMethod,mouseMethod,menuKeyMethod,cursorVisibleMethod,cursorSetVisibleMethod,cursorLockMethod,cursorSetLockMethod})
        if(!required){disabled=true;return false;}
    log("READY: - opens controls; hotkeys toggle each feature. No hold is required.");
    return true;
}
void scan() {
    P type=type_get_object(class_get_type(playerClass));P args[]={type};
    P array=invoke(findObjects,nullptr,args);
    uint32_t next=array?gchandle_new(array,false):0;
    if(playersHandle)gchandle_free(playersHandle);
    playersHandle=next;
}
std::vector<P> players() {
    P array=playersHandle?gchandle_get_target(playersHandle):nullptr;
    if(!array)return {};
    uint32_t count=array_length(array);if(count>512)return {};
    std::vector<P> result(count);
    if(count)std::memcpy(result.data(),static_cast<char *>(array)+arrayHeader,count*sizeof(P));
    return result;
}
bool visible(aim::Vec origin,aim::Vec target,P root) {
    std::array<uint64_t,32> hit{};int mask=-5,ignoreTriggers=1;bool blocked=true;
    P args[]={&origin,&target,hit.data(),&mask,&ignoreTriggers};
    if(!value(linecast,nullptr,blocked,args))return false;
    if(!blocked)return true;
    P hitTr=invoke(hitTransform,hit.data());if(!alive(hitTr))return false;
    bool child=false;P parent[]={root};
    return hitTr==root||(value(isChildOf,hitTr,child,parent)&&child);
}
bool set(P tr,aim::Quat q) {
    unsigned before=errors;P args[]={&q};invoke(setRotation,tr,args);return errors==before;
}

bool postFireEvent(bool down) {
    // Feed the normal Quartz input stream; process-addressed events did not
    // update Unity input in live testing. Presses require focused gameplay.
    if(down&&!isFocusedGame())return false;
    if(!CGPreflightPostEventAccess()) {
        // Explicit opt-in: the user must authorize the macOS permission request.
        static bool requested=false;
        const char *request=std::getenv("PG3D_REQUEST_INPUT_ACCESS");
        if(!requested&&request&&std::strcmp(request,"1")==0) {
            requested=true;
            bool granted=CGRequestPostEventAccess();
            fprintf(logFile,"TRIGGER permission request: granted=%d accessibility=%d\n",granted,AXIsProcessTrusted());fflush(logFile);
        }
        if(!CGPreflightPostEventAccess()) {
            log("TRIGGER: macOS event-posting access is unavailable for this process.");
            return false;
        }
    }
    NSWindow *window=gameWindow();if(!window)return !down;
    CGEventRef locationEvent=CGEventCreate(nullptr);
    if(!locationEvent)return false;
    CGPoint point=CGEventGetLocation(locationEvent);CFRelease(locationEvent);
    if(down) {
        bool shown=true;
        if(!value(cursorVisibleMethod,nullptr,shown)||shown)return false;
    }
    CGEventSourceRef source=CGEventSourceCreate(kCGEventSourceStatePrivate);
    CGEventRef event=CGEventCreateMouseEvent(source,down?kCGEventLeftMouseDown:kCGEventLeftMouseUp,point,kCGMouseButtonLeft);
    if(source)CFRelease(source);
    if(!event)return false;
    CGEventSetIntegerValueField(event,kCGMouseEventClickState,1);
    CGEventSetIntegerValueField(event,kCGEventSourceUserData,generatedMouseTag);
    if(down&&!isFocusedGame()){CFRelease(event);return false;}
    CGEventPost(kCGHIDEventTap,event);CFRelease(event);
    if(down)++firePresses;else ++fireReleases;
    return true;
}
void applyTrigger(uintptr_t target,bool allowed) {
    if(fireTrigger.pressed&&ready&&thread_current()) {
        int left=0;bool held=false;P a[]={&left};
        if(value(mouseMethod,nullptr,held,a)&&held){++unityHeldSamples;pulseSeenByUnity=true;}
    }
    double now=std::chrono::duration<double>(Clock::now().time_since_epoch()).count();
    // Own generated events must not be mistaken for physical takeover.
    bool manual=physicalLeftHeld;
    if(!fireTrigger.pressed&&CGEventSourceButtonState(kCGEventSourceStateHIDSystemState,kCGMouseButtonLeft))manual=true;
    int edge=fireTrigger.update(now,target,allowed,manual,fireAcquireMs/1000.,fireHoldMs/1000.,fireGapMs/1000.);
    if(edge) {
        bool delivered=postFireEvent(edge>0);
        if(!delivered) {
            fireTrigger.pressed=false;fireTrigger.target=0;fireBackendFailed=true;
            log("TRIGGER STOP: event dispatch failed.");
        } else if(edge<0&&pulseSeenByUnity&&categoryEnabled&&categorySynced&&!menuVisible&&isFocusedGame()) {
            categoryCycle.fireReleased(std::chrono::duration<double>(Clock::now().time_since_epoch()).count());
            log("3CAT fire input read by Unity, release sent; weapon switch queued.");
        }
        pulseSeenByUnity=false;
    }
    if(edge&&reportNow){fprintf(logFile,"TRIGGER %s dispatched\n",edge>0?"press":"release");fflush(logFile);}
}
bool enemy(P p,P local) {
    if(!p||p==local||!alive(p))return false;
    bool dead=true;if(!value(deadMethod,p,dead)||dead)return false;
    P damage=reference(p,damageField);
    if(!damage||!class_is_assignable_from(damageClass,object_get_class(damage)))return false;
    bool result=false;P args[]={local};return value(enemyMethod,damage,result,args)&&result;
}
bool under(P child,P parent);
uintptr_t crosshairEnemy(P cameraTr,P local,const std::vector<P> &list) {
    aim::Vec origin{};aim::Quat rotation{};
    if(!alive(cameraTr)||!alive(local)||!value(transformPosition,cameraTr,origin)||
       !value(transformRotation,cameraTr,rotation)||!aim::finite(origin))return 0;
    auto direction=aim::forward(rotation);
    if(raycastAll&&hitCollider&&hitDistance&&colliderIsTrigger&&arrayElementSize) {
        float distance=fireMaxDistance;int mask=-5,collideTriggers=2;
        P args[]={&origin,&direction,&distance,&mask,&collideTriggers};
        P hits=invoke(raycastAll,nullptr,args);
        if(!hits)return 0;
        // Keep the managed array pinned while invoking RaycastHit accessors.
        uint32_t handle=gchandle_new(hits,true);
        if(!handle)return 0;
        struct RootedHits {uint32_t handle;~RootedHits(){gchandle_free(handle);}} rooted{handle};
        hits=gchandle_get_target(handle);if(!hits)return 0;
        uint32_t count=array_length(hits);
        int stride=arrayElementSize(object_get_class(hits));
        if(count>4096||stride<1||stride>256)return 0;
        std::vector<std::pair<P,P>> targets;
        for(P p:list)if(enemy(p,local)){P root=reference(p,rootField);if(alive(root))targets.emplace_back(p,root);}
        P localRoot=reference(local,rootField);
        pg3d_fire::CrosshairHits selection;
        for(uint32_t i=0;i<count;++i) {
            P hit=static_cast<char *>(hits)+arrayHeader+i*stride;
            float hitRange=0;if(!value(hitDistance,hit,hitRange))return 0;
            P collider=invoke(hitCollider,hit);if(!alive(collider))continue;
            // Collider.transform identifies child hitboxes even when the rigidbody
            // associated with RaycastHit.transform sits elsewhere in the hierarchy.
            P tr=transform(collider);if(!alive(tr))return 0;
            if(under(tr,localRoot))continue;
            uintptr_t candidate=0;
            for(const auto &entry:targets)if(under(tr,entry.second)){candidate=reinterpret_cast<uintptr_t>(entry.first);break;}
            bool trigger=false;if(!value(colliderIsTrigger,collider,trigger))return 0;
            selection.add(hitRange,candidate,trigger,false);
        }
        if(reportNow){fprintf(logFile,"TRIGGER ray hits=%u enemy_distance=%.3f blocker_distance=%.3f target=%d\n",count,selection.enemyDistance,selection.blockDistance,selection.result()!=0);fflush(logFile);}
        return selection.result();
    }
    aim::Vec end{origin.x+direction.x*fireMaxDistance,origin.y+direction.y*fireMaxDistance,origin.z+direction.z*fireMaxDistance};
    std::array<uint64_t,32> hit{};int mask=-5,ignoreTriggers=2;bool hitSomething=false;
    P args[]={&origin,&end,hit.data(),&mask,&ignoreTriggers};
    if(!value(linecast,nullptr,hitSomething,args)||!hitSomething)return 0;
    P hitTr=invoke(hitTransform,hit.data());if(!alive(hitTr))return 0;
    for(P p:list) {
        if(!enemy(p,local))continue;
        P root=reference(p,rootField);if(!alive(root))continue;
        bool child=false;P parent[]={root};
        if(hitTr==root||(value(isChildOf,hitTr,child,parent)&&child))return reinterpret_cast<uintptr_t>(p);
    }
    return 0;
}
bool under(P child,P parent) {
    if(!alive(child)||!alive(parent))return false;
    if(child==parent)return true;
    bool result=false;P a[]={parent};return value(isChildOf,child,result,a)&&result;
}
P findPlayerCamera(P root,P view) {
    P main=invoke(cameraMain,nullptr),tr=transform(main);bool active=false;
    if(under(tr,view)&&value(cameraEnabled,main,active)&&active)return tr;
    P array=invoke(allCameras,nullptr);if(!array)return nullptr;
    uint32_t count=array_length(array);if(count>128)return nullptr;
    P *cameras=reinterpret_cast<P *>(static_cast<char *>(array)+arrayHeader);
    for(uint32_t i=0;i<count;++i) {
        P camera=cameras[i];active=false;
        if(!alive(camera)||!value(cameraEnabled,camera,active)||!active)continue;
        tr=transform(camera);
        if(under(tr,view))return tr;
    }
    // Some modes reparent the active camera within the player's root.
    if(under(transform(main),root)&&value(cameraEnabled,main,active)&&active)return transform(main);
    return nullptr;
}
void maintainFPS() {
    if(!fpsUnlockEnabled||!fpsGet||!fpsSet||!vsyncGet||!vsyncSet)return;
    auto now=Clock::now();if(now-lastFPSCheck<std::chrono::milliseconds(500))return;
    lastFPSCheck=now;
    int fps=fpsGet(),vsync=vsyncGet();
    bool changed=fps!=requestedFPS||vsync!=0;
    if(vsync!=0)vsyncSet(0);
    if(fps!=requestedFPS)fpsSet(requestedFPS);
    static bool first=true;
    if(changed||first) {
        fprintf(logFile,"FPS readback: target=%d vsync=%d\n",fpsGet(),vsyncGet());fflush(logFile);
        first=false;
    }
}
bool postWeaponKey(unsigned short key) {
    if(!isFocusedGame()||menuVisible||!((key<128)||(key>=256&&key<288))||!CGPreflightPostEventAccess())return false;
    CGEventSourceRef source=CGEventSourceCreate(kCGEventSourceStatePrivate);
    CGEventRef down=nullptr,up=nullptr;
    if(key>=256) {
        int button=key-256;
        CGEventRef location=CGEventCreate(nullptr);
        if(!location){if(source)CFRelease(source);return false;}
        CGPoint point=CGEventGetLocation(location);CFRelease(location);
        CGEventType downType=button==0?kCGEventLeftMouseDown:button==1?kCGEventRightMouseDown:kCGEventOtherMouseDown;
        CGEventType upType=button==0?kCGEventLeftMouseUp:button==1?kCGEventRightMouseUp:kCGEventOtherMouseUp;
        down=CGEventCreateMouseEvent(source,downType,point,static_cast<CGMouseButton>(button));
        up=CGEventCreateMouseEvent(source,upType,point,static_cast<CGMouseButton>(button));
    } else {
        down=CGEventCreateKeyboardEvent(source,key,true);up=CGEventCreateKeyboardEvent(source,key,false);
    }
    if(source)CFRelease(source);
    if(!down||!up){if(down)CFRelease(down);if(up)CFRelease(up);return false;}
    CGEventSetIntegerValueField(down,kCGEventSourceUserData,generatedMouseTag);
    CGEventSetIntegerValueField(up,kCGEventSourceUserData,generatedMouseTag);
    CGEventPost(kCGHIDEventTap,down);CFRelease(down);
    // Keep the key held across an input update, then always release it.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,40*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
        CGEventPost(kCGHIDEventTap,up);CFRelease(up);
    });
    return true;
}
bool updateCategory(P local,bool allowed) {
    if(!categoryEnabled||!allowed||!local) {
        categoryState=categoryEnabled?"Waiting for gameplay":"Off";
        categorySynced=false;categoryCycle.reset();lastCategoryLocal=0;manualCycleSawUnity=false;
        return true;
    }
    if(lastCategoryLocal!=reinterpret_cast<uintptr_t>(local)) {
        categorySynced=false;categoryCycle.reset();lastCategoryLocal=reinterpret_cast<uintptr_t>(local);
    }
    double now=std::chrono::duration<double>(Clock::now().time_since_epoch()).count();
    if(!categorySynced) {
        categoryCycle.reset();
        if(!postWeaponKey(categoryKeys[0])){categoryState="Weapon key failed; trigger remains independent";return true;}
        categorySynced=true;categoryCycle.selected(now);
        log("3CAT selected slot 1; waiting for weapon equip.");return false;
    }
    if(physicalLeftHeld) {
        int left=0;bool held=false;P a[]={&left};
        if(value(mouseMethod,nullptr,held,a)&&held)manualCycleSawUnity=true;
    }
    if(categoryCycle.pending) {
        if(!categoryCycle.canSwitch(now)){
            categoryState="Firing enabled; switch delay running";return categoryCycle.ready(now);
        }
        // Complete the current pulse before switching. Repeated fire releases
        // must not restart the pending swap deadline.
        if(fireTrigger.pressed||physicalLeftHeld){categoryState="Finishing fire input before switch";return categoryCycle.ready(now);}
        int next=categoryCycle.nextSlot();
        if(postWeaponKey(categoryKeys[next])) {
            categoryCycle.slot=next;categoryCycle.selected(now);
            fprintf(logFile,"3CAT selected slot %d after fire input\n",next+1);fflush(logFile);
            categoryState="Waiting for weapon equip";return false;
        }
        categoryCycle.pending=false;categoryState="Weapon key failed; trigger remains independent";return true;
    }
    bool ready=categoryCycle.ready(now);
    categoryState=ready?"Ready for fire input":"Waiting for weapon equip";
    return ready;
}
struct FireFrame {
    std::vector<P> list;
    P local=nullptr,camera=nullptr;
    ~FireFrame() {
        bool allow=ready&&!stopped&&!disabled&&enabled&&autoFire&&!fireBackendFailed&&gameFocused&&isFocusedGame()&&!menuVisible&&local&&camera;
        bool cursorVisible=true;
        if(allow)allow=value(cursorVisibleMethod,nullptr,cursorVisible)&&!cursorVisible;
        bool categoryAllowed=ready&&!stopped&&!disabled&&enabled&&gameFocused&&isFocusedGame()&&!menuVisible&&local&&camera;
        bool categoryCursor=true;
        if(categoryAllowed)categoryAllowed=value(cursorVisibleMethod,nullptr,categoryCursor)&&!categoryCursor;
        bool categoryReady=updateCategory(local,categoryAllowed);
        if(categoryEnabled&&!categoryReady)allow=false;
        uintptr_t target=allow?crosshairEnemy(camera,local,list):0;
        if(reportNow){fprintf(logFile,"TRIGGER gate allowed=%d target=%d physical_button=%d synthetic_down=%d category=%s\n",allow,target!=0,physicalLeftHeld,fireTrigger.pressed,categoryState);fflush(logFile);}
        // Recheck after managed calls: an exception can disable the module.
        applyTrigger(target,allow&&!disabled);
    }
};
void tick() {
    FireFrame frame;
    reportNow=false;
    if(menuVisible&&!isFocusedGame())closeMenu(false);
    if(cursorRestorePending&&isFocusedGame()&&ready&&!disabled&&thread_current()) {
        // Unity may have cached a lock request while its native calls were
        // suppressed. Force a transition so the saved state is actually applied.
        int unlocked=0;bool visible=true;P resetLock[]={&unlocked};P resetVisible[]={&visible};
        invoke(cursorSetLockMethod,nullptr,resetLock);invoke(cursorSetVisibleMethod,nullptr,resetVisible);
        P lockArgs[]={&savedCursorLock};invoke(cursorSetLockMethod,nullptr,lockArgs);
        P visibleArgs[]={&savedCursorVisible};invoke(cursorSetVisibleMethod,nullptr,visibleArgs);
        cursorRestorePending=false;
    }
    if(stopped||disabled)return status("Stopped: check log");
    auto now=Clock::now();
    if(!ready) {
        if(++attempts>120){disabled=true;log("STOP: runtime not ready.");return;}
        if(!bind()||!thread_current()||!domain_get())return;
        ready=setup();
        if(ready){lastTick=now;lastScan=now-std::chrono::seconds(1);lastStatus=lastScan;
            dispatch_source_set_timer(timer,dispatch_time(DISPATCH_TIME_NOW,0),NSEC_PER_SEC/120,NSEC_PER_MSEC);}
        return;
    }
    if(!thread_current())return status("Waiting for runtime");
    pollMenuShortcut();
    if(now<retryRuntimeAfter){lastScan=now-std::chrono::seconds(1);return status("Reacquiring match");}
    maintainFPS();
    float dt=std::clamp(std::chrono::duration<float>(now-lastTick).count(),0.f,.05f);lastTick=now;
    if(now-lastScan>std::chrono::milliseconds(150)){scan();lastScan=now;}
    bool report=now-lastStatus>std::chrono::seconds(2);
    reportNow=report;
    if(report)lastStatus=now;
    frame.list=players();auto &list=frame.list;P local=nullptr;
    P selectedCamera=nullptr;
    playerCount=list.size();livingCount=damageCount=enemyCount=coneCount=visibleCount=0;
    for(P p:list)if(alive(p)) {
        P photon=reference(p,photonField);bool mine=false;
        if(alive(photon)&&value(mineMethod,photon,mine)&&mine){
            P root=reference(p,rootField),view=reference(p,viewField);
            P candidate=findPlayerCamera(root,view);
            if(candidate){local=p;selectedCamera=candidate;break;}
        }
    }
    localFound=local!=nullptr;gameFocused=false;rightHeld=false;
    int button=1;P mouseArgs[]={&button};
    if(!value(focusedMethod,nullptr,gameFocused)||!value(mouseMethod,nullptr,rightHeld,mouseArgs))return status("Input unavailable");
    if(!enabled)return status("Disabled in aim.conf");
    if(menuVisible){updateMenuPointer();havePreviousApplied=false;return status("Paused - controls open");}
    if(!aimEnabled&&!autoFire&&!categoryEnabled)return status("Disabled - press -");
    if(!local){havePreviousApplied=false;lastActualAimUpdate={};retainedTarget=0;return status("Waiting for match");}
    if(!gameFocused&&!report)return status("Waiting for game focus");
    bool dead=true;if(!value(deadMethod,local,dead)||dead)return status("Waiting for respawn");
    P cameraTr=selectedCamera;
    P localRoot=reference(local,rootField),localView=reference(local,viewField);
    if(!alive(cameraTr)||!alive(localRoot)||!alive(localView)||localRoot==localView)return status("Camera unavailable");
    bool cursorShown=true;
    if(!value(cursorVisibleMethod,nullptr,cursorShown)||cursorShown)return status("Paused - game menu");
    frame.local=local;frame.camera=cameraTr;
    if(!aimEnabled){havePreviousApplied=false;return status(fireBackendFailed?"Trigger input unavailable":"Trigger ready");}
    int currentFrame=-1;
    if(value(frameCountMethod,nullptr,currentFrame)&&currentFrame==lastAimFrame)return;
    lastAimFrame=currentFrame;
    float aimDt=lastActualAimUpdate.time_since_epoch().count()?std::clamp(std::chrono::duration<float>(now-lastActualAimUpdate).count(),.001f,.033f):dt;
    lastActualAimUpdate=now;
    aim::Vec origin{};aim::Quat current{};
    if(!value(transformPosition,cameraTr,origin)||!value(transformRotation,cameraTr,current)||!aim::finite(origin))return status("Camera pose unavailable");
    aim::Vec forward=aim::forward(current),bestDirection{};float best=cone,bestScore=cone;P target=nullptr;
    for(P p:list) {
        if(!enemy(p,local))continue;
        ++livingCount;++damageCount;++enemyCount;
        P head=reference(p,headField),root=reference(p,rootField);if(!alive(head)||!alive(root))continue;
        aim::Vec headPoint{},rootPoint{};
        if(!value(transformPosition,head,headPoint)||!value(transformPosition,root,rootPoint))continue;
        const float heights[]={1.f,.78f,.56f,.35f};
        for(int j=0;j<(wholePlayer?4:1);++j) {
            float t=heights[j];
            aim::Vec point{rootPoint.x+(headPoint.x-rootPoint.x)*t,rootPoint.y+(headPoint.y-rootPoint.y)*t,rootPoint.z+(headPoint.z-rootPoint.z)*t};
            if(!aim::finite(point))continue;
            auto direction=point-origin;float distance=aim::length(direction);if(distance<.1f||distance>500)continue;
            float angle=aim::angle(forward,direction);if(angle>=cone)continue;
            ++coneCount;
            bool retained=reinterpret_cast<uintptr_t>(p)==retainedTarget;
            float score=angle*(retained?.8f:1.f);
            if(retained&&aimOwnsMouse.load(std::memory_order_relaxed))score=-1.f+angle*.001f;
            if(score>=bestScore||!visible(origin,point,root))continue;
            ++visibleCount;best=angle;bestScore=score;bestDirection=direction;target=p;
        }
    }
    if(disabled)return status("Stopped: check log");
    if(!target){havePreviousApplied=false;retainedTarget=0;return status(!enemyCount?"No enemy detected":!coneCount?"Enemy outside aim cone":"Target blocked");}
    if(!gameFocused)return status("Waiting for game focus");
    // Level 1 preserves the former maximum aim. Higher levels retain tracking lag.
    bool lockTarget=aim::shouldLock(best,reinterpret_cast<uintptr_t>(target)==retainedTarget,aimOwnsMouse.load(std::memory_order_relaxed));
    float strength=aim::legitimacyScale(legitimacy);
    float amount=std::min(.12f,1-std::exp(-std::min(smoothing,10.f)*strength*aimDt));
    if(best>.001f)amount=std::min(amount,std::min(speed,120.f)*strength*aimDt/best);
    if(lockTarget)amount=aim::closeTrackingAmount(legitimacy,aimDt);
    aimOwnsMouse.store(lockTarget&&legitimacy==1,std::memory_order_relaxed);
    retainedTarget=reinterpret_cast<uintptr_t>(target);
    auto desired=aim::slerp(current,aim::look(bestDirection),amount);
    // Pitch belongs on the optical camera, not myCameraContainer: rotating
    // an offset parent moves the camera around its pivot and can pull the view
    // into the player mesh. Never feed camera animation offsets into that parent.
    auto nextForward=aim::forward(desired);
    float deltaYaw=std::remainder(std::atan2(nextForward.x,nextForward.z)-std::atan2(forward.x,forward.z),2*aim::pi);
    aim::Quat rootQ{};
    if(!value(transformRotation,localRoot,rootQ)||!set(localRoot,aim::yaw(deltaYaw)*rootQ))return status("Root rotation unavailable");
    if(!set(cameraTr,desired))return status("Camera rotation failed");
    havePreviousApplied=value(transformRotation,cameraTr,previousAppliedCamera);
    status("Tracking");
    if(report){aim::Quat after{};if(value(transformRotation,cameraTr,after)){
        fprintf(logFile,"AIM before=%.3f after=%.3f degrees\n",best,aim::angle(aim::forward(after),bestDirection));fflush(logFile);}}
}
void saveConfig() {
    if(!*configPath)return;
    char temporary[4200];std::snprintf(temporary,sizeof(temporary),"%s.%d.tmp",configPath,getpid());
    FILE *f=fopen(temporary,"w");
    if(!f){log("Could not persist controls; current session settings remain active.");return;}
    fprintf(f,"enabled=%d\naim_enabled=%d\nauto_fire=%d\nfov=%.3f\nsmoothing=%.3f\nmax_degrees_per_second=%.3f\n"
        "fire_acquire_ms=%.3f\nfire_hold_ms=%.3f\nfire_gap_ms=%.3f\nfire_max_distance=%.3f\n"
        "aim_key=%u\ntrigger_key=%u\naim_modifiers=%lu\ntrigger_modifiers=%lu\naim_label=%s\ntrigger_label=%s\n",
        enabled,aimEnabled,autoFire,cone,smoothing,speed,fireAcquireMs,fireHoldMs,fireGapMs,fireMaxDistance,
        aimKey,triggerKey,static_cast<unsigned long>(aimModifiers),static_cast<unsigned long>(triggerModifiers),aimKeyLabel,triggerKeyLabel);
    fprintf(f,"feedback_visible=%d\nlegitimacy=%d\nfps_unlock=%d\nfps_target=%d\n",feedbackVisible,legitimacy,fpsUnlockEnabled,requestedFPS);
    fprintf(f,"category_switch_delay_ms=%.0f\n",categoryCycle.switchDelaySeconds*1000.);
    fprintf(f,"whole_player=%d\ncategory_enabled=%d\n",wholePlayer,categoryEnabled);
    for(int i=0;i<3;++i)fprintf(f,"category_key_%d=%u\ncategory_label_%d=%s\n",i+1,categoryKeys[i],i+1,categoryLabels[i]);
    bool failed=ferror(f)!=0;
    if(fclose(f)!=0)failed=true;
    if(failed||std::rename(temporary,configPath)!=0){std::remove(temporary);log("Could not save controls file.");}
}
void refreshMenu() {
    lastState=nullptr;
    aimSwitch.state=aimEnabled?NSControlStateValueOn:NSControlStateValueOff;
    triggerSwitch.state=autoFire?NSControlStateValueOn:NSControlStateValueOff;
    aimBindButton.title=[NSString stringWithFormat:@"Bind: %s",aimKeyLabel];
    triggerBindButton.title=[NSString stringWithFormat:@"Bind: %s",triggerKeyLabel];
    switchDelaySlider.doubleValue=categoryCycle.switchDelaySeconds*1000.;
    switchDelayValue.stringValue=[NSString stringWithFormat:@"Switch delay: %.0f ms",categoryCycle.switchDelaySeconds*1000.];
    legitimacySlider.integerValue=legitimacy;radiusSlider.doubleValue=cone;
    legitimacyValue.stringValue=[NSString stringWithFormat:@"Legitimacy: %d / 10",legitimacy];
    radiusValue.stringValue=[NSString stringWithFormat:@"Pull radius: %.0f°",cone];
    targetModeControl.selectedSegment=wholePlayer?1:0;
    categorySwitch.state=categoryEnabled?NSControlStateValueOn:NSControlStateValueOff;
    for(int i=0;i<3;++i)categoryButtons[i].title=[NSString stringWithFormat:@"Slot %d: %s",i+1,categoryLabels[i]];
    bindingHint.stringValue=bindingTarget?@"Press a key or click a mouse button. Escape cancels.":@"Hotkeys toggle ON/OFF. Nothing needs to be held.";
}
void updateMenuPointer() {
    if(!menuVisible||!isFocusedGame()||!controlsRoot.superview)return;
    NSView *host=controlsRoot.superview;NSWindow *game=host.window;
    if(!menuPointer) {
        menuPointer=[[PG3DMenuPointer alloc] initWithFrame:NSZeroRect];
        menuPointer.image=NSCursor.arrowCursor.image;
        menuPointer.imageScaling=NSImageScaleNone;
    }
    if(menuPointer.superview!=host)[host addSubview:menuPointer positioned:NSWindowAbove relativeTo:nil];
    NSPoint point=[host convertPoint:[game convertPointFromScreen:NSEvent.mouseLocation] fromView:nil];
    NSSize size=menuPointer.image.size;NSPoint hot=NSCursor.arrowCursor.hotSpot;
    CGFloat y=host.isFlipped?point.y-hot.y:point.y-(size.height-hot.y);
    menuPointer.frame=NSMakeRect(point.x-hot.x,y,size.width,size.height);
    menuPointer.hidden=!NSPointInRect(point,host.bounds);
}
void finishMenuPointer() {
    menuPointer.hidden=YES;
    // Calls from this image reach the original Quartz functions, balancing
    // only the extra hide performed by this menu, not the game's hide count.
    if(menuPointerHideBalanced){CGDisplayShowCursor(kCGDirectMainDisplay);menuPointerHideBalanced=false;}
}
void closeMenu(bool activateGame) {
    if(!menuVisible)return;
    bindingTarget=0;menuVisible=false;menuOwnsCursor.store(false,std::memory_order_relaxed);
    finishMenuPointer();
    controlsRoot.hidden=YES;
    [controlsPanel orderOut:nil];
    if(controlsPanel.parentWindow)[controlsPanel.parentWindow removeChildWindow:controlsPanel];
    NSWindow *game=[NSApp windowWithWindowNumber:gameWindowNumber];
    if(game&&activateGame&&isFocusedGame())[game makeKeyAndOrderFront:nil];
    if(cursorSaved&&ready&&!disabled&&thread_current()&&isFocusedGame()) {
        // Unity may have cached a lock request while its native calls were
        // suppressed. Force a transition so the saved state is actually applied.
        int unlocked=0;bool visible=true;P resetLock[]={&unlocked};P resetVisible[]={&visible};
        invoke(cursorSetLockMethod,nullptr,resetLock);invoke(cursorSetVisibleMethod,nullptr,resetVisible);
        P lockArgs[]={&savedCursorLock};invoke(cursorSetLockMethod,nullptr,lockArgs);
        P visibleArgs[]={&savedCursorVisible};invoke(cursorSetVisibleMethod,nullptr,visibleArgs);
    }
    cursorRestorePending=cursorSaved&&!isFocusedGame();
    cursorSaved=false;lastState=nullptr;
}
void openMenu() {
    if(stopped||!isFocusedGame())return;
    if(menuVisible){closeMenu();return;}
    NSWindow *game=gameWindow();if(!game)return;
    aimOwnsMouse.store(false,std::memory_order_relaxed);
    applyTrigger(0,false);havePreviousApplied=false;
    cursorRestorePending=false;
    game.acceptsMouseMovedEvents=YES;
    gameWindowNumber=game.windowNumber;
    if(ready&&!disabled&&thread_current()) {
        cursorSaved=value(cursorVisibleMethod,nullptr,savedCursorVisible)&&value(cursorLockMethod,nullptr,savedCursorLock);
        int unlocked=0;bool visible=true;
        if(cursorSaved){P a[]={&unlocked};invoke(cursorSetLockMethod,nullptr,a);P b[]={&visible};invoke(cursorSetVisibleMethod,nullptr,b);}
    }
    CGAssociateMouseAndMouseCursorPosition(true);
    [NSCursor.arrowCursor set];
    menuOwnsCursor.store(true,std::memory_order_relaxed);
    // Use a single explicit pointer in the menu regardless of Unity's existing
    // cursor hide count. The extra native hide is balanced exactly on close.
    if(!menuPointerHideBalanced)menuPointerHideBalanced=CGDisplayHideCursor(kCGDirectMainDisplay)==kCGErrorSuccess;
    if(!controlsRoot) {
        controlsOwner=[PG3DControls new];
        controlsRoot=[[NSBox alloc] initWithFrame:NSMakeRect(0,0,520,434)];
        controlsRoot.boxType=NSBoxCustom;controlsRoot.borderWidth=1;
        controlsRoot.appearance=[NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
        controlsRoot.fillColor=[NSColor colorWithCalibratedWhite:.1 alpha:.98];
        controlsRoot.borderColor=NSColor.grayColor;controlsRoot.wantsLayer=YES;
        NSView *content=controlsRoot.contentView;
        NSTextField *title=[NSTextField labelWithString:@"PG3D Controls — press - to close"];
        title.frame=NSMakeRect(20,344,475,24);title.font=[NSFont boldSystemFontOfSize:18];
        [content addSubview:title];
        aimSwitch=[NSButton checkboxWithTitle:@"Aimbot" target:controlsOwner action:@selector(changeAim:)];
        triggerSwitch=[NSButton checkboxWithTitle:@"Triggerbot (instant repeat)" target:controlsOwner action:@selector(changeTrigger:)];
        aimSwitch.frame=NSMakeRect(20,300,260,28);triggerSwitch.frame=NSMakeRect(20,264,280,28);
        aimBindButton=[NSButton buttonWithTitle:@"" target:controlsOwner action:@selector(bindAim:)];
        triggerBindButton=[NSButton buttonWithTitle:@"" target:controlsOwner action:@selector(bindTrigger:)];
        aimBindButton.frame=NSMakeRect(320,300,170,28);triggerBindButton.frame=NSMakeRect(320,264,170,28);
        legitimacyValue=[NSTextField labelWithString:@""];legitimacyValue.frame=NSMakeRect(20,224,210,24);
        radiusValue=[NSTextField labelWithString:@""];radiusValue.frame=NSMakeRect(20,186,210,24);
        legitimacySlider=[NSSlider sliderWithValue:legitimacy minValue:1 maxValue:10 target:controlsOwner action:@selector(changeLegitimacy:)];
        legitimacySlider.toolTip=@"1: maximum aim with hard lock. 10: gentler tracking with mouse control.";
        legitimacySlider.numberOfTickMarks=10;legitimacySlider.allowsTickMarkValuesOnly=YES;legitimacySlider.frame=NSMakeRect(230,224,260,24);
        radiusSlider=[NSSlider sliderWithValue:cone minValue:1 maxValue:45 target:controlsOwner action:@selector(changeRadius:)];radiusSlider.frame=NSMakeRect(230,186,260,24);
        targetModeControl=[NSSegmentedControl segmentedControlWithLabels:@[@"Head",@"Whole Player"] trackingMode:NSSegmentSwitchTrackingSelectOne target:controlsOwner action:@selector(changeTargetMode:)];
        targetModeControl.frame=NSMakeRect(20,144,470,28);
        categorySwitch=[NSButton checkboxWithTitle:@"3-cat weapon cycle" target:controlsOwner action:@selector(changeCategory:)];categorySwitch.frame=NSMakeRect(20,105,470,28);
        for(int i=0;i<3;++i) {
            categoryButtons[i]=[NSButton buttonWithTitle:@"" target:controlsOwner action:@selector(bindCategory:)];
            categoryButtons[i].tag=i;categoryButtons[i].frame=NSMakeRect(20+i*160,67,150,28);[content addSubview:categoryButtons[i]];
        }
        bindingHint=[NSTextField wrappingLabelWithString:@""];bindingHint.frame=NSMakeRect(20,10,470,44);
        for(NSView *view in @[aimSwitch,triggerSwitch,aimBindButton,triggerBindButton,legitimacyValue,radiusValue,legitimacySlider,radiusSlider,targetModeControl,categorySwitch,bindingHint])[content addSubview:view];
        for(NSView *view in content.subviews)if(view!=bindingHint){NSRect frame=view.frame;frame.origin.y+=44;view.frame=frame;}
        switchDelayValue=[NSTextField labelWithString:@""];switchDelayValue.frame=NSMakeRect(20,67,210,24);
        switchDelaySlider=[NSSlider sliderWithValue:categoryCycle.switchDelaySeconds*1000. minValue:100 maxValue:1000 target:controlsOwner action:@selector(changeSwitchDelay:)];
        switchDelaySlider.frame=NSMakeRect(230,67,260,24);switchDelaySlider.continuous=YES;
        switchDelaySlider.toolTip=@"Pause after releasing fire before switching weapons (100–1000 ms).";
        [content addSubview:switchDelayValue];[content addSubview:switchDelaySlider];
    }
    NSView *host=game.contentView;
    if(controlsRoot.superview!=host)[host addSubview:controlsRoot positioned:NSWindowAbove relativeTo:nil];
    controlsRoot.frame=NSMakeRect((NSWidth(host.bounds)-520)/2,(NSHeight(host.bounds)-434)/2,520,434);
    controlsRoot.hidden=NO;
    menuVisible=true;physicalLeftHeld=false;refreshMenu();[game makeFirstResponder:controlsRoot];updateMenuPointer();
    log("MENU opened inside the focused Pixel Gun 3D window.");
}
void toggleMenuShortcut() {
    auto now=Clock::now();
    if(now-lastMenuToggle<std::chrono::milliseconds(250))return;
    lastMenuToggle=now;openMenu();
}
// Shared endpoints used by both AppKit events and Unity input polling.
void toggleFeature(unsigned feature) {
    if(feature>4||stopped||!isFocusedGame()||menuVisible)return;
    auto now=Clock::now();
    if(now-lastFeatureToggle[feature]<std::chrono::milliseconds(250))return;
    lastFeatureToggle[feature]=now;
    if(feature==0){aimEnabled=!aimEnabled;if(!aimEnabled)aimOwnsMouse.store(false,std::memory_order_relaxed);}
    else if(feature==1){autoFire=!autoFire;if(!autoFire)applyTrigger(0,false);}
    else if(feature==2){feedbackVisible=!feedbackVisible;statusLabel.hidden=!feedbackVisible;}
    else legitimacy=std::clamp(legitimacy+(feature==4?1:-1),1,10);
    if(feature==1&&autoFire)fireBackendFailed=false;
    saveConfig();refreshMenu();
}
int unityFunctionKey(unsigned short key) {
    switch(key){case 123:return 276;case 124:return 275;case 125:return 274;case 126:return 273;}
    const unsigned short keys[]={122,120,99,118,96,97,98,100,101,109,103,111,105,107,113};
    for(unsigned i=0;i<15;++i)if(keys[i]==key)return 282+int(i);
    return -1;
}
void pollMenuShortcut() {
    if(!isFocusedGame()){
        menuBracketWasDown=false;
        for(bool &down:featureKeysDown)down=false;
        return;
    }
    const unsigned short keys[]={aimKey,triggerKey,HOTKEY_FEEDBACK,HOTKEY_LEGITIMACY_DOWN,HOTKEY_LEGITIMACY_UP};
    const NSEventModifierFlags modifiers[]={aimModifiers,triggerModifiers,0,0,0};
    auto heldModifiers=NSEvent.modifierFlags&(NSEventModifierFlagCommand|NSEventModifierFlagControl|NSEventModifierFlagOption|NSEventModifierFlagShift);
    for(unsigned i=0;i<5;++i) {
        int code=unityFunctionKey(keys[i]);bool held=false;
        if(code>=0){P a[]={&code};value(menuKeyMethod,nullptr,held,a);}
        held=held&&heldModifiers==modifiers[i];
        if(held&&!featureKeysDown[i])toggleFeature(i);
        featureKeysDown[i]=held;
    }
    // Unity KeyCode.RightBracket is the ASCII value 93.
    int rightBracket=45;P args[]={&rightBracket};bool down=false;
    if(value(menuKeyMethod,nullptr,down,args)) {
        if(down&&!menuBracketWasDown)toggleMenuShortcut();
        menuBracketWasDown=down;
    }
}
NSEventModifierFlags keyModifiers(NSEvent *event) {
    return event.modifierFlags&(NSEventModifierFlagCommand|NSEventModifierFlagControl|NSEventModifierFlagOption|NSEventModifierFlagShift);
}
NSString *keyLabel(NSEvent *event) {
    NSString *base=nil;
    const unsigned short functionKeys[]={122,120,99,118,96,97,98,100,101,109,103,111,105,107,113,106,64,79,80,90};
    for(unsigned i=0;i<20;++i)if(event.keyCode==functionKeys[i])base=[NSString stringWithFormat:@"F%u",i+1];
    if(!base)switch(event.keyCode){
        case 36:base=@"Return";break;case 48:base=@"Tab";break;case 49:base=@"Space";break;
        case 51:base=@"Backspace";break;case 123:base=@"Left";break;case 124:base=@"Right";break;
        case 125:base=@"Down";break;case 126:base=@"Up";break;
        default:base=event.charactersIgnoringModifiers.uppercaseString;
    }
    if(!base.length)base=[NSString stringWithFormat:@"Key %u",event.keyCode];
    NSEventModifierFlags m=keyModifiers(event);
    return [NSString stringWithFormat:@"%@%@%@%@%@",m&NSEventModifierFlagControl?@"Ctrl+":@"",
        m&NSEventModifierFlagOption?@"Alt+":@"",m&NSEventModifierFlagShift?@"Shift+":@"",
        m&NSEventModifierFlagCommand?@"Cmd+":@"",base];
}
bool captureBinding(unsigned short code,NSEventModifierFlags mods,NSString *label) {
    if(!bindingTarget)return false;
    if(bindingTarget>=3) {
        int slot=bindingTarget-3;
        bool conflict=code==aimKey||code==triggerKey||code==HOTKEY_FEEDBACK;
        for(int i=0;i<3;++i)if(i!=slot&&categoryKeys[i]==code)conflict=true;
        if(mods||conflict){bindingHint.stringValue=@"Use a distinct weapon button without modifiers.";return true;}
        categoryKeys[slot]=code;std::snprintf(categoryLabels[slot],80,"%s",label.UTF8String);categorySynced=false;
    } else {
        bool conflict=code==HOTKEY_FEEDBACK||(bindingTarget==1?(code==triggerKey&&mods==triggerModifiers):(code==aimKey&&mods==aimModifiers));
        for(int i=0;i<3;++i)if(categoryKeys[i]==code)conflict=true;
        if(conflict){bindingHint.stringValue=@"That button is already assigned. Choose another.";return true;}
        if(bindingTarget==1){aimKey=code;aimModifiers=mods;std::snprintf(aimKeyLabel,80,"%s",label.UTF8String);}
        else {triggerKey=code;triggerModifiers=mods;std::snprintf(triggerKeyLabel,80,"%s",label.UTF8String);}
    }
    bindingTarget=0;saveConfig();refreshMenu();return true;
}
void installControls() {
    NSEventMask mouseMask=NSEventMaskLeftMouseDown|NSEventMaskLeftMouseUp|NSEventMaskRightMouseDown|NSEventMaskRightMouseUp|NSEventMaskOtherMouseDown|NSEventMaskOtherMouseUp;
    NSEventMask motionMask=NSEventMaskMouseMoved|NSEventMaskLeftMouseDragged|NSEventMaskRightMouseDragged|NSEventMaskOtherMouseDragged;
    keyMonitor=[NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskKeyDown|mouseMask|motionMask handler:^NSEvent *(NSEvent *event){
        if(event.type==NSEventTypeMouseMoved||event.type==NSEventTypeLeftMouseDragged||event.type==NSEventTypeRightMouseDragged||event.type==NSEventTypeOtherMouseDragged){if(menuVisible)updateMenuPointer();
            else if(isFocusedGame()&&aimOwnsMouse.load(std::memory_order_relaxed))return nil;
            return event;}
        bool down=event.type==NSEventTypeLeftMouseDown||event.type==NSEventTypeRightMouseDown||event.type==NSEventTypeOtherMouseDown;
        bool up=event.type==NSEventTypeLeftMouseUp||event.type==NSEventTypeRightMouseUp||event.type==NSEventTypeOtherMouseUp;
        if(down||up) {
            CGEventRef native=event.CGEvent;
            // Ignore generated trigger and weapon events for hotkey capture/toggle.
            if(native&&CGEventGetIntegerValueField(native,kCGEventSourceUserData)==generatedMouseTag)return event;
            if(event.buttonNumber==0) {
                physicalLeftHeld=down;
                if(up&&manualCycleSawUnity&&categoryEnabled&&categorySynced&&!menuVisible&&isFocusedGame())categoryCycle.fireReleased(std::chrono::duration<double>(Clock::now().time_since_epoch()).count());
                if(up)manualCycleSawUnity=false;
            }
            if(stopped||!isFocusedGame()||event.buttonNumber<0||event.buttonNumber>31)return event;
            unsigned short code=256+event.buttonNumber;
            auto mods=keyModifiers(event);
            if(menuVisible) {
                updateMenuPointer();
                if(down&&bindingTarget) {
                    NSString *label=event.buttonNumber==0?@"Mouse Left":event.buttonNumber==1?@"Mouse Right":event.buttonNumber==2?@"Mouse Middle":[NSString stringWithFormat:@"Mouse %ld",long(event.buttonNumber+1)];
                    if(captureBinding(code,mods,label))return nil;
                }
                return event;
            }
            if(code==aimKey&&mods==aimModifiers){if(down)toggleFeature(0);return nil;}
            if(code==triggerKey&&mods==triggerModifiers){if(down)toggleFeature(1);return nil;}
            return event;
        }
        if(stopped||!isFocusedGame())return event;
        bool menuKey=[event.characters isEqualToString:@"-"]||[event.charactersIgnoringModifiers isEqualToString:@"-"];
        if(menuKey){if(!event.isARepeat)toggleMenuShortcut();return nil;}
        if(menuVisible) {
            if(event.keyCode==53){if(bindingTarget){bindingTarget=0;refreshMenu();}else closeMenu();return nil;}
            if(bindingTarget&&!event.isARepeat){captureBinding(event.keyCode,keyModifiers(event),keyLabel(event));return nil;}
            return event;
        }
        auto mods=keyModifiers(event);
        if(event.keyCode==HOTKEY_LEGITIMACY_DOWN&&mods==0){if(!event.isARepeat)toggleFeature(3);return nil;}
        if(event.keyCode==HOTKEY_LEGITIMACY_UP&&mods==0){if(!event.isARepeat)toggleFeature(4);return nil;}
        if(event.keyCode==HOTKEY_FEEDBACK&&mods==0){if(!event.isARepeat)toggleFeature(2);return nil;}
        if(event.keyCode==aimKey&&mods==aimModifiers){if(!event.isARepeat)toggleFeature(0);return nil;}
        if(event.keyCode==triggerKey&&mods==triggerModifiers){if(!event.isARepeat)toggleFeature(1);return nil;}
        return event;
    }];
    focusObserver=[[NSNotificationCenter defaultCenter] addObserverForName:NSApplicationWillResignActiveNotification object:nil queue:[NSOperationQueue mainQueue] usingBlock:^(NSNotification *){
        aimOwnsMouse.store(false,std::memory_order_relaxed);applyTrigger(0,false);physicalLeftHeld=false;closeMenu(false);statusLabel.hidden=YES;[statusPanel orderOut:nil];
    }];
    terminationObserver=[[NSNotificationCenter defaultCenter] addObserverForName:NSApplicationWillTerminateNotification object:nil queue:[NSOperationQueue mainQueue] usingBlock:^(NSNotification *){stopModule(false);}];
}
void stopModule(bool restoreCursor) {
    if(stopped)return;
    if(restoreCursor&&menuVisible)closeMenu();
    applyTrigger(0,false);aimOwnsMouse.store(false,std::memory_order_relaxed);menuOwnsCursor.store(false,std::memory_order_relaxed);finishMenuPointer();stopped=true;
    if(timer)dispatch_source_cancel(timer);
    if(keyMonitor){[NSEvent removeMonitor:keyMonitor];keyMonitor=nil;}
    if(focusObserver){[[NSNotificationCenter defaultCenter] removeObserver:focusObserver];focusObserver=nil;}
    if(terminationObserver){[[NSNotificationCenter defaultCenter] removeObserver:terminationObserver];terminationObserver=nil;}
    [menuPointer removeFromSuperview];[controlsRoot removeFromSuperview];[controlsPanel orderOut:nil];[statusPanel orderOut:nil];[statusLabel removeFromSuperview];
    // Keep the managed array handle until process exit: termination notifications
    // may arrive after Unity has already shut down its allocator.
    if(logFile){log("STOPPED: controls removed and generated mouse press released.");}
}
}
@implementation PG3DControls
- (void)changeAim:(id)sender {aimEnabled=[sender state]==NSControlStateValueOn;if(!aimEnabled)aimOwnsMouse.store(false,std::memory_order_relaxed);saveConfig();refreshMenu();}
- (void)changeTrigger:(id)sender {autoFire=[sender state]==NSControlStateValueOn;if(!autoFire)applyTrigger(0,false);saveConfig();refreshMenu();}
- (void)bindAim:(id)sender {(void)sender;bindingTarget=1;refreshMenu();}
- (void)bindTrigger:(id)sender {(void)sender;bindingTarget=2;refreshMenu();}
- (void)changeLegitimacy:(id)sender {legitimacy=std::clamp(int([sender integerValue]),1,10);saveConfig();refreshMenu();}
- (void)changeSwitchDelay:(id)sender {categoryCycle.setDelayMs(std::round([sender doubleValue]));saveConfig();refreshMenu();}
- (void)changeRadius:(id)sender {cone=std::clamp(float([sender doubleValue]),1.f,45.f);saveConfig();refreshMenu();}
- (void)changeTargetMode:(id)sender {wholePlayer=[sender selectedSegment]==1;retainedTarget=0;saveConfig();refreshMenu();}
- (void)changeCategory:(id)sender {categoryEnabled=[sender state]==NSControlStateValueOn;categorySynced=false;saveConfig();refreshMenu();}
- (void)bindCategory:(id)sender {bindingTarget=3+int([sender tag]);refreshMenu();}
- (BOOL)windowShouldClose:(NSWindow *)sender {(void)sender;closeMenu();return NO;}
@end
extern "C" __attribute__((visibility("default"))) void pg3d_aim_start(const char *path,const char *config) {
    if(!isGameHost())return;
    if(logFile)return;
    char defaultLog[4096];
    const char *temporary=NSTemporaryDirectory().fileSystemRepresentation;
    std::snprintf(defaultLog,sizeof(defaultLog),"%spg3d-aim-%d.log",temporary,getpid());
    if(!(logFile=fopen(path?path:defaultLog,"w")))return;
    if(config)std::snprintf(configPath,sizeof(configPath),"%s",config);
    else std::snprintf(configPath,sizeof(configPath),"%spg3d-aim-controls.conf",temporary);
    loadConfig();
    fprintf(logFile,"PG3D Aim + Trigger loaded. pid=%d build=%s %s\nControls: %s\n",getpid(),__DATE__,__TIME__,configPath);fflush(logFile);
    dispatch_async(dispatch_get_main_queue(),^{
        if(stopped)return;
        installControls();
        timer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,dispatch_get_main_queue());
        dispatch_source_set_timer(timer,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC),NSEC_PER_SEC,NSEC_PER_SEC/10);
        dispatch_source_set_event_handler(timer,^{@autoreleasepool{tick();}});
        dispatch_resume(timer);
    });
}
extern "C" __attribute__((visibility("default"))) void pg3d_aim_stop() {
    if([NSThread isMainThread])stopModule();else dispatch_sync(dispatch_get_main_queue(),^{stopModule();});
}
#ifndef PG3D_NO_AUTOSTART
__attribute__((constructor)) static void startAim() {
    dispatch_async(dispatch_get_main_queue(),^{
        if(isGameHost())
            pg3d_aim_start(std::getenv("PG3D_AIM_LOG"),std::getenv("PG3D_AIM_CONFIG"));
    });
}
#endif

#else
#include <cassert>
int main() {
    assert(aim::shouldLock(5.f,false,false));assert(!aim::shouldLock(5.1f,false,false));
    assert(aim::shouldLock(8.f,true,true));assert(!aim::shouldLock(8.f,false,true));
    pg3d_fire::WeaponCycle cycle;
    cycle.selected(0);assert(!cycle.ready(.1));assert(cycle.ready(.181));
    cycle.fireReleased(.2);assert(cycle.ready(.2));assert(!cycle.canSwitch(.299));
    cycle.fireReleased(.21);assert(cycle.canSwitch(.301));assert(cycle.nextSlot()==1);
    cycle.slot=cycle.nextSlot();cycle.selected(.2);assert(cycle.ready(.381));
    cycle.fireReleased(.4);cycle.slot=cycle.nextSlot();cycle.selected(.4);assert(cycle.slot==2);
    cycle.fireReleased(.6);assert(cycle.nextSlot()==0);cycle.reset();assert(cycle.slot==0&&!cycle.pending);
    cycle.setDelayMs(550);cycle.fireReleased(1);assert(!cycle.canSwitch(1.549));assert(cycle.canSwitch(1.551));
    cycle.reset();assert(std::abs(cycle.switchDelaySeconds-.55)<1e-9);
    cycle.setDelayMs(2000);assert(cycle.switchDelaySeconds==1.);cycle.fireReleased(2);assert(!cycle.canSwitch(2.999));assert(cycle.canSwitch(3.));
    cycle.setDelayMs(0);assert(cycle.switchDelaySeconds==.1);
    // A full one-second swap delay must still permit repeated trigger pulses.
    pg3d_fire::WeaponCycle delayed;delayed.setDelayMs(1000);delayed.selected(0);
    pg3d_fire::PulseTrigger repeating;int presses=0;
    for(int i=180;i<1200;++i){
        double now=i/1000.;
        int edge=repeating.update(now,42,delayed.ready(now),false,0,.05,.075);
        if(edge>0)++presses;
        if(edge<0)delayed.fireReleased(now);
    }
    assert(presses>=8);assert(delayed.pending);assert(!delayed.canSwitch(1.229));assert(delayed.canSwitch(1.231));
    pg3d_fire::CrosshairHits hits;
    hits.add(2,0,false,true); // local body is ignored
    hits.add(3,0,true,false); // non-player trigger does not obscure an enemy
    hits.add(20,7,true,false);hits.add(10,8,false,false);
    assert(hits.result()==8); // unsorted hits choose the nearer enemy
    hits.add(5,0,false,false);assert(hits.result()==0); // wall blocks firing
    pg3d_fire::CrosshairHits ties;ties.add(5,8,true,false);ties.add(5,0,false,false);assert(!ties.result());
    pg3d_fire::AmmoObserver ammo;
    assert(!ammo.observe(1,10));assert(!ammo.observe(1,10));
    assert(ammo.observe(1,9)); // a shot
    assert(!ammo.observe(1,12)); // reload
    assert(!ammo.observe(2,3)); // different weapon
    assert(ammo.observe(2,2));
    assert(!ammo.observe(0,-1)); // missing data resets the baseline
    assert(!ammo.observe(2,1));
    assert(aim::legitimacyScale(1)==2.f);
    assert(std::abs(aim::legitimacyScale(10)-.7f)<1e-6f);
    assert(aim::closeTrackingAmount(1,.016f)==1.f);
    assert(aim::closeTrackingAmount(10,.016f)>.08f&&aim::closeTrackingAmount(10,.016f)<.1f);
    for(int level=1;level<10;++level){
        assert(aim::legitimacyScale(level)>aim::legitimacyScale(level+1));
        assert(aim::closeTrackingAmount(level,.016f)>aim::closeTrackingAmount(level+1,.016f));
    }
    float half=aim::closeTrackingAmount(10,.008f),full=aim::closeTrackingAmount(10,.016f);
    assert(std::abs((1-half)*(1-half)-(1-full))<1e-6f);
    pg3d_fire::PulseTrigger p;
    auto step=[&](double time,uintptr_t target,bool allowed=true,bool manual=false){return p.update(time,target,allowed,manual,.060,.050,.075);};
    assert(step(0,1)==0);assert(step(.059,1)==0);assert(step(.061,1)==1);
    assert(step(.080,1)==0);assert(step(.112,1)==-1);assert(step(.15,1)==0);assert(step(.188,1)==1);
    assert(step(.190,0)==-1);assert(!p.pressed);
    assert(step(.3,2)==0);assert(step(.361,2)==1);assert(step(.37,2,false)==-1);
    assert(step(.5,2)==0);assert(step(.561,2)==1);assert(step(.57,3)==-1);
    assert(step(.60,3)==0);assert(step(.631,3)==1);
    assert(step(.64,3,true,true)==0);assert(!p.pressed); // user takes ownership
    assert(step(.65,3)==0);assert(step(.711,3)==1);
    assert(step(.72,3,false)==-1);assert(step(.73,3,false)==0);
    pg3d_fire::PulseTrigger instant;
    assert(instant.update(0,9,true,false,0,.05,.015)==1);
    assert(instant.update(.051,9,true,false,0,.05,.015)==-1);
    assert(instant.update(.067,9,true,false,0,.05,.015)==1);
    assert(instant.update(.068,0,false,false,0,.05,.015)==-1);
    std::puts("PASS: firing during switch delay, crosshair obstruction selection, ammo shot/reload/switch detection, immediate acquisition and repeat,  legitimacy bounds/scaling, acquisition delay, pulse timing, target loss/change, disable/focus loss, manual takeover, no duplicate release");
}
#endif // PG3D_AUTOFIRE_TEST
