import SwiftUI
import UniformTypeIdentifiers

struct WelcomeView: View {
    @ObservedObject var project: MashupProject
    @ObservedObject var engine: AudioEngineManager
    var onStart: () -> Void

    @State private var recentProjects: [RecentProject] = []

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 24) {
                appIcon

                Text("MashupStudio")
                    .font(.system(size: 28, weight: .bold, design: .monospaced))
                    .foregroundColor(.white)

                Text("two-track audio mashup editor")
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundColor(.white.opacity(0.35))

                HStack(spacing: 16) {
                    actionButton(
                        icon: "plus.square",
                        label: "New Project",
                        color: .cyan
                    ) {
                        onStart()
                    }

                    actionButton(
                        icon: "folder",
                        label: "Open Project",
                        color: .orange
                    ) {
                        openExisting()
                    }
                }
                .padding(.top, 8)

                if !recentProjects.isEmpty {
                    recentsList
                }
            }
            .frame(maxWidth: 480)

            Spacer()

            Text("v\(UpdateManager.shared.currentVersion)")
                .font(.system(size: 10, design: .monospaced))
                .foregroundColor(.white.opacity(0.15))
                .padding(.bottom, 12)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: NSColor(white: 0.08, alpha: 1)))
        .onAppear {
            recentProjects = RecentProjectStore.load()
        }
    }

    private var appIcon: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 20)
                .fill(
                    LinearGradient(
                        colors: [
                            Color(nsColor: NSColor(white: 0.15, alpha: 1)),
                            Color(nsColor: NSColor(white: 0.1, alpha: 1))
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: 80, height: 80)

            waveformIcon
        }
    }

    private var waveformIcon: some View {
        Canvas { context, size in
            let w = size.width
            let h = size.height
            let mid = h / 2

            func wave(_ phase: Double, amplitude: Double) -> Path {
                var path = Path()
                let steps = 40
                for i in 0...steps {
                    let x = w * Double(i) / Double(steps)
                    let t = Double(i) / Double(steps) * .pi * 3 + phase
                    let env = sin(Double(i) / Double(steps) * .pi)
                    let y = mid + sin(t) * amplitude * env
                    if i == 0 { path.move(to: CGPoint(x: x, y: y)) }
                    else { path.addLine(to: CGPoint(x: x, y: y)) }
                }
                return path
            }

            context.opacity = 0.7
            context.stroke(wave(0, amplitude: 14), with: .color(.cyan), lineWidth: 2.5)
            context.stroke(wave(1.2, amplitude: 12), with: .color(.orange), lineWidth: 2.5)
        }
        .frame(width: 50, height: 50)
    }

    private func actionButton(
        icon: String,
        label: String,
        color: Color,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 24))
                    .foregroundColor(color)
                Text(label)
                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                    .foregroundColor(.white.opacity(0.7))
            }
            .frame(width: 140, height: 80)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(color.opacity(0.08))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .strokeBorder(color.opacity(0.15), lineWidth: 1)
                    )
            )
        }
        .buttonStyle(.plain)
    }

    private var recentsList: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recent Projects")
                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                .foregroundColor(.white.opacity(0.3))
                .textCase(.uppercase)
                .padding(.leading, 4)

            VStack(spacing: 2) {
                ForEach(recentProjects.prefix(5)) { recent in
                    recentRow(recent)
                }
            }
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color(nsColor: NSColor(white: 0.11, alpha: 1)))
            )
        }
        .padding(.top, 8)
    }

    private func recentRow(_ recent: RecentProject) -> some View {
        Button(action: { openRecent(recent) }) {
            HStack(spacing: 10) {
                Image(systemName: "doc.badge.clock")
                    .font(.system(size: 12))
                    .foregroundColor(.purple.opacity(0.6))
                    .frame(width: 20)

                VStack(alignment: .leading, spacing: 2) {
                    Text(recent.name)
                        .font(.system(size: 12, weight: .medium, design: .monospaced))
                        .foregroundColor(.white.opacity(0.7))
                        .lineLimit(1)
                    Text(recent.path)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(.white.opacity(0.2))
                        .lineLimit(1)
                        .truncationMode(.middle)
                }

                Spacer()

                Text(recent.dateString)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.white.opacity(0.2))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func openExisting() {
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
                RecentProjectStore.add(url: url)
                onStart()
            }
        }
    }

    private func openRecent(_ recent: RecentProject) {
        let url = URL(fileURLWithPath: recent.path)
        guard FileManager.default.fileExists(atPath: recent.path) else {
            RecentProjectStore.remove(path: recent.path)
            recentProjects = RecentProjectStore.load()
            return
        }
        Task { @MainActor in
            try? await ProjectFile.load(from: url, into: project, engine: engine)
            engine.applySettings()
            RecentProjectStore.touch(path: recent.path)
            onStart()
        }
    }
}

struct RecentProject: Identifiable, Codable {
    var id: String { path }
    let name: String
    let path: String
    let date: Date

    var dateString: String {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f.localizedString(for: date, relativeTo: Date())
    }
}

enum RecentProjectStore {
    private static let key = "MashupStudio.recentProjects"
    private static let maxCount = 10

    static func load() -> [RecentProject] {
        guard let data = UserDefaults.standard.data(forKey: key),
              let list = try? JSONDecoder().decode([RecentProject].self, from: data) else {
            return []
        }
        return list.filter { FileManager.default.fileExists(atPath: $0.path) }
    }

    static func add(url: URL) {
        var list = load()
        list.removeAll { $0.path == url.path }
        let entry = RecentProject(
            name: url.deletingPathExtension().lastPathComponent,
            path: url.path,
            date: Date()
        )
        list.insert(entry, at: 0)
        if list.count > maxCount { list = Array(list.prefix(maxCount)) }
        save(list)
    }

    static func touch(path: String) {
        var list = load()
        if let idx = list.firstIndex(where: { $0.path == path }) {
            var entry = list.remove(at: idx)
            entry = RecentProject(name: entry.name, path: entry.path, date: Date())
            list.insert(entry, at: 0)
            save(list)
        }
    }

    static func remove(path: String) {
        var list = load()
        list.removeAll { $0.path == path }
        save(list)
    }

    private static func save(_ list: [RecentProject]) {
        if let data = try? JSONEncoder().encode(list) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }
}
