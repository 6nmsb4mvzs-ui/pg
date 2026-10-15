import SwiftUI

// MARK: - Root

struct ContentView: View {
    @Environment(DownloadManager.self) private var manager

    var body: some View {
        Group {
            if manager.isCheckingDeps {
                VStack(spacing: 12) {
                    ProgressView()
                        .controlSize(.large)
                    Text("Checking for yt-dlp and ffmpeg...")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if !manager.dependenciesReady {
                SetupView()
            } else {
                MainView()
            }
        }
    }
}

// MARK: - Setup

struct SetupView: View {
    @Environment(DownloadManager.self) private var manager

    var body: some View {
        VStack(spacing: 24) {
            Image(systemName: "arrow.down.circle")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(.secondary)

            Text("Install Dependencies")
                .font(.title2.bold())

            Text("SoundGrab uses yt-dlp and ffmpeg to download\nand convert audio at 320kbps MP3.")
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            VStack(alignment: .leading, spacing: 10) {
                DepRow(name: "yt-dlp", installed: manager.ytdlpPath != nil)
                DepRow(name: "ffmpeg", installed: manager.ffmpegPath != nil)
            }
            .padding()
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))

            if manager.isInstalling {
                VStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text(manager.installOutput)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
            } else {
                if !manager.installOutput.isEmpty {
                    Text(manager.installOutput)
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                Button("Install via Homebrew") {
                    Task { await manager.installDependencies() }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .padding(48)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct DepRow: View {
    let name: String
    let installed: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: installed ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(installed ? .green : .red)
            Text(name)
                .font(.body.monospaced())
            Spacer()
            Text(installed ? "Found" : "Missing")
                .font(.caption)
                .foregroundStyle(installed ? .green : .secondary)
        }
    }
}

// MARK: - Main view with tabs

struct MainView: View {
    @Environment(DownloadManager.self) private var manager
    @State private var selectedTab: Platform = .soundcloud

    var body: some View {
        VStack(spacing: 0) {
            TabView(selection: $selectedTab) {
                PlatformTab(platform: .soundcloud)
                    .tabItem { Label("SoundCloud", systemImage: "cloud.fill") }
                    .tag(Platform.soundcloud)
                PlatformTab(platform: .youtube)
                    .tabItem { Label("YouTube", systemImage: "play.rectangle.fill") }
                    .tag(Platform.youtube)
            }

            Divider()

            HStack(spacing: 8) {
                Image(systemName: "folder")
                    .foregroundStyle(.secondary)
                Text(manager.downloadDir.path.replacingOccurrences(of: NSHomeDirectory(), with: "~"))
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)

                Button("Open") {
                    NSWorkspace.shared.open(manager.downloadDir)
                }
                .buttonStyle(.borderless)
                .font(.caption)

                Spacer()

                if manager.isInstalling {
                    ProgressView()
                        .controlSize(.mini)
                    Text(manager.installOutput)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Button("Update yt-dlp") {
                        Task { await manager.updateYTDLP() }
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 8)
            .background(.bar)
        }
    }
}

// MARK: - Platform tab

struct PlatformTab: View {
    let platform: Platform
    @Environment(DownloadManager.self) private var manager
    @State private var urlText = ""
    @State private var importToMusic = true
    @State private var errorMessage: String?

    private var platformItems: [DownloadItem] {
        manager.items.filter { $0.platform == platform }
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 12) {
                HStack(spacing: 8) {
                    TextField(
                        platform == .soundcloud
                            ? "Paste SoundCloud track or playlist link..."
                            : "Paste YouTube video or playlist link...",
                        text: $urlText
                    )
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { startDownload() }

                    Button {
                        if let clip = NSPasteboard.general.string(forType: .string) {
                            urlText = clip.trimmingCharacters(in: .whitespacesAndNewlines)
                        }
                    } label: {
                        Image(systemName: "doc.on.clipboard")
                    }
                    .buttonStyle(.bordered)
                    .help("Paste from clipboard")

                    Button("Download") { startDownload() }
                        .buttonStyle(.borderedProminent)
                        .tint(platform == .soundcloud ? .orange : .red)
                        .disabled(urlText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }

                HStack {
                    Toggle("Import to Apple Music", isOn: $importToMusic)
                        .toggleStyle(.checkbox)

                    if importToMusic {
                        Text("(syncs to iCloud Music Library)")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }

                    Spacer()

                    if platformItems.contains(where: { $0.status == .done }) {
                        Button("Clear Completed") {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                manager.clearCompleted(platform: platform)
                            }
                        }
                        .buttonStyle(.borderless)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                }

                if let msg = errorMessage {
                    Text(msg)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding()

            Divider()

            if platformItems.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: platform.icon)
                        .font(.system(size: 40, weight: .ultraLight))
                        .foregroundStyle(.quaternary)
                    Text("Paste a \(platform.rawValue) link to get started")
                        .foregroundStyle(.secondary)
                    Text("Single tracks and playlists both work")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(platformItems) { item in
                            DownloadRow(item: item)
                            if item.id != platformItems.last?.id {
                                Divider().padding(.leading, 44)
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private func startDownload() {
        let url = urlText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty else { return }

        guard url.lowercased().contains(platform.hostPattern) else {
            errorMessage = "That doesn't look like a \(platform.rawValue) link"
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { errorMessage = nil }
            return
        }

        errorMessage = nil
        urlText = ""
        Task {
            await manager.submitURL(url, platform: platform, importToMusic: importToMusic)
        }
    }
}

// MARK: - Download row

struct DownloadRow: View {
    let item: DownloadItem

    var body: some View {
        HStack(spacing: 12) {
            statusIcon
                .frame(width: 24, height: 24)

            VStack(alignment: .leading, spacing: 3) {
                Text(item.title)
                    .font(.body)
                    .lineLimit(1)

                if case .failed(let msg) = item.status {
                    Text(msg)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .lineLimit(2)
                } else if item.status != .done {
                    Text(item.status.label)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer()

            if item.status == .downloading {
                ProgressView(value: item.progress)
                    .frame(width: 100)
                    .tint(item.platform == .soundcloud ? .orange : .red)
                Text("\(Int(item.progress * 100))%")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .frame(width: 36, alignment: .trailing)
            }

            if let path = item.filePath {
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([path])
                } label: {
                    Image(systemName: "folder")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.borderless)
                .help("Reveal in Finder")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(rowBackground)
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch item.status {
        case .waiting:
            Image(systemName: "clock")
                .foregroundStyle(.secondary)
        case .fetchingInfo, .downloading, .converting:
            ProgressView()
                .controlSize(.small)
        case .importing:
            Image(systemName: "music.note")
                .foregroundStyle(.blue)
                .symbolEffect(.pulse)
        case .done:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failed:
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
        }
    }

    private var rowBackground: some ShapeStyle {
        switch item.status {
        case .done: AnyShapeStyle(Color.green.opacity(0.04))
        case .failed: AnyShapeStyle(Color.red.opacity(0.04))
        default: AnyShapeStyle(Color.clear)
        }
    }
}
