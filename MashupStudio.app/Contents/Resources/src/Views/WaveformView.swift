import SwiftUI

struct WaveformView: View {
    @ObservedObject var track: AudioTrack
    @ObservedObject var project: MashupProject
    @Environment(\.undoManager) var undoManager

    let color: Color
    let height: CGFloat

    @State private var isDragging = false
    @State private var dragStartOffset: TimeInterval = 0
    @State private var isDraggingTrimStart = false
    @State private var isDraggingTrimEnd = false
    @State private var trimDragOrigin: TimeInterval = 0

    private let trimHandleWidth: CGFloat = 10

    var body: some View {
        GeometryReader { _ in
            let pps = project.pixelsPerSecond
            let trackX = project.timeToX(track.offset)
            let trackWidth = CGFloat(track.duration * pps)
            let trimStartX = CGFloat(track.trimStart * pps)
            let trimEndX = CGFloat(track.trimEnd * pps)
            let visibleWidth = trackWidth - trimStartX - trimEndX

            if track.isLoaded && visibleWidth > 0 {
                ZStack(alignment: .leading) {
                    if trimStartX > 0 {
                        Rectangle()
                            .fill(color.opacity(0.08))
                            .frame(width: trimStartX, height: height)
                            .offset(x: trackX)
                    }
                    if trimEndX > 0 {
                        Rectangle()
                            .fill(color.opacity(0.08))
                            .frame(width: trimEndX, height: height)
                            .offset(x: trackX + trackWidth - trimEndX)
                    }

                    waveformPath(width: visibleWidth, height: height)
                        .fill(
                            LinearGradient(
                                colors: [color.opacity(0.9), color.opacity(0.5)],
                                startPoint: .top,
                                endPoint: .bottom
                            )
                        )
                        .frame(width: visibleWidth, height: height)
                        .background(color.opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                        .overlay(
                            RoundedRectangle(cornerRadius: 4)
                                .stroke(isDragging ? Color.white : color.opacity(0.4), lineWidth: 1)
                        )
                        .offset(x: trackX + trimStartX)
                        .gesture(dragGesture())

                    trimHandle(isStart: true)
                        .offset(x: trackX + trimStartX - trimHandleWidth / 2)
                        .gesture(trimStartGesture())

                    trimHandle(isStart: false)
                        .offset(x: trackX + trimStartX + visibleWidth - trimHandleWidth / 2)
                        .gesture(trimEndGesture())

                    Text(track.label)
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .foregroundColor(color)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.black.opacity(0.6))
                        .cornerRadius(3)
                        .offset(x: trackX + trimStartX + 6, y: -height / 2 + 14)

                    Text(track.name)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.white.opacity(0.6))
                        .lineLimit(1)
                        .offset(x: trackX + trimStartX + 28, y: -height / 2 + 14)
                }
                .frame(height: height)
            } else {
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.white.opacity(0.03))
                    .frame(height: height)
                    .overlay(
                        Text("Drop audio file or click Open")
                            .font(.system(size: 12))
                            .foregroundColor(.white.opacity(0.25))
                    )
            }
        }
        .frame(height: height)
    }

    private func waveformPath(width: CGFloat, height: CGFloat) -> Path {
        let samples = track.waveformSamples
        guard !samples.isEmpty else { return Path() }

        let totalSamples = samples.count
        let trimStartFrac = track.duration > 0 ? track.trimStart / track.duration : 0
        let trimEndFrac = track.duration > 0 ? track.trimEnd / track.duration : 0
        let startIdx = Int(Double(totalSamples) * trimStartFrac)
        let endIdx = totalSamples - Int(Double(totalSamples) * trimEndFrac)
        let visibleSamples = Array(samples[max(0, startIdx)..<min(totalSamples, endIdx)])

        guard !visibleSamples.isEmpty else { return Path() }

        return Path { path in
            let count = visibleSamples.count
            let step = width / CGFloat(count)
            let mid = height / 2

            path.move(to: CGPoint(x: 0, y: mid))

            for i in 0..<count {
                let x = CGFloat(i) * step
                let amp = CGFloat(visibleSamples[i]) * mid * 0.9
                path.addLine(to: CGPoint(x: x, y: mid - amp))
            }

            path.addLine(to: CGPoint(x: width, y: mid))

            for i in stride(from: count - 1, through: 0, by: -1) {
                let x = CGFloat(i) * step
                let amp = CGFloat(visibleSamples[i]) * mid * 0.9
                path.addLine(to: CGPoint(x: x, y: mid + amp))
            }

            path.closeSubpath()
        }
    }

    private func trimHandle(isStart: Bool) -> some View {
        RoundedRectangle(cornerRadius: 2)
            .fill(Color.yellow.opacity(0.9))
            .frame(width: trimHandleWidth, height: height)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .stroke(Color.yellow, lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.5), radius: 2)
            .contentShape(Rectangle())
            .onHover { hovering in
                if hovering {
                    NSCursor.resizeLeftRight.push()
                } else {
                    NSCursor.pop()
                }
            }
    }

    private func dragGesture() -> some Gesture {
        DragGesture(minimumDistance: 2)
            .onChanged { value in
                if !isDragging {
                    isDragging = true
                    dragStartOffset = track.offset
                }
                let delta = Double(value.translation.width) / project.pixelsPerSecond
                track.offset = max(0, dragStartOffset + delta)
            }
            .onEnded { _ in
                isDragging = false
                if track.offset != dragStartOffset {
                    let finalOffset = track.offset
                    track.offset = dragStartOffset
                    project.setOffset(track, to: finalOffset, undoManager: undoManager)
                }
            }
    }

    private func trimStartGesture() -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                if !isDraggingTrimStart {
                    isDraggingTrimStart = true
                    trimDragOrigin = track.trimStart
                }
                let delta = Double(value.translation.width) / project.pixelsPerSecond
                let newTrim = max(0, min(trimDragOrigin + delta, track.duration - track.trimEnd - 0.1))
                track.trimStart = newTrim
            }
            .onEnded { _ in
                isDraggingTrimStart = false
                let finalTrim = track.trimStart
                track.trimStart = trimDragOrigin
                project.setTrimStart(track, to: finalTrim, undoManager: undoManager)
            }
    }

    private func trimEndGesture() -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                if !isDraggingTrimEnd {
                    isDraggingTrimEnd = true
                    trimDragOrigin = track.trimEnd
                }
                let delta = -Double(value.translation.width) / project.pixelsPerSecond
                let newTrim = max(0, min(trimDragOrigin + delta, track.duration - track.trimStart - 0.1))
                track.trimEnd = newTrim
            }
            .onEnded { _ in
                isDraggingTrimEnd = false
                let finalTrim = track.trimEnd
                track.trimEnd = trimDragOrigin
                project.setTrimEnd(track, to: finalTrim, undoManager: undoManager)
            }
    }
}
