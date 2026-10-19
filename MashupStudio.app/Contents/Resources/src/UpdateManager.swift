import Foundation

class UpdateManager: ObservableObject {
    static let shared = UpdateManager()

    @Published var currentVersion: String = "1.0.0"
    @Published var remoteVersion: String?
    @Published var updateAvailable: Bool = false
    @Published var isChecking: Bool = false
    @Published var isDownloading: Bool = false
    @Published var downloadProgress: Double = 0
    @Published var statusMessage: String = ""
    @Published var lastChecked: Date?

    private let repoOwner = "6nmsb4mvzs-ui"
    private let repoName = "mix"
    private let branch = "main"
    private var autoCheckTimer: Timer?

    private lazy var noCacheSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        config.urlCache = nil
        return URLSession(configuration: config)
    }()

    var sourceDir: String? {
        ProcessInfo.processInfo.environment["MASHUP_SOURCE_DIR"]
    }

    init() {
        loadCurrentVersion()
    }

    private func loadCurrentVersion() {
        guard let dir = sourceDir else { return }
        let versionFile = URL(fileURLWithPath: dir).appendingPathComponent("version.txt")
        if let contents = try? String(contentsOf: versionFile, encoding: .utf8) {
            currentVersion = contents.trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }

    @MainActor
    func checkOnLaunch() {
        Task {
            await checkForUpdates(silent: true)
        }
        autoCheckTimer?.invalidate()
        autoCheckTimer = Timer.scheduledTimer(withTimeInterval: 1800, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                await self?.checkForUpdates(silent: true)
            }
        }
    }

    @MainActor
    func checkForUpdates(silent: Bool = false) async {
        guard !isChecking else { return }
        isChecking = true
        if !silent { statusMessage = "Checking for updates..." }

        let urlString = "https://raw.githubusercontent.com/\(repoOwner)/\(repoName)/\(branch)/version.txt"
        guard let url = URL(string: urlString) else {
            isChecking = false
            if !silent { statusMessage = "Invalid update URL" }
            return
        }

        do {
            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
            let (data, response) = try await noCacheSession.data(for: request)
            if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                isChecking = false
                if !silent { statusMessage = "Server returned \(http.statusCode)" }
                return
            }
            let remote = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

            guard !remote.isEmpty, remote.contains(".") else {
                isChecking = false
                if !silent { statusMessage = "Invalid version data from server" }
                return
            }

            remoteVersion = remote
            updateAvailable = Self.isNewer(remote: remote, local: currentVersion)
            isChecking = false
            lastChecked = Date()
            statusMessage = updateAvailable
                ? "Update available: v\(remote)"
                : "You're up to date (v\(currentVersion))"
        } catch {
            isChecking = false
            if !silent { statusMessage = "Connection failed: \(error.localizedDescription)" }
        }
    }

    @MainActor
    func downloadAndInstall() async {
        guard let sourceDir = sourceDir else {
            statusMessage = "Cannot locate source directory"
            return
        }

        isDownloading = true
        downloadProgress = 0
        statusMessage = "Downloading update..."

        let zipURL = "https://github.com/\(repoOwner)/\(repoName)/archive/refs/heads/\(branch).zip"
        guard let url = URL(string: zipURL) else {
            isDownloading = false
            statusMessage = "Invalid download URL"
            return
        }

        do {
            let (tempURL, _) = try await noCacheSession.download(from: url)

            downloadProgress = 0.5
            statusMessage = "Installing update..."

            let fm = FileManager.default
            let tempDir = fm.temporaryDirectory
                .appendingPathComponent("mashup-update-\(UUID().uuidString)")
            try fm.createDirectory(at: tempDir, withIntermediateDirectories: true)

            let unzip = Process()
            unzip.executableURL = URL(fileURLWithPath: "/usr/bin/unzip")
            unzip.arguments = ["-o", tempURL.path, "-d", tempDir.path]
            unzip.standardOutput = FileHandle.nullDevice
            unzip.standardError = FileHandle.nullDevice
            try unzip.run()
            unzip.waitUntilExit()

            let contents = try fm.contentsOfDirectory(at: tempDir, includingPropertiesForKeys: nil)
            guard let extractedDir = contents.first(where: { $0.hasDirectoryPath }) else {
                throw UpdateError.badArchive
            }

            let remoteSrc = extractedDir
                .appendingPathComponent("MashupStudio.app/Contents/Resources/src")
            let sourceDirURL = URL(fileURLWithPath: sourceDir)

            guard fm.fileExists(atPath: remoteSrc.path) else {
                throw UpdateError.badArchive
            }

            let items = try fm.contentsOfDirectory(
                at: remoteSrc, includingPropertiesForKeys: nil
            )
            for item in items {
                let dest = sourceDirURL.appendingPathComponent(item.lastPathComponent)
                try? fm.removeItem(at: dest)
                try fm.copyItem(at: item, to: dest)
            }

            let support = fm.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support/MashupStudio/build")
            try? fm.removeItem(at: support.appendingPathComponent(".source-stamp"))

            try? fm.removeItem(at: tempDir)
            try? fm.removeItem(at: tempURL)

            downloadProgress = 1.0
            isDownloading = false
            loadCurrentVersion()
            updateAvailable = false
            statusMessage = "Update installed — restart the app to apply."
        } catch {
            isDownloading = false
            statusMessage = "Update failed: \(error.localizedDescription)"
        }
    }

    static func isNewer(remote: String, local: String) -> Bool {
        let r = remote.split(separator: ".").compactMap { Int($0) }
        let l = local.split(separator: ".").compactMap { Int($0) }
        for i in 0..<max(r.count, l.count) {
            let rv = i < r.count ? r[i] : 0
            let lv = i < l.count ? l[i] : 0
            if rv > lv { return true }
            if rv < lv { return false }
        }
        return false
    }
}

enum UpdateError: LocalizedError {
    case badArchive
    var errorDescription: String? {
        switch self {
        case .badArchive: return "Downloaded archive has unexpected structure"
        }
    }
}
