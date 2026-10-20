import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var project = MashupProject()
    @StateObject private var engine = AudioEngineManager()
    @State private var keyMonitor: Any?
    @State private var showEditor = false

    var body: some View {
        Group {
            if showEditor {
                editorView
            } else {
                WelcomeView(project: project, engine: engine) {
                    withAnimation(.easeInOut(duration: 0.25)) {
                        showEditor = true
                    }
                }
            }
        }
        .background(Color(nsColor: NSColor(white: 0.1, alpha: 1)))
        .onAppear {
            engine.project = project
            installKeyMonitor()
            UpdateManager.shared.checkOnLaunch()
        }
        .onDisappear {
            engine.stop()
            if let monitor = keyMonitor {
                NSEvent.removeMonitor(monitor)
                keyMonitor = nil
            }
        }
    }

    private var editorView: some View {
        VStack(spacing: 0) {
            TransportBar(project: project, engine: engine, onHome: {
                withAnimation(.easeInOut(duration: 0.25)) {
                    showEditor = false
                }
            })

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
    }

    private func installKeyMonitor() {
        let eng = engine
        let proj = project
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            // Pass events through to text fields, panels, and sheets
            if let w = event.window {
                let wClass = String(describing: type(of: w))
                if w is NSSavePanel || w is NSOpenPanel
                    || wClass.contains("SavePanel") || wClass.contains("OpenPanel") {
                    return event
                }
                if let resp = w.firstResponder {
                    let rClass = String(describing: type(of: resp))
                    if resp is NSTextView || resp is NSTextField
                        || rClass.contains("TextField") || rClass.contains("TextEditor") {
                        return event
                    }
                }
            }
            if NSApp.keyWindow?.attachedSheet != nil { return event }

            // Only handle shortcuts when the editor is visible
            guard showEditor else { return event }

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
            case 51: // Backspace — delete clip at playhead
                let wasPlaying = eng.isPlaying
                if wasPlaying { eng.pause() }
                proj.deleteAtPlayhead(undoManager: NSApp.keyWindow?.undoManager)
                if wasPlaying { eng.play(from: proj.currentTime) }
                return nil
            case 2: // D — duplicate clip at playhead
                let wasPlaying = eng.isPlaying
                if wasPlaying { eng.pause() }
                proj.duplicateAtPlayhead(undoManager: NSApp.keyWindow?.undoManager)
                if wasPlaying { eng.play(from: proj.currentTime) }
                return nil
            case 18: // 1
                proj.selectedTrack = proj.selectedTrack == .a ? .none : .a
                return nil
            case 19: // 2
                proj.selectedTrack = proj.selectedTrack == .b ? .none : .b
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
            hint("D", "Duplicate Clip")
            hint("⌫", "Delete Clip")
            hint("1 / 2", "Select Track A / B")
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
    guard let window = NSApp.keyWindow ?? NSApp.mainWindow else { return }
    let panel = NSSavePanel()
    panel.allowedContentTypes = [UTType(filenameExtension: "mashup") ?? .json]
    panel.nameFieldStringValue = "project.mashup"
    panel.canCreateDirectories = true
    panel.isExtensionHidden = false

    panel.beginSheetModal(for: window) { response in
        guard response == .OK, let url = panel.url else { return }
        Task {
            try? ProjectFile.save(project: project, to: url)
            await MainActor.run {
                RecentProjectStore.add(url: url)
            }
        }
    }
}

private func openProject(_ project: MashupProject, _ engine: AudioEngineManager) {
    guard let window = NSApp.keyWindow ?? NSApp.mainWindow else { return }
    let panel = NSOpenPanel()
    panel.allowedContentTypes = [UTType(filenameExtension: "mashup") ?? .json]
    panel.allowsMultipleSelection = false
    panel.canChooseDirectories = false
    panel.message = "Open a MashupStudio project"

    panel.beginSheetModal(for: window) { response in
        guard response == .OK, let url = panel.url else { return }
        Task { @MainActor in
            try? await ProjectFile.load(from: url, into: project, engine: engine)
            engine.applySettings()
            RecentProjectStore.add(url: url)
        }
    }
}
