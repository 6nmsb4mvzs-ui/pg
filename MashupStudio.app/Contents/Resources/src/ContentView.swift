import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var project = MashupProject()
    @StateObject private var engine = AudioEngineManager()
    @State private var keyMonitor: Any?

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
        .onAppear {
            engine.project = project
            installKeyMonitor()
        }
        .onDisappear {
            engine.stop()
            if let monitor = keyMonitor {
                NSEvent.removeMonitor(monitor)
                keyMonitor = nil
            }
        }
    }

    private func installKeyMonitor() {
        let eng = engine
        let proj = project
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            if let responder = NSApp.keyWindow?.firstResponder,
               responder is NSTextView {
                return event
            }
            switch event.keyCode {
            case 49: // space
                if eng.isPlaying { eng.pause() } else { eng.play(from: proj.currentTime) }
                return nil
            case 36: // return
                eng.stop()
                proj.currentTime = 0
                return nil
            case 1: // S
                if event.modifierFlags.contains(.command) {
                    saveProject(proj, eng)
                    return nil
                }
                let wasPlaying = eng.isPlaying
                if wasPlaying { eng.pause() }
                proj.splitAtPlayhead(undoManager: NSApp.keyWindow?.undoManager)
                if wasPlaying { eng.play(from: proj.currentTime) }
                return nil
            case 31: // O
                if event.modifierFlags.contains(.command) {
                    openProject(proj, eng)
                    return nil
                }
                return event
            case 123: // left arrow
                if !eng.isPlaying {
                    proj.currentTime = max(0, min(proj.totalDuration, proj.currentTime - 1))
                }
                return nil
            case 124: // right arrow
                if !eng.isPlaying {
                    proj.currentTime = max(0, min(proj.totalDuration, proj.currentTime + 1))
                }
                return nil
            default:
                return event
            }
        }
    }

    private var keyboardHints: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Shortcuts")
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .foregroundColor(.white.opacity(0.3))
            hint("Space", "Play / Pause")
            hint("Return", "Stop & Rewind")
            hint("S", "Split at Playhead")
            hint("⌘S", "Save Project")
            hint("⌘O", "Open Project")
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
}

private func saveProject(_ project: MashupProject, _ engine: AudioEngineManager) {
    let panel = NSSavePanel()
    panel.allowedContentTypes = [UTType(filenameExtension: "mashup") ?? .json]
    panel.nameFieldStringValue = "project.mashup"
    panel.canCreateDirectories = true

    panel.begin { response in
        guard response == .OK, let url = panel.url else { return }
        Task {
            try? ProjectFile.save(project: project, to: url)
        }
    }
}

private func openProject(_ project: MashupProject, _ engine: AudioEngineManager) {
    let panel = NSOpenPanel()
    panel.allowedContentTypes = [UTType(filenameExtension: "mashup") ?? .json]
    panel.allowsMultipleSelection = false
    panel.canChooseDirectories = false
    panel.message = "Open a MashupStudio project"

    panel.begin { response in
        guard response == .OK, let url = panel.url else { return }
        Task { @MainActor in
            try? await ProjectFile.load(from: url, into: project, engine: engine)
            engine.applySettings()
        }
    }
}
