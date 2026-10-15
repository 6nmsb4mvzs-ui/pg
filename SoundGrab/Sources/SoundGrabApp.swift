import SwiftUI

@main
struct SoundGrabApp: App {
    @State private var manager = DownloadManager()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(manager)
                .frame(minWidth: 640, minHeight: 480)
        }
        .defaultSize(width: 740, height: 580)
    }
}
