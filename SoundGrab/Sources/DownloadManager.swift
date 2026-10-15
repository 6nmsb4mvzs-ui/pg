import Foundation
import Observation

enum Platform: String, CaseIterable, Equatable {
    case soundcloud = "SoundCloud"
    case youtube = "YouTube"

    var icon: String {
        switch self {
        case .soundcloud: "cloud.fill"
        case .youtube: "play.rectangle.fill"
        }
    }

    var hostPattern: String {
        switch self {
        case .soundcloud: "soundcloud.com"
        case .youtube: "youtu"
        }
    }
}

enum DownloadStatus: Equatable {
    case waiting
    case fetchingInfo
    case downloading
    case converting
    case importing
    case done
    case failed(String)

    var label: String {
        switch self {
        case .waiting: "Queued"
        case .fetchingInfo: "Fetching info..."
        case .downloading: "Downloading..."
        case .converting: "Converting to 320kbps MP3..."
        case .importing: "Importing to Apple Music..."
        case .done: "Done"
        case .failed(let msg): msg
        }
    }
}

enum DownloadError: Error, LocalizedError {
    case processError(String)
    case fileNotFound
    case fileTooSmall(Int)

    var errorDescription: String? {
        switch self {
        case .processError(let msg):
            let lines = msg.components(separatedBy: .newlines).filter { !$0.isEmpty }
            let meaningful = lines.first(where: { $0.contains("ERROR") }) ?? lines.last ?? msg
            let clean = meaningful
                .replacingOccurrences(of: "ERROR: ", with: "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            return String(clean.prefix(200))
        case .fileNotFound:
            return "MP3 file not found after download"
        case .fileTooSmall(let bytes):
            return "File too small (\(bytes) bytes) — likely corrupted"
        }
    }
}

@Observable
final class DownloadItem: Identifiable {
    let id = UUID()
    var title: String
    var status: DownloadStatus = .waiting
    var progress: Double = 0
    var filePath: URL?
    let url: String
    let platform: Platform

    init(title: String, url: String, platform: Platform) {
        self.title = title
        self.url = url
        self.platform = platform
    }
}

@Observable
final class DownloadManager {
    var items: [DownloadItem] = []
    var dependenciesReady = false
    var isCheckingDeps = true
    var isInstalling = false
    var installOutput = ""

    var ytdlpPath: String?
    var ffmpegPath: String?

    let downloadDir: URL

    private static let searchDirs = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/opt/local/bin"
    ]

    init() {
        downloadDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Music/SoundGrab")
        try? FileManager.default.createDirectory(at: downloadDir, withIntermediateDirectories: true)
        Task { @MainActor in
            await checkDependencies()
        }
    }

    // MARK: - Dependencies

    @MainActor
    func checkDependencies() async {
        isCheckingDeps = true
        ytdlpPath = Self.findExecutable("yt-dlp")
        ffmpegPath = Self.findExecutable("ffmpeg")
        dependenciesReady = ytdlpPath != nil && ffmpegPath != nil
        isCheckingDeps = false
    }

    @MainActor
    func installDependencies() async {
        isInstalling = true
        installOutput = "Looking for Homebrew..."

        guard let brew = Self.findExecutable("brew") else {
            installOutput = "Homebrew not found — install from https://brew.sh first"
            isInstalling = false
            return
        }

        do {
            if ytdlpPath == nil {
                installOutput = "Installing yt-dlp..."
                _ = try await Self.exec(brew, ["install", "yt-dlp"])
            }
            if ffmpegPath == nil {
                installOutput = "Installing ffmpeg..."
                _ = try await Self.exec(brew, ["install", "ffmpeg"])
            }
            await checkDependencies()
            if dependenciesReady {
                installOutput = ""
            } else {
                installOutput = "Install completed but binaries not found in PATH"
            }
        } catch {
            installOutput = "Install failed: \(error.localizedDescription)"
        }
        isInstalling = false
    }

    @MainActor
    func updateYTDLP() async {
        guard let brew = Self.findExecutable("brew") else { return }
        installOutput = "Updating yt-dlp..."
        isInstalling = true
        do {
            _ = try await Self.exec(brew, ["upgrade", "yt-dlp"])
            installOutput = ""
        } catch {
            installOutput = "Update failed: \(error.localizedDescription)"
        }
        isInstalling = false
    }

    // MARK: - URL submission

    @MainActor
    func submitURL(_ rawURL: String, platform: Platform, importToMusic: Bool) async {
        let url = rawURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty, let ytdlp = ytdlpPath else { return }

        let isPlaylist = await Self.checkIfPlaylist(url: url, ytdlpPath: ytdlp)

        if isPlaylist {
            await downloadPlaylist(url: url, platform: platform, importToMusic: importToMusic)
        } else {
            let item = DownloadItem(title: extractFallbackTitle(url), url: url, platform: platform)
            items.insert(item, at: 0)
            await downloadTrack(item: item, importToMusic: importToMusic)
        }
    }

    // MARK: - Playlist handling

    @MainActor
    private func downloadPlaylist(url: String, platform: Platform, importToMusic: Bool) async {
        guard let ytdlp = ytdlpPath else { return }

        let placeholder = DownloadItem(title: "Loading playlist...", url: url, platform: platform)
        placeholder.status = .fetchingInfo
        items.insert(placeholder, at: 0)

        do {
            let output = try await Self.exec(ytdlp, [
                "--flat-playlist", "--dump-json", "--no-warnings", url
            ])

            let lines = output.components(separatedBy: .newlines).filter { !$0.isEmpty }
            var trackInfos: [(url: String, title: String)] = []

            for line in lines {
                guard let data = line.data(using: .utf8),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    continue
                }
                let title = json["title"] as? String ?? "Unknown"
                let trackURL: String
                if let directURL = json["url"] as? String,
                   directURL.hasPrefix("http") {
                    trackURL = directURL
                } else if let id = json["id"] as? String {
                    trackURL = platform == .youtube
                        ? "https://www.youtube.com/watch?v=\(id)"
                        : url
                } else {
                    continue
                }
                trackInfos.append((trackURL, title))
            }

            if let idx = items.firstIndex(where: { $0.id == placeholder.id }) {
                items.remove(at: idx)
            }

            guard !trackInfos.isEmpty else {
                placeholder.status = .failed("No tracks found in playlist")
                items.insert(placeholder, at: 0)
                return
            }

            var playlistItems: [DownloadItem] = []
            for (i, info) in trackInfos.enumerated() {
                let item = DownloadItem(title: info.title, url: info.url, platform: platform)
                playlistItems.append(item)
                items.insert(item, at: i)
            }

            for item in playlistItems {
                await downloadTrack(item: item, importToMusic: importToMusic)
            }

        } catch {
            placeholder.title = "Playlist"
            placeholder.status = .failed("Failed to load playlist: \(error.localizedDescription)")
        }
    }

    // MARK: - Single track download

    @MainActor
    private func downloadTrack(item: DownloadItem, importToMusic: Bool) async {
        guard let ytdlp = ytdlpPath else {
            item.status = .failed("yt-dlp not found")
            return
        }

        // Fetch metadata
        item.status = .fetchingInfo
        var expectedDuration: Double?

        do {
            let output = try await Self.exec(ytdlp, [
                "--dump-json", "--no-playlist", "--no-warnings", item.url
            ])
            if let data = output.data(using: .utf8),
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let title = json["title"] as? String { item.title = title }
                expectedDuration = json["duration"] as? Double
            }
        } catch {
            // Metadata fetch failed — continue with download anyway
        }

        // Prepare temp directory (isolates this download's files)
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("SoundGrab-\(item.id.uuidString)")
        try? FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let outputTemplate = tempDir.appendingPathComponent("%(title)s [%(id)s].%(ext)s").path

        // Build yt-dlp arguments
        var args: [String] = []
        if let ffmpeg = ffmpegPath {
            args += ["--ffmpeg-location", URL(fileURLWithPath: ffmpeg).deletingLastPathComponent().path]
        }
        args += [
            "--no-playlist",
            "-x", "--audio-format", "mp3",
            "--audio-quality", "320K",
            "--embed-thumbnail",
            "--embed-metadata",
            "--newline",
            "--retries", "3",
            "--fragment-retries", "3",
            "--no-overwrites",
            "-o", outputTemplate,
            item.url
        ]

        // Download with progress
        item.status = .downloading
        item.progress = 0

        do {
            try await Self.runDownload(ytdlpPath: ytdlp, arguments: args) { pct in
                Task { @MainActor in item.progress = pct }
            } onConverting: {
                Task { @MainActor in item.status = .converting }
            }
        } catch {
            item.status = .failed(error.localizedDescription)
            return
        }

        // Find the MP3 file
        guard let contents = try? FileManager.default.contentsOfDirectory(
            at: tempDir, includingPropertiesForKeys: [.fileSizeKey]
        ) else {
            item.status = .failed(DownloadError.fileNotFound.localizedDescription)
            return
        }

        let mp3Files = contents.filter { $0.pathExtension.lowercased() == "mp3" }
        guard let mp3File = mp3Files.first else {
            item.status = .failed(DownloadError.fileNotFound.localizedDescription)
            return
        }

        // Verify file integrity
        let attrs = try? FileManager.default.attributesOfItem(atPath: mp3File.path)
        let fileSize = (attrs?[.size] as? Int) ?? 0
        if fileSize < 10_000 {
            item.status = .failed(DownloadError.fileTooSmall(fileSize).localizedDescription)
            return
        }

        // Verify duration matches metadata (catches wrong-song downloads)
        if let expected = expectedDuration, expected > 10, let ffprobe = Self.findFFProbe(ffmpegPath: ffmpegPath) {
            let actualDuration = await Self.probeAudioDuration(path: mp3File, ffprobePath: ffprobe)
            if let actual = actualDuration {
                let tolerance = max(expected * 0.20, 5.0)
                if abs(actual - expected) > tolerance {
                    item.status = .failed("Duration mismatch (expected \(Int(expected))s, got \(Int(actual))s) — wrong song downloaded")
                    return
                }
            }
        }

        // Move to final directory
        let finalPath = Self.uniquePath(for: mp3File.lastPathComponent, in: downloadDir)
        do {
            try FileManager.default.moveItem(at: mp3File, to: finalPath)
        } catch {
            item.status = .failed("Failed to save: \(error.localizedDescription)")
            return
        }
        item.filePath = finalPath

        // Import to Apple Music
        if importToMusic {
            item.status = .importing
            let ok = await MusicImporter.importFile(finalPath)
            if !ok {
                item.status = .done
                return
            }
        }

        item.status = .done
        item.progress = 1.0
    }

    // MARK: - Helpers

    @MainActor
    func clearCompleted(platform: Platform) {
        items.removeAll { $0.platform == platform && $0.status == .done }
    }

    private func extractFallbackTitle(_ url: String) -> String {
        let cleaned = url
            .replacingOccurrences(of: "https://", with: "")
            .replacingOccurrences(of: "http://", with: "")
            .replacingOccurrences(of: "www.", with: "")
        let parts = cleaned.split(separator: "/")
        if let last = parts.last, !last.isEmpty {
            return String(last).replacingOccurrences(of: "-", with: " ")
        }
        return url
    }

    private static func uniquePath(for filename: String, in dir: URL) -> URL {
        var path = dir.appendingPathComponent(filename)
        if !FileManager.default.fileExists(atPath: path.path) { return path }

        let name = path.deletingPathExtension().lastPathComponent
        let ext = path.pathExtension
        var counter = 2
        while FileManager.default.fileExists(atPath: path.path) {
            path = dir.appendingPathComponent("\(name) (\(counter)).\(ext)")
            counter += 1
        }
        return path
    }

    // MARK: - Process execution

    private static func findExecutable(_ name: String) -> String? {
        for dir in searchDirs {
            let path = "\(dir)/\(name)"
            if FileManager.default.isExecutableFile(atPath: path) { return path }
        }
        let home = NSHomeDirectory()
        let localBin = "\(home)/.local/bin/\(name)"
        if FileManager.default.isExecutableFile(atPath: localBin) { return localBin }

        if let output = try? shellWhich(name), !output.isEmpty { return output }
        return nil
    }

    private static func shellWhich(_ name: String) throws -> String? {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        proc.arguments = [name]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        try proc.run()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func findFFProbe(ffmpegPath: String?) -> String? {
        guard let ffmpeg = ffmpegPath else { return nil }
        let dir = URL(fileURLWithPath: ffmpeg).deletingLastPathComponent()
        let probe = dir.appendingPathComponent("ffprobe").path
        return FileManager.default.isExecutableFile(atPath: probe) ? probe : nil
    }

    nonisolated static func exec(_ path: String, _ args: [String]) async throws -> String {
        try await withCheckedThrowingContinuation { cont in
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: path)
            proc.arguments = args
            let outPipe = Pipe()
            let errPipe = Pipe()
            proc.standardOutput = outPipe
            proc.standardError = errPipe
            proc.terminationHandler = { p in
                let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
                let output = String(data: outData, encoding: .utf8) ?? ""
                if p.terminationStatus == 0 {
                    cont.resume(returning: output)
                } else {
                    let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
                    let errOutput = String(data: errData, encoding: .utf8) ?? ""
                    let msg = errOutput.isEmpty ? output : errOutput
                    cont.resume(throwing: DownloadError.processError(msg))
                }
            }
            do {
                try proc.run()
            } catch {
                cont.resume(throwing: error)
            }
        }
    }

    nonisolated static func checkIfPlaylist(url: String, ytdlpPath: String) async -> Bool {
        do {
            let output = try await exec(ytdlpPath, [
                "--dump-single-json", "--flat-playlist", "--no-warnings", "--no-download", url
            ])
            if let data = output.data(using: .utf8),
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let type = json["_type"] as? String {
                return type == "playlist"
            }
        } catch {}
        return false
    }

    nonisolated static func runDownload(
        ytdlpPath: String,
        arguments: [String],
        onProgress: @Sendable @escaping (Double) -> Void,
        onConverting: @Sendable @escaping () -> Void
    ) async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: ytdlpPath)
            proc.arguments = arguments

            let outPipe = Pipe()
            let errPipe = Pipe()
            proc.standardOutput = outPipe
            proc.standardError = errPipe

            let errBuffer = LockedBuffer()

            outPipe.fileHandleForReading.readabilityHandler = { handle in
                let data = handle.availableData
                guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
                for line in text.components(separatedBy: .newlines) where !line.isEmpty {
                    if line.contains("[download]") {
                        for part in line.split(separator: " ") {
                            if part.hasSuffix("%"), let pct = Double(part.dropLast()) {
                                onProgress(min(pct / 100.0, 1.0))
                                break
                            }
                        }
                    }
                    if line.contains("[ExtractAudio]") || line.contains("[ffmpeg]") {
                        onConverting()
                    }
                }
            }

            errPipe.fileHandleForReading.readabilityHandler = { handle in
                let data = handle.availableData
                guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
                errBuffer.append(text)
            }

            proc.terminationHandler = { _ in
                outPipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil
                if proc.terminationStatus == 0 {
                    cont.resume()
                } else {
                    cont.resume(throwing: DownloadError.processError(errBuffer.value))
                }
            }

            do {
                try proc.run()
            } catch {
                cont.resume(throwing: error)
            }
        }
    }

    nonisolated static func probeAudioDuration(path: URL, ffprobePath: String) async -> Double? {
        guard let output = try? await exec(ffprobePath, [
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            path.path
        ]) else { return nil }
        return Double(output.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}

final class LockedBuffer: @unchecked Sendable {
    private var buffer = ""
    private let lock = NSLock()

    func append(_ text: String) {
        lock.lock()
        buffer += text
        lock.unlock()
    }

    var value: String {
        lock.lock()
        defer { lock.unlock() }
        return buffer
    }
}
