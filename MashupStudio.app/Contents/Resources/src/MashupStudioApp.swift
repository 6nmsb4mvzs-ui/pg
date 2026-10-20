import SwiftUI
import UniformTypeIdentifiers

@main
struct MashupStudioApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        WindowGroup {
            ContentView()
                .frame(minWidth: 900, minHeight: 400)
                .preferredColorScheme(.dark)
        }
        .windowStyle(.titleBar)
        .defaultSize(width: 1200, height: 500)
        .commands {
            CommandGroup(replacing: .undoRedo) {
                Button("Undo") {
                    NSApp.sendAction(Selector(("undo:")), to: nil, from: nil)
                }
                .keyboardShortcut("z", modifiers: .command)

                Button("Redo") {
                    NSApp.sendAction(Selector(("redo:")), to: nil, from: nil)
                }
                .keyboardShortcut("z", modifiers: [.command, .shift])
            }
        }
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate()
        loadAppIcon()
    }

    private func loadAppIcon() {
        if let appDir = ProcessInfo.processInfo.environment["MASHUP_APP_DIR"] {
            let icnsPath = URL(fileURLWithPath: appDir)
                .appendingPathComponent("Contents/Resources/AppIcon.icns")
            if let icon = NSImage(contentsOf: icnsPath) {
                NSApp.applicationIconImage = icon
            }
        }
    }
}
