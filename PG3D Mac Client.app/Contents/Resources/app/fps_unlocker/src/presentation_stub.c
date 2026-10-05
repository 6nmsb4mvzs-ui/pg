// Stand-in for presentation_probe.m (Metal drawable timing, diagnostics only),
// whose source isn't in this repository. The SDK-free build links this instead;
// frame pacing itself lives entirely in pg3d_fps_unlock.c.
extern void pg3d_presentation_log(const char *message);

void pg3d_presentation_start(void) {
    pg3d_presentation_log("presentation probe not included in this build (frame pacing unaffected).");
}

void pg3d_presentation_sample(double elapsed) {
    (void)elapsed;
}
