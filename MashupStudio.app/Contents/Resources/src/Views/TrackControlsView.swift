import SwiftUI
import UniformTypeIdentifiers

struct TrackControlsView: View {
    @ObservedObject var track: AudioTrack
    @ObservedObject var project: MashupProject
    @ObservedObject var engine: AudioEngineManager
    @Environment(\.undoManager) var undoManager

    let color: Color

    @State private var isDropTarget = false
    @State private var showEQ = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle()
                    .fill(color)
                    .frame(width: 10, height: 10)
                Text("Track \(track.label)")
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
                    .foregroundColor(.white)
                Spacer()
                if track.isLoaded {
                    Button(action: { track.reset() }) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 12))
                            .foregroundColor(.white.opacity(0.3))
                    }
                    .buttonStyle(.plain)
                    .help("Remove track")
                }
            }

            HStack(spacing: 6) {
                Text(track.name)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(.white.opacity(0.7))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button("Open") {
                    openFile()
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }

            if track.isLoaded {
                Divider().background(Color.white.opacity(0.1))

                controlRow("Vol") {
                    Slider(value: Binding(
                        get: { track.volume },
                        set: { track.volume = $0 }
                    ), in: 0...1)
                    .onChange(of: track.volume) { _, _ in
                        engine.applySettings()
                    }
                    Text("\(Int(track.volume * 100))%")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.white.opacity(0.5))
                        .frame(width: 32, alignment: .trailing)
                }

                HStack(spacing: 8) {
                    Button(action: { project.toggleMute(track, undoManager: undoManager) }) {
                        Text("M")
                            .font(.system(size: 11, weight: .bold, design: .monospaced))
                            .foregroundColor(track.isMuted ? .red : .white.opacity(0.5))
                            .frame(width: 28, height: 22)
                            .background(track.isMuted ? Color.red.opacity(0.2) : Color.white.opacity(0.05))
                            .cornerRadius(4)
                    }
                    .buttonStyle(.plain)

                    Button(action: { project.toggleSolo(track, undoManager: undoManager) }) {
                        Text("S")
                            .font(.system(size: 11, weight: .bold, design: .monospaced))
                            .foregroundColor(track.isSolo ? .yellow : .white.opacity(0.5))
                            .frame(width: 28, height: 22)
                            .background(track.isSolo ? Color.yellow.opacity(0.2) : Color.white.opacity(0.05))
                            .cornerRadius(4)
                    }
                    .buttonStyle(.plain)

                    Spacer()

                    Text(formatTime(track.effectiveDuration))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.white.opacity(0.4))
                }

                Divider().background(Color.white.opacity(0.1))

                controlRow("Spd") {
                    Slider(value: Binding(
                        get: { track.playbackRate },
                        set: { track.playbackRate = $0 }
                    ), in: 0.5...2.0, step: 0.05)
                    .onChange(of: track.playbackRate) { _, _ in
                        engine.applySettings()
                    }
                    Text(String(format: "%.2fx", track.playbackRate))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.white.opacity(0.5))
                        .frame(width: 36, alignment: .trailing)
                }

                Divider().background(Color.white.opacity(0.1))

                DisclosureGroup(isExpanded: $showEQ) {
                    VStack(alignment: .leading, spacing: 6) {
                        eqSlider("Lo", value: Binding(
                            get: { track.eqLow },
                            set: { project.setEQ(track, band: .low, to: $0, undoManager: undoManager) }
                        ))
                        eqSlider("Mid", value: Binding(
                            get: { track.eqMid },
                            set: { project.setEQ(track, band: .mid, to: $0, undoManager: undoManager) }
                        ))
                        eqSlider("Hi", value: Binding(
                            get: { track.eqHigh },
                            set: { project.setEQ(track, band: .high, to: $0, undoManager: undoManager) }
                        ))
                        Button("Reset") {
                            project.setEQ(track, band: .low, to: 0, undoManager: undoManager)
                            project.setEQ(track, band: .mid, to: 0, undoManager: undoManager)
                            project.setEQ(track, band: .high, to: 0, undoManager: undoManager)
                        }
                        .buttonStyle(.plain)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.white.opacity(0.3))
                    }
                } label: {
                    Text("EQ")
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .foregroundColor(.white.opacity(0.4))
                }
                .tint(.white.opacity(0.3))
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: NSColor(white: 0.13, alpha: 1)))
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(
                            isDropTarget ? color : color.opacity(0.2),
                            lineWidth: isDropTarget ? 2 : 1
                        )
                )
        )
        .onDrop(of: [.fileURL], isTargeted: $isDropTarget) { providers in
            handleDrop(providers: providers)
        }
    }

    private func controlRow<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 6) {
            Text(label)
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .foregroundColor(.white.opacity(0.4))
                .frame(width: 26, alignment: .leading)
            content()
        }
    }

    private func eqSlider(_ label: String, value: Binding<Float>) -> some View {
        HStack(spacing: 6) {
            Text(label)
                .font(.system(size: 10, design: .monospaced))
                .foregroundColor(.white.opacity(0.4))
                .frame(width: 26, alignment: .leading)
            Slider(value: value, in: -12...12, step: 0.5)
                .onChange(of: value.wrappedValue) { _, _ in
                    engine.applySettings()
                }
            Text(String(format: "%+.0f", value.wrappedValue))
                .font(.system(size: 10, design: .monospaced))
                .foregroundColor(.white.opacity(0.5))
                .frame(width: 28, alignment: .trailing)
        }
    }

    private func formatTime(_ t: TimeInterval) -> String {
        let m = Int(t) / 60
        let s = Int(t) % 60
        return String(format: "%d:%02d", m, s)
    }

    private func openFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.audio]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = "Choose an audio file for Track \(track.label)"
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in
                try? await track.loadFile(url)
                if track.label == "A" {
                    try? engine.loadTrackA(url: url)
                } else {
                    try? engine.loadTrackB(url: url)
                }
            }
        }
    }

    private func handleDrop(providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        provider.loadItem(forTypeIdentifier: "public.file-url", options: nil) { item, _ in
            guard let data = item as? Data,
                  let url = URL(dataRepresentation: data, relativeTo: nil) else { return }
            let exts = ["mp3", "wav", "aiff", "aif", "m4a", "flac", "aac", "ogg", "caf"]
            guard exts.contains(url.pathExtension.lowercased()) else { return }
            Task { @MainActor in
                try? await track.loadFile(url)
                if track.label == "A" {
                    try? engine.loadTrackA(url: url)
                } else {
                    try? engine.loadTrackB(url: url)
                }
            }
        }
        return true
    }
}
