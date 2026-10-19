import SwiftUI
import UniformTypeIdentifiers

struct TransportBar: View {
    @ObservedObject var project: MashupProject
    @ObservedObject var engine: AudioEngineManager
    @Environment(\.undoManager) var undoManager

    @State private var exportProgress = false
    @State private var saveProgress = false
    @State private var showSettings = false

    var body: some View {
        HStack(spacing: 16) {
            HStack(spacing: 8) {
                transportButton(systemName: "backward.end.fill", tooltip: "Rewind (Return)") {
                    engine.stop()
                    project.currentTime = 0
                }

                transportButton(
                    systemName: engine.isPlaying ? "pause.fill" : "play.fill",
                    accent: true,
                    tooltip: "Play / Pause (Space)"
                ) {
                    if engine.isPlaying {
                        engine.pause()
                    } else {
                        engine.play(from: project.currentTime)
                    }
                }

                transportButton(systemName: "stop.fill", tooltip: "Stop (Return)") {
                    engine.stop()
                    project.currentTime = 0
                }
            }

            Divider().frame(height: 20)

            Text(formatTime(project.currentTime))
                .font(.system(size: 16, weight: .medium, design: .monospaced))
                .foregroundColor(.white)
                .frame(width: 80)

            Text("/")
                .foregroundColor(.white.opacity(0.3))

            Text(formatTime(project.totalDuration))
                .font(.system(size: 12, design: .monospaced))
                .foregroundColor(.white.opacity(0.4))
                .frame(width: 60)

            Divider().frame(height: 20)

            Button(action: {
                let wasPlaying = engine.isPlaying
                if wasPlaying { engine.pause() }
                project.splitAtPlayhead(undoManager: undoManager)
                if wasPlaying { engine.play(from: project.currentTime) }
            }) {
                Image(systemName: "scissors")
                    .font(.system(size: 12))
                    .foregroundColor(.white.opacity(0.7))
                    .frame(width: 28, height: 28)
                    .background(Color.white.opacity(0.05))
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)
            .help("Split at Playhead (S)")
            .disabled(!project.trackA.isLoaded && !project.trackB.isLoaded)

            Divider().frame(height: 20)

            HStack(spacing: 4) {
                Button(action: { project.loopEnabled.toggle() }) {
                    Image(systemName: "repeat")
                        .font(.system(size: 12))
                        .foregroundColor(project.loopEnabled ? .purple : .white.opacity(0.4))
                        .frame(width: 24, height: 24)
                        .background(project.loopEnabled ? Color.purple.opacity(0.2) : .clear)
                        .cornerRadius(4)
                }
                .buttonStyle(.plain)

                if project.loopEnabled {
                    Text(formatTime(project.loopStart))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.purple.opacity(0.7))
                    Text("-")
                        .foregroundColor(.white.opacity(0.3))
                    Text(formatTime(project.loopEnd))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.purple.opacity(0.7))
                }
            }

            Divider().frame(height: 20)

            HStack(spacing: 4) {
                Image(systemName: "speaker.wave.2.fill")
                    .font(.system(size: 10))
                    .foregroundColor(.white.opacity(0.4))
                Slider(value: $project.masterVolume, in: 0...1)
                    .frame(width: 80)
                    .onChange(of: project.masterVolume) { _, _ in
                        engine.applySettings()
                    }
            }

            Divider().frame(height: 20)

            HStack(spacing: 4) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 10))
                    .foregroundColor(.white.opacity(0.4))
                Slider(
                    value: $project.zoomLevel,
                    in: 0.2...5.0
                )
                .frame(width: 100)
                Text(String(format: "%.1fx", project.zoomLevel))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.white.opacity(0.4))
                    .frame(width: 30)
            }

            Spacer()

            Button(action: { openProject() }) {
                HStack(spacing: 4) {
                    Image(systemName: "folder")
                        .font(.system(size: 11))
                    Text("Open")
                        .font(.system(size: 12, weight: .medium))
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help("Open Project (⌘O)")

            Button(action: { saveProject() }) {
                HStack(spacing: 4) {
                    if saveProgress {
                        ProgressView()
                            .scaleEffect(0.5)
                            .frame(width: 14, height: 14)
                    } else {
                        Image(systemName: "square.and.arrow.down")
                            .font(.system(size: 11))
                    }
                    Text("Save")
                        .font(.system(size: 12, weight: .medium))
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(!project.trackA.isLoaded && !project.trackB.isLoaded)
            .help("Save Project (⌘S)")

            Button(action: { exportMix() }) {
                HStack(spacing: 4) {
                    if exportProgress {
                        ProgressView()
                            .scaleEffect(0.5)
                            .frame(width: 14, height: 14)
                    } else {
                        Image(systemName: "square.and.arrow.up")
                            .font(.system(size: 11))
                    }
                    Text("Export WAV")
                        .font(.system(size: 12, weight: .medium))
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(!project.trackA.isLoaded && !project.trackB.isLoaded)
            .help("Export WAV with all effects applied")

            Button(action: { showSettings = true }) {
                ZStack(alignment: .topTrailing) {
                    Image(systemName: "gearshape")
                        .font(.system(size: 12))
                    if UpdateManager.shared.updateAvailable {
                        Circle()
                            .fill(Color.green)
                            .frame(width: 7, height: 7)
                            .offset(x: 3, y: -3)
                    }
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help(UpdateManager.shared.updateAvailable ? "Update available!" : "Settings")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color(nsColor: NSColor(white: 0.12, alpha: 1)))
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
    }

    private func transportButton(
        systemName: String,
        accent: Bool = false,
        tooltip: String = "",
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: accent ? 16 : 12))
                .foregroundColor(accent ? .white : .white.opacity(0.7))
                .frame(width: accent ? 36 : 28, height: 28)
                .background(accent ? Color.accentColor.opacity(0.3) : Color.white.opacity(0.05))
                .cornerRadius(6)
        }
        .buttonStyle(.plain)
        .help(tooltip)
    }

    private func formatTime(_ t: TimeInterval) -> String {
        let m = Int(t) / 60
        let s = Int(t) % 60
        let ms = Int((t - floor(t)) * 100)
        return String(format: "%d:%02d.%02d", m, s, ms)
    }

    private func saveProject() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "mashup") ?? .json]
        panel.nameFieldStringValue = "project.mashup"
        panel.canCreateDirectories = true

        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            saveProgress = true
            Task {
                do {
                    try ProjectFile.save(project: project, to: url)
                    await MainActor.run { saveProgress = false }
                } catch {
                    await MainActor.run { saveProgress = false }
                }
            }
        }
    }

    private func openProject() {
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

    private func exportMix() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.wav]
        panel.nameFieldStringValue = "mashup_export.wav"
        panel.canCreateDirectories = true

        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            exportProgress = true
            Task {
                do {
                    try await AudioExporter.exportMix(project: project, to: url)
                    await MainActor.run { exportProgress = false }
                    NSWorkspace.shared.activateFileViewerSelecting([url])
                } catch {
                    await MainActor.run { exportProgress = false }
                }
            }
        }
    }
}
