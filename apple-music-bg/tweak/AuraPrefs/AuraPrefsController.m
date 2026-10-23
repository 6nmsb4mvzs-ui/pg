#import "AuraPrefsController.h"
#import <Preferences/PSSpecifier.h>

#define PREFS_PATH @"/var/mobile/Library/Preferences/com.aura.musicbg.plist"
#define PREFS_NOTIFY "com.aura.musicbg/prefsChanged"

@implementation AuraPrefsController

- (NSArray *)specifiers {
    if (!_specifiers) _specifiers = [self loadSpecifiersFromPlistName:@"Root" target:self];
    return _specifiers;
}

- (id)readPreferenceValue:(PSSpecifier *)specifier {
    NSDictionary *prefs = [NSDictionary dictionaryWithContentsOfFile:PREFS_PATH];
    NSString *key = [specifier propertyForKey:@"key"];
    id val = prefs[key];
    if (!val) val = [specifier propertyForKey:@"default"];
    return val;
}

- (void)setPreferenceValue:(id)value specifier:(PSSpecifier *)specifier {
    NSString *key = [specifier propertyForKey:@"key"];
    NSMutableDictionary *prefs = [NSMutableDictionary dictionaryWithContentsOfFile:PREFS_PATH] ?: [NSMutableDictionary new];
    prefs[key] = value;
    [prefs writeToFile:PREFS_PATH atomically:YES];
    CFNotificationCenterPostNotification(
        CFNotificationCenterGetDarwinNotifyCenter(),
        CFSTR(PREFS_NOTIFY), NULL, NULL, YES
    );
}

- (void)applyPreset:(PSSpecifier *)specifier {
    NSInteger idx = [[specifier propertyForKey:@"presetIndex"] integerValue];
    NSMutableDictionary *prefs = [NSMutableDictionary dictionaryWithContentsOfFile:PREFS_PATH] ?: [NSMutableDictionary new];
    prefs[@"preset"] = @(idx);
    [prefs writeToFile:PREFS_PATH atomically:YES];
    CFNotificationCenterPostNotification(
        CFNotificationCenterGetDarwinNotifyCenter(),
        CFSTR(PREFS_NOTIFY), NULL, NULL, YES
    );
    [self reloadSpecifiers];
}

- (void)resetPrefs:(PSSpecifier *)specifier {
    [@{} writeToFile:PREFS_PATH atomically:YES];
    CFNotificationCenterPostNotification(
        CFNotificationCenterGetDarwinNotifyCenter(),
        CFSTR(PREFS_NOTIFY), NULL, NULL, YES
    );
    [self reloadSpecifiers];
}

@end
