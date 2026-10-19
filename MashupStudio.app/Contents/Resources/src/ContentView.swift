import SwiftUI

struct ContentView: View {
    @StateObject private var project = MashupProject()
    @StateObject private var engine = AudioEngineManager()
    @FocusState private var isFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            TransportBar(project: project, engine: engine)

            Divider().background(Color.white.opacity(0.1))

            HSplitView {
                ScrollView {
                    VStack(spacing: 12) {
                        TrackControlsView(
                            track: project.trackA,
                            project: project,
                            engine: engine,
                            color: .cyan
                        )
                        TrackControlsView(
                            track: project.trackB,
                            project: project,
                            engine: engine,
                            color: .orange
                        )

                        keyboardHints
                    }
                    .padding(10)
                }
                .frame(minWidth: 220, idealWidth: 260, maxWidth: 320)
                .background(Color(nsColor: NSColor(white: 0.09, alpha: 1)))

                TimelineView(project: project, engine: engine)
                    .frame(minWidth: 400)
            }
        }
        .background(Color(nsColor: NSColor(white: 0.1, alpha: 1)))
        .focusable()
        .focused($isFocused)
        .onKeyPress(.space) {
            togglePlayback()
            return .handled
        }
        .onKeyPress(.leftArrow) {
            nudge(-1)
            return .handled
        }
        .onKeyPress(.rightArrow) {
            nudge(1)
            return .handled
        }
        .onKeyPress(.return) {
            engine.stop()
            project.currentTime = 0
            return .handled
        }
        .onAppear {
            engine.project = project
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { isFocused = true }
        }
        .onDisappear { engine.stop() }
    }

    private var keyboardHints: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Shortcuts")
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .foregroundColor(.white.opacity(0.3))
            hint("Space", "Play / Pause")
            hint("Return", "Stop & Rewind")
            hint("← →", "Scrub ±1s")
            hint("⌘Z / ⇧⌘Z", "Undo / Redo")
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: NSColor(white: 0.13, alpha: 1)))
        )
    }

    private func hint(_ key: String, _ label: String) -> some View {
        HStack(spacing: 6) {
            Text(key)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .foregroundColor(.white.opacity(0.5))
                .padding(.horizontal, 4)
                .padding(.vertical, 1)
                .background(Color.white.opacity(0.08))
                .cornerRadius(3)
            Text(label)
                .font(.system(size: 10, design: .monospaced))
                .foregroundColor(.white.opacity(0.25))
        }
    }

    private func togglePlayback() {
        if engine.isPlaying {
            engine.pause()
        } else {
            engine.play(from: project.currentTime)
        }
    }

    private func nudge(_ seconds: TimeInterval) {
        if !engine.isPlaying {
            project.currentTime = max(0, min(project.totalDuration, project.currentTime + seconds))
        }
    }
}
