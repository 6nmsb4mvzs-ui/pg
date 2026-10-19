import SwiftUI

struct ContentView: View {
    @StateObject private var project = MashupProject()
    @StateObject private var engine = AudioEngineManager()

    var body: some View {
        VStack(spacing: 0) {
            TransportBar(project: project, engine: engine)

            Divider().background(Color.white.opacity(0.1))

            HSplitView {
                // Controls sidebar
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
                    }
                    .padding(10)
                }
                .frame(minWidth: 220, idealWidth: 260, maxWidth: 320)
                .background(Color(nsColor: NSColor(white: 0.09, alpha: 1)))

                // Timeline
                TimelineView(project: project, engine: engine)
                    .frame(minWidth: 400)
            }
        }
        .background(Color(nsColor: NSColor(white: 0.1, alpha: 1)))
        .onAppear {
            engine.project = project
        }
        .onDisappear {
            engine.stop()
        }
    }
}
