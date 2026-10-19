import SwiftUI

struct ContentView: View {
    @StateObject private var project = MashupProject()
    @StateObject private var engine = AudioEngineManager()

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
        .background(
            KeyboardMonitor(
                onSpace: { togglePlayback() },
                onReturn: {
                    engine.stop()
                    project.currentTime = 0
                },
                onLeft: { nudge(-1) },
                onRight: { nudge(1) }
            )
        )
        .background(Color(nsColor: NSColor(white: 0.1, alpha: 1)))
        .onAppear { engine.project = project }
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

struct KeyboardMonitor: NSViewRepresentable {
    let onSpace: () -> Void
    let onReturn: () -> Void
    let onLeft: () -> Void
    let onRight: () -> Void

    func makeNSView(context: Context) -> NSView {
        let view = MonitorView()
        view.onSpace = onSpace
        view.onReturn = onReturn
        view.onLeft = onLeft
        view.onRight = onRight
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {}

    class MonitorView: NSView {
        var onSpace: (() -> Void)?
        var onReturn: (() -> Void)?
        var onLeft: (() -> Void)?
        var onRight: (() -> Void)?
        private var monitor: Any?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if window != nil && monitor == nil {
                monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                    guard let self else { return event }
                    switch event.keyCode {
                    case 49: self.onSpace?(); return nil
                    case 36: self.onReturn?(); return nil
                    case 123: self.onLeft?(); return nil
                    case 124: self.onRight?(); return nil
                    default: return event
                    }
                }
            }
        }

        override func removeFromSuperview() {
            if let monitor { NSEvent.removeMonitor(monitor) }
            monitor = nil
            super.removeFromSuperview()
        }

        deinit {
            if let monitor { NSEvent.removeMonitor(monitor) }
        }
    }
}
