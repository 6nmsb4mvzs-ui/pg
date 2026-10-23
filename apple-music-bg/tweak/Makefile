ARCHS = arm64 arm64e
TARGET := iphone:clang:latest:15.0
INSTALL_TARGET_PROCESSES = Music

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = AuraBG
AuraBG_FILES = Tweak.x
AuraBG_CFLAGS = -fobjc-arc -Wno-unused-variable
AuraBG_FRAMEWORKS = UIKit CoreGraphics QuartzCore

include $(THEOS_MAKE_PATH)/tweak.mk

SUBPROJECTS += AuraPrefs
include $(THEOS_MAKE_PATH)/aggregate.mk
