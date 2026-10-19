import SwiftUI
import UniformTypeIdentifiers

struct TimelineView: View {
    @ObservedObject var project: MashupProject
    @ObservedObject var engine: AudioEngineManager
    let trackHeight: CGFloat = 100

    var body: some View {
        let totalWidth = project.timeToX(project.totalDuration + 10)

        ScrollView(.horizontal, showsIndicators: true) {
            ZStack(alignment: .topLeading) {
                VStack(spacing: 0) {
                    timeRuler(width: totalWidth)
                        .frame(height: 28)

                    Divider().background(Color.white.opacity(0.1))

                    ZStack(alignment: .topLeading) {
                        gridLines(width: totalWidth, height: trackHeight)
                        WaveformView(
                            track: project.trackA,
                            project: project,
                            color: .cyan,
                            height: trackHeight
                        )
                    }
                    .frame(height: trackHeight)
                    .onDrop(of: [.fileURL], isTargeted: nil) { providers in
                        handleDrop(providers: providers, track: project.trackA)
                    }

                    Divider().background(Color.white.opacity(0.1))

                    ZStack(alignment: .topLeading) {
                        gridLines(width: totalWidth, height: trackHeight)
                        WaveformView(
                            track: project.trackB,
                            project: project,
                            color: .orange,
                            height: trackHeight
                        )
                    }
                    .frame(height: trackHeight)
                    .onDrop(of: [.fileURL], isTargeted: nil) { providers in
                        handleDrop(providers: providers, track: project.trackB)
                    }

                    Spacer()
                }

                if project.loopEnabled {
                    let loopX = project.timeToX(project.loopStart)
                    let loopW = project.timeToX(project.loopEnd) - loopX
                    Rectangle()
                        .fill(Color.purple.opacity(0.1))
                        .frame(width: loopW)
                        .offset(x: loopX, y: 28)
                        .allowsHitTesting(false)
                }

                let playX = project.timeToX(project.currentTime)
                Rectangle()
                    .fill(Color.white)
                    .frame(width: 1)
                    .offset(x: playX)
                    .allowsHitTesting(false)

                Path { p in
                    p.move(to: CGPoint(x: playX - 5, y: 0))
                    p.addLine(to: CGPoint(x: playX + 5, y: 0))
                    p.addLine(to: CGPoint(x: playX, y: 8))
                    p.closeSubpath()
                }
                .fill(Color.white)
                .allowsHitTesting(false)
            }
            .frame(width: totalWidth, alignment: .topLeading)
            .contentShape(Rectangle())
            .onTapGesture { location in
                if !engine.isPlaying {
                    project.currentTime = project.xToTime(location.x)
                }
            }
        }
        .background(Color(nsColor: NSColor(white: 0.1, alpha: 1)))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func timeRuler(width: CGFloat) -> some View {
        Canvas { ctx, size in
            let pps = project.pixelsPerSecond

            var interval: Double = 1
            if pps < 5 { interval = 10 }
            else if pps < 15 { interval = 5 }
            else if pps < 40 { interval = 2 }

            let majorInterval = interval * 5
            var t: Double = 0

            while t <= project.totalDuration + 10 {
                let x = CGFloat(t * pps)
                let isMajor = t.truncatingRemainder(dividingBy: majorInterval) < 0.001

                let lineHeight: CGFloat = isMajor ? 12 : 6
                let color: Color = isMajor ? .white.opacity(0.5) : .white.opacity(0.2)

                ctx.stroke(
                    Path { p in
                        p.move(to: CGPoint(x: x, y: size.height))
                        p.addLine(to: CGPoint(x: x, y: size.height - lineHeight))
                    },
                    with: .color(color),
                    lineWidth: 1
                )

                if isMajor {
                    let minutes = Int(t) / 60
                    let seconds = Int(t) % 60
                    let label = String(format: "%d:%02d", minutes, seconds)
                    ctx.draw(
                        Text(label)
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundColor(.white.opacity(0.5)),
                        at: CGPoint(x: x, y: 8)
                    )
                }

                t += interval
            }
        }
        .background(Color(nsColor: NSColor(white: 0.08, alpha: 1)))
    }

    private func gridLines(width: CGFloat, height: CGFloat) -> some View {
        Canvas { ctx, size in
            let pps = project.pixelsPerSecond
            var interval: Double = 1
            if pps < 5 { interval = 10 }
            else if pps < 15 { interval = 5 }
            else if pps < 40 { interval = 2 }

            var t: Double = 0
            while t <= project.totalDuration + 10 {
                let x = CGFloat(t * pps)
                ctx.stroke(
                    Path { p in
                        p.move(to: CGPoint(x: x, y: 0))
                        p.addLine(to: CGPoint(x: x, y: size.height))
                    },
                    with: .color(.white.opacity(0.04)),
                    lineWidth: 0.5
                )
                t += interval
            }
        }
    }

    private func handleDrop(providers: [NSItemProvider], track: AudioTrack) -> Bool {
        guard let provider = providers.first else { return false }
        provider.loadItem(forTypeIdentifier: "public.file-url", options: nil) { item, _ in
            guard let data = item as? Data,
                  let url = URL(dataRepresentation: data, relativeTo: nil),
                  isAudioFile(url) else { return }
            Task { @MainActor in
                try? await track.loadFile(url)
                if track === project.trackA {
                    try? engine.loadTrackA(url: url)
                } else {
                    try? engine.loadTrackB(url: url)
                }
            }
        }
        return true
    }

    private func isAudioFile(_ url: URL) -> Bool {
        let exts = ["mp3", "wav", "aiff", "aif", "m4a", "flac", "aac", "ogg", "caf"]
        return exts.contains(url.pathExtension.lowercased())
    }
}
