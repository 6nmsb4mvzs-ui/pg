// AuraBG — Apple Music background customization tweak
// Hooks the Now Playing screen and replaces/modifies its background
// Targets iOS 15-17, rootless-compatible

#import <UIKit/UIKit.h>
#import <objc/runtime.h>
#import <QuartzCore/QuartzCore.h>

#define PREFS_PATH @"/var/mobile/Library/Preferences/com.aura.musicbg.plist"
#define PREFS_NOTIFY "com.aura.musicbg/prefsChanged"
#define AURA_BG_TAG 0xA07A
#define AURA_OV_TAG 0xA07B

// ── Preferences ──

static BOOL kEnabled = YES;
static NSInteger kMode = 1;
// 0 = tint overlay (color layer over default blur)
// 1 = gradient (full replacement)
// 2 = solid color (full replacement)
// 3 = enhanced blur (modifies existing blur)

static NSString *kTintColor;
static CGFloat kTintAlpha = 0.35;

static NSString *kGradC1;
static NSString *kGradC2;
static NSString *kGradC3;
static CGFloat kGradAngle = 135;
static BOOL kGradAnim = NO;

static NSString *kSolidColor;

static CGFloat kBlurAlpha = 0.9;
static CGFloat kBlurSat = 1.5;

static BOOL kMiniPlayerTint = YES;

// Preset system — overrides individual colors when active
static NSInteger kPreset = -1;

typedef struct {
    const char *name;
    NSInteger mode;
    const char *c1, *c2, *c3;
    CGFloat angle;
    BOOL anim;
} AuraPreset;

static const AuraPreset kPresets[] = {
    { "midnight",  1, "#0f0c29", "#302b63", "#24243e", 135, NO },
    { "sunset",    1, "#833ab4", "#f12711", "#f5af19", 160, NO },
    { "ocean",     1, "#005c97", "#0083B0", "#00b4d8", 180, YES },
    { "neon",      1, "#ff006e", "#8338ec", "#3a86ff", 135, YES },
    { "forest",    1, "#134e5e", "#71b280", "#0b3d0b", 145, NO },
    { "lavender",  1, "#667eea", "#764ba2", "#f093fb", 135, YES },
    { "ember",     1, "#ff4e00", "#ec9f05", "#ff6b35", 170, NO },
    { "void",      2, "#0d0d14", NULL, NULL, 0, NO },
};
static const NSUInteger kPresetCount = sizeof(kPresets) / sizeof(kPresets[0]);

static UIColor *hexColor(NSString *hex) {
    if (!hex || hex.length < 6) return [UIColor colorWithWhite:0.05 alpha:1];
    NSString *clean = [hex stringByReplacingOccurrencesOfString:@"#" withString:@""];
    if (clean.length < 6) return [UIColor colorWithWhite:0.05 alpha:1];
    unsigned int val = 0;
    [[NSScanner scannerWithString:clean] scanHexInt:&val];
    return [UIColor colorWithRed:((val >> 16) & 0xFF) / 255.0
                           green:((val >> 8) & 0xFF) / 255.0
                            blue:(val & 0xFF) / 255.0
                           alpha:1.0];
}

static void applyPreset(NSInteger idx) {
    if (idx < 0 || idx >= (NSInteger)kPresetCount) return;
    AuraPreset p = kPresets[idx];
    kMode = p.mode;
    if (p.mode == 1) {
        kGradC1 = [NSString stringWithUTF8String:p.c1];
        kGradC2 = p.c2 ? [NSString stringWithUTF8String:p.c2] : kGradC1;
        kGradC3 = p.c3 ? [NSString stringWithUTF8String:p.c3] : kGradC2;
        kGradAngle = p.angle;
        kGradAnim = p.anim;
    } else if (p.mode == 2) {
        kSolidColor = [NSString stringWithUTF8String:p.c1];
    }
}

static void loadPrefs(void) {
    NSDictionary *p = [NSDictionary dictionaryWithContentsOfFile:PREFS_PATH];

    kEnabled      = p[@"enabled"]      ? [p[@"enabled"] boolValue]       : YES;
    kMode         = p[@"mode"]         ? [p[@"mode"] integerValue]       : 1;
    kTintColor    = p[@"tintColor"]    ?: @"#7c3aed";
    kTintAlpha    = p[@"tintAlpha"]    ? [p[@"tintAlpha"] floatValue]    : 0.35;
    kGradC1       = p[@"gradC1"]       ?: @"#1a1a2e";
    kGradC2       = p[@"gradC2"]       ?: @"#16213e";
    kGradC3       = p[@"gradC3"]       ?: @"#0f3460";
    kGradAngle    = p[@"gradAngle"]    ? [p[@"gradAngle"] floatValue]    : 135;
    kGradAnim     = p[@"gradAnim"]     ? [p[@"gradAnim"] boolValue]      : NO;
    kSolidColor   = p[@"solidColor"]   ?: @"#0a0a0f";
    kBlurAlpha    = p[@"blurAlpha"]    ? [p[@"blurAlpha"] floatValue]    : 0.9;
    kBlurSat      = p[@"blurSat"]      ? [p[@"blurSat"] floatValue]     : 1.5;
    kMiniPlayerTint = p[@"miniPlayer"] ? [p[@"miniPlayer"] boolValue]   : YES;
    kPreset       = p[@"preset"]       ? [p[@"preset"] integerValue]    : -1;

    if (kPreset >= 0) applyPreset(kPreset);
}

// ── Gradient builder ──

static CAGradientLayer *makeGradient(CGRect frame) {
    CAGradientLayer *g = [CAGradientLayer layer];
    g.frame = frame;
    g.colors = @[
        (id)hexColor(kGradC1).CGColor,
        (id)hexColor(kGradC2).CGColor,
        (id)hexColor(kGradC3).CGColor,
    ];
    g.locations = @[@0.0, @0.5, @1.0];
    CGFloat rad = kGradAngle * M_PI / 180.0;
    g.startPoint = CGPointMake(0.5 - cosf(rad) * 0.5, 0.5 - sinf(rad) * 0.5);
    g.endPoint   = CGPointMake(0.5 + cosf(rad) * 0.5, 0.5 + sinf(rad) * 0.5);
    g.name = @"auraGrad";
    return g;
}

static void addGradientAnimation(CAGradientLayer *g) {
    CABasicAnimation *a = [CABasicAnimation animationWithKeyPath:@"colors"];
    a.toValue = @[
        (id)hexColor(kGradC2).CGColor,
        (id)hexColor(kGradC3).CGColor,
        (id)hexColor(kGradC1).CGColor,
    ];
    a.duration = 6.0;
    a.autoreverses = YES;
    a.repeatCount = HUGE_VALF;
    a.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
    [g addAnimation:a forKey:@"auraColorShift"];
}

// ── View hierarchy helpers ──

static BOOL isNowPlayingVC(UIViewController *vc) {
    NSString *cls = NSStringFromClass([vc class]);
    // Covers ObjC MusicNowPlayingViewController and Swift _TtC5Music...NowPlaying...
    if ([cls containsString:@"NowPlaying"]) return YES;
    if ([cls containsString:@"nowPlaying"]) return YES;
    if ([cls containsString:@"FullScreen"] && [cls containsString:@"Player"]) return YES;
    return NO;
}

static BOOL isMiniPlayerVC(UIViewController *vc) {
    NSString *cls = NSStringFromClass([vc class]);
    if ([cls containsString:@"MiniPlayer"]) return YES;
    if ([cls containsString:@"miniPlayer"]) return YES;
    if ([cls containsString:@"CompactPlayer"]) return YES;
    return NO;
}

// Depth-first search for background views
static UIView *findBGView(UIView *root, NSUInteger depth) {
    if (depth > 5) return nil;
    CGFloat sw = UIScreen.mainScreen.bounds.size.width;
    CGFloat sh = UIScreen.mainScreen.bounds.size.height;

    for (UIView *sub in root.subviews) {
        CGRect f = sub.frame;
        BOOL fullSize = (f.size.width >= sw * 0.85 && f.size.height >= sh * 0.4);
        if (!fullSize) continue;

        if ([sub isKindOfClass:[UIVisualEffectView class]]) return sub;
        if ([sub isKindOfClass:[UIImageView class]] &&
            sub.contentMode == UIViewContentModeScaleAspectFill) return sub;

        // Check for hosting view with a blur child
        for (UIView *inner in sub.subviews) {
            if ([inner isKindOfClass:[UIVisualEffectView class]]) return inner;
        }
    }
    for (UIView *sub in root.subviews) {
        UIView *found = findBGView(sub, depth + 1);
        if (found) return found;
    }
    return nil;
}

// ── Apply styles ──

static void clearAura(UIView *view) {
    UIView *bg = [view viewWithTag:AURA_BG_TAG];
    UIView *ov = [view viewWithTag:AURA_OV_TAG];
    if (bg) [bg removeFromSuperview];
    if (ov) [ov removeFromSuperview];
}

static void restoreOriginalBG(UIView *root) {
    UIView *bgView = findBGView(root, 0);
    if (bgView) {
        bgView.hidden = NO;
        bgView.alpha = 1.0;
    }
}

static void applyAura(UIViewController *vc) {
    UIView *root = vc.view;
    clearAura(root);

    if (!kEnabled) {
        restoreOriginalBG(root);
        return;
    }

    CGRect bounds = root.bounds;
    UIView *bgView = findBGView(root, 0);

    switch (kMode) {
        case 0: {
            // Tint overlay — keep the default blur, add a colored layer on top
            restoreOriginalBG(root);
            UIView *tint = [[UIView alloc] initWithFrame:bounds];
            tint.tag = AURA_OV_TAG;
            tint.backgroundColor = [hexColor(kTintColor) colorWithAlphaComponent:kTintAlpha];
            tint.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
            tint.userInteractionEnabled = NO;

            if (bgView && bgView.superview) {
                NSUInteger idx = [bgView.superview.subviews indexOfObject:bgView];
                [bgView.superview insertSubview:tint atIndex:MIN(idx + 1, bgView.superview.subviews.count)];
            } else {
                [root insertSubview:tint atIndex:1];
            }
            break;
        }
        case 1: {
            // Gradient — replace background entirely
            UIView *container = [[UIView alloc] initWithFrame:bounds];
            container.tag = AURA_BG_TAG;
            container.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
            container.userInteractionEnabled = NO;
            container.clipsToBounds = YES;

            CAGradientLayer *gl = makeGradient(bounds);
            [container.layer addSublayer:gl];
            if (kGradAnim) addGradientAnimation(gl);

            [root insertSubview:container atIndex:0];
            if (bgView) bgView.hidden = YES;
            break;
        }
        case 2: {
            // Solid color
            UIView *solid = [[UIView alloc] initWithFrame:bounds];
            solid.tag = AURA_BG_TAG;
            solid.backgroundColor = hexColor(kSolidColor);
            solid.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
            solid.userInteractionEnabled = NO;
            [root insertSubview:solid atIndex:0];
            if (bgView) bgView.hidden = YES;
            break;
        }
        case 3: {
            // Enhanced blur — modify the existing background
            if (bgView) {
                bgView.hidden = NO;
                bgView.alpha = kBlurAlpha;

                // Apply saturation boost through a tinted overlay
                UIView *sat = [[UIView alloc] initWithFrame:bgView.bounds];
                sat.tag = AURA_OV_TAG;
                sat.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
                sat.userInteractionEnabled = NO;
                sat.backgroundColor = [UIColor colorWithWhite:0 alpha:1.0 - kBlurAlpha];
                sat.layer.compositingFilter = @"multiplyBlendMode";

                if (bgView.superview) {
                    NSUInteger idx = [bgView.superview.subviews indexOfObject:bgView];
                    [bgView.superview insertSubview:sat atIndex:MIN(idx + 1, bgView.superview.subviews.count)];
                }
            }
            break;
        }
    }
}

static void applyMiniPlayer(UIViewController *vc) {
    if (!kEnabled || !kMiniPlayerTint) return;
    UIView *root = vc.view;
    clearAura(root);

    CGRect bounds = root.bounds;
    UIView *tint = [[UIView alloc] initWithFrame:bounds];
    tint.tag = AURA_OV_TAG;
    tint.backgroundColor = [hexColor(kMode == 1 ? kGradC1 : kMode == 2 ? kSolidColor : kTintColor)
                            colorWithAlphaComponent:0.25];
    tint.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    tint.userInteractionEnabled = NO;
    [root insertSubview:tint atIndex:1];
}

// Keep a weak ref to the active now playing VC so we can refresh on pref changes
static __weak UIViewController *gNowPlayingVC = nil;
static __weak UIViewController *gMiniPlayerVC = nil;

static void prefsChanged(void) {
    loadPrefs();
    dispatch_async(dispatch_get_main_queue(), ^{
        if (gNowPlayingVC) applyAura(gNowPlayingVC);
        if (gMiniPlayerVC) applyMiniPlayer(gMiniPlayerVC);
    });
}

// ── Hooks ──

%hook UIViewController

- (void)viewDidAppear:(BOOL)animated {
    %orig;
    if (isNowPlayingVC(self)) {
        gNowPlayingVC = self;
        // Slight delay to let the app finish laying out its own views
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.15 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            if (gNowPlayingVC == self) applyAura(self);
        });
    } else if (isMiniPlayerVC(self)) {
        gMiniPlayerVC = self;
        applyMiniPlayer(self);
    }
}

- (void)viewWillDisappear:(BOOL)animated {
    %orig;
    if (isNowPlayingVC(self)) {
        clearAura(self.view);
        restoreOriginalBG(self.view);
        if (gNowPlayingVC == self) gNowPlayingVC = nil;
    } else if (isMiniPlayerVC(self)) {
        clearAura(self.view);
        if (gMiniPlayerVC == self) gMiniPlayerVC = nil;
    }
}

- (void)viewDidLayoutSubviews {
    %orig;
    if (self != gNowPlayingVC) return;
    CGRect bounds = self.view.bounds;
    UIView *bg = [self.view viewWithTag:AURA_BG_TAG];
    if (bg) {
        bg.frame = bounds;
        for (CALayer *sub in bg.layer.sublayers) {
            if ([sub.name isEqualToString:@"auraGrad"]) sub.frame = bg.bounds;
        }
    }
    UIView *ov = [self.view viewWithTag:AURA_OV_TAG];
    if (ov) ov.frame = bounds;
}

%end

%ctor {
    @autoreleasepool {
        NSString *bid = NSBundle.mainBundle.bundleIdentifier;
        if (![bid isEqualToString:@"com.apple.Music"]) return;
        loadPrefs();
        CFNotificationCenterAddObserver(
            CFNotificationCenterGetDarwinNotifyCenter(), NULL,
            (CFNotificationCallback)prefsChanged,
            CFSTR(PREFS_NOTIFY), NULL,
            CFNotificationSuspensionBehaviorCoalesce
        );
    }
}
