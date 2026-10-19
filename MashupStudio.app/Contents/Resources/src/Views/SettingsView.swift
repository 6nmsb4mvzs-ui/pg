import SwiftUI

struct SettingsView: View {
    @ObservedObject private var updateManager = UpdateManager.shared
    @Environment(\.dismiss) var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Settings")
                    .font(.system(size: 16, weight: .semibold, design: .monospaced))
                    .foregroundColor(.white)
                Spacer()
                Button(action: { dismiss() }) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 16))
                        .foregroundColor(.white.opacity(0.3))
                }
                .buttonStyle(.plain)
            }
            .padding(16)

            Divider().background(Color.white.opacity(0.1))

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    sectionHeader("Updates")

                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Current version")
                                .font(.system(size: 12, design: .monospaced))
                                .foregroundColor(.white.opacity(0.5))
                            Spacer()
                            Text("v\(updateManager.currentVersion)")
                                .font(.system(size: 12, weight: .medium, design: .monospaced))
                                .foregroundColor(.white.opacity(0.8))
                        }

                        if let remote = updateManager.remoteVersion,
                           updateManager.updateAvailable {
                            HStack {
                                Text("Available")
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundColor(.white.opacity(0.5))
                                Spacer()
                                Text("v\(remote)")
                                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                                    .foregroundColor(.green)
                            }
                        }

                        if !updateManager.statusMessage.isEmpty {
                            Text(updateManager.statusMessage)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundColor(.white.opacity(0.4))
                        }

                        if let lastChecked = updateManager.lastChecked {
                            Text("Last checked: \(lastChecked.formatted(date: .abbreviated, time: .shortened))")
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundColor(.white.opacity(0.25))
                        }

                        HStack(spacing: 8) {
                            Button(action: {
                                Task { await updateManager.checkForUpdates() }
                            }) {
                                HStack(spacing: 4) {
                                    if updateManager.isChecking {
                                        ProgressView()
                                            .scaleEffect(0.5)
                                            .frame(width: 12, height: 12)
                                    }
                                    Text("Check for Updates")
                                        .font(.system(size: 12, weight: .medium))
                                }
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                            .disabled(updateManager.isChecking || updateManager.isDownloading)

                            if updateManager.updateAvailable {
                                Button(action: {
                                    Task { await updateManager.downloadAndInstall() }
                                }) {
                                    HStack(spacing: 4) {
                                        if updateManager.isDownloading {
                                            ProgressView()
                                                .scaleEffect(0.5)
                                                .frame(width: 12, height: 12)
                                        }
                                        Text("Download & Install")
                                            .font(.system(size: 12, weight: .medium))
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                                .controlSize(.small)
                                .disabled(updateManager.isDownloading)
                            }
                        }

                        if updateManager.isDownloading {
                            ProgressView(value: updateManager.downloadProgress)
                                .progressViewStyle(.linear)
                        }
                    }
                    .padding(12)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color(nsColor: NSColor(white: 0.13, alpha: 1)))
                    )

                    sectionHeader("About")

                    VStack(alignment: .leading, spacing: 8) {
                        Text("MashupStudio")
                            .font(.system(size: 13, weight: .semibold, design: .monospaced))
                            .foregroundColor(.white)
                        Text("Two-track audio mashup tool with timeline editing, EQ, and waveform display.")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(.white.opacity(0.4))
                            .fixedSize(horizontal: false, vertical: true)
                        Text("Updates from: github.com/6nmsb4mvzs-ui/mix")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundColor(.white.opacity(0.25))
                        if let src = updateManager.sourceDir {
                            Text("Source: \(src)")
                                .font(.system(size: 9, design: .monospaced))
                                .foregroundColor(.white.opacity(0.15))
                                .lineLimit(1)
                                .truncationMode(.middle)
                        } else {
                            Text("Source path not found — updates may not install")
                                .font(.system(size: 9, design: .monospaced))
                                .foregroundColor(.red.opacity(0.5))
                        }
                    }
                    .padding(12)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color(nsColor: NSColor(white: 0.13, alpha: 1)))
                    )
                }
                .padding(16)
            }
        }
        .frame(width: 420, height: 400)
        .background(Color(nsColor: NSColor(white: 0.1, alpha: 1)))
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 11, weight: .semibold, design: .monospaced))
            .foregroundColor(.white.opacity(0.3))
            .textCase(.uppercase)
    }
}
