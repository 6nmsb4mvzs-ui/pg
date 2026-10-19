import SwiftUI

struct ClipView: View {
    @ObservedObject var clip: AudioClip
    @ObservedObject var project: MashupProject
    @Environment(\.undoManager) var undoManager

    let color: Color
    let height: CGFloat

    @State private var isDragging = false
    @State private var dragStartOffset: TimeInterval = 0
    @State private var isDraggingTrimStart = false
    @State private var isDraggingTrimEnd = false
    @State private var trimDragOrigin: TimeInterval = 0
    @State private var isHovering = false

    private let trimHandleWidth: CGFloat = 8

    var body: some View {
        let pps = project.pixelsPerSecond
        let clipX = project.timeToX(clip.offset)
        let sourceWidth = CGFloat(clip.sourceDuration * pps)
        let trimStartPx = CGFloat(clip.trimStart * pps)
        let trimEndPx = CGFloat(clip.trimEnd * pps)
        let visibleWidth = max(1, sourceWidth - trimStartPx - trimEndPx)

        ZStack(alignment: .leading) {
            if trimStartPx > 1 {
                Rectangle()
                    .fill(color.opacity(0.05))
                    .frame(width: trimStartPx, height: height)
                    .offset(x: clipX)
                    .allowsHitTesting(false)
            }
            if trimEndPx > 1 {
                Rectangle()
                    .fill(color.opacity(0.05))
                    .frame(width: trimEndPx, height: height)
                    .offset(x: clipX + sourceWidth - trimEndPx)
                    .allowsHitTesting(false)
            }

            waveformShape(width: visibleWidth, height: height)
                .fill(
                    LinearGradient(
                        colors: [color.opacity(0.85), color.opacity(0.4)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: visibleWidth, height: height)
                .background(
                    RoundedRectangle(cornerRadius: 5)
                        .fill(color.opacity(isHovering ? 0.16 : 0.08))
                )
                .clipShape(RoundedRectangle(cornerRadius: 5))
                .overlay(
                    RoundedRectangle(cornerRadius: 5)
                        .stroke(
                            isDragging ? Color.white.opacity(0.7) :
                            isHovering ? color.opacity(0.6) : color.opacity(0.3),
                            lineWidth: isDragging ? 1.5 : 1
                        )
                )
                .shadow(color: color.opacity(isDragging ? 0.25 : 0), radius: 8)
                .offset(x: clipX + trimStartPx)
                .gesture(dragGesture())
                .onHover { isHovering = $0 }

            trimHandle(isStart: true)
                .offset(x: clipX + trimStartPx - trimHandleWidth / 2)
                .gesture(trimStartGesture())

            trimHandle(isStart: false)
                .offset(x: clipX + trimStartPx + visibleWidth - trimHandleWidth / 2)
                .gesture(trimEndGesture())

            Text(clip.sourceName)
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .foregroundColor(.white.opacity(0.65))
                .lineLimit(1)
                .padding(.horizontal, 5)
                .padding(.vertical, 2)
                .background(Color.black.opacity(0.45))
                .cornerRadius(3)
                .offset(x: clipX + trimStartPx + 4, y: -height / 2 + 12)
                .allowsHitTesting(false)

            if let bpm = clip.bpm, visibleWidth > 80 {
                Text("\(String(format: "%.0f", bpm))")
                    .font(.system(size: 8, weight: .medium, design: .monospaced))
                    .foregroundColor(color.opacity(0.5))
                    .offset(x: clipX + trimStartPx + 4, y: height / 2 - 14)
                    .allowsHitTesting(false)
            }
        }
        .frame(height: height)
    }

    private func waveformShape(width: CGFloat, height: CGFloat) -> Path {
        let samples = clip.fullWaveform
        guard !samples.isEmpty, clip.sourceDuration > 0 else { return Path() }

        let totalSamples = samples.count
        let trimStartFrac = clip.trimStart / clip.sourceDuration
        let trimEndFrac = clip.trimEnd / clip.sourceDuration
        let startIdx = max(0, Int(Double(totalSamples) * trimStartFrac))
        let endIdx = min(totalSamples, totalSamples - Int(Double(totalSamples) * trimEndFrac))
        guard startIdx < endIdx else { return Path() }
        let visible = Array(samples[startIdx..<endIdx])
        guard !visible.isEmpty else { return Path() }

        return Path { path in
            let count = visible.count
            let step = width / CGFloat(count)
            let mid = height / 2

            path.move(to: CGPoint(x: 0, y: mid))
            for i in 0..<count {
                let x = CGFloat(i) * step
                let amp = CGFloat(visible[i]) * mid * 0.85
                path.addLine(to: CGPoint(x: x, y: mid - amp))
            }
            path.addLine(to: CGPoint(x: width, y: mid))
            for i in stride(from: count - 1, through: 0, by: -1) {
                let x = CGFloat(i) * step
                let amp = CGFloat(visible[i]) * mid * 0.85
                path.addLine(to: CGPoint(x: x, y: mid + amp))
            }
            path.closeSubpath()
        }
    }

    private func trimHandle(isStart: Bool) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 2)
                .fill(Color.yellow.opacity(0.8))
                .frame(width: trimHandleWidth, height: height * 0.6)
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(Color.yellow, lineWidth: 0.5)
                )
                .shadow(color: .black.opacity(0.3), radius: 2)
        }
        .frame(width: trimHandleWidth + 8, height: height)
        .contentShape(Rectangle())
        .onHover { hovering in
            if hovering { NSCursor.resizeLeftRight.push() }
            else { NSCursor.pop() }
        }
    }

    private func dragGesture() -> some Gesture {
        DragGesture(minimumDistance: 3)
            .onChanged { value in
                if !isDragging {
                    isDragging = true
                    dragStartOffset = clip.offset
                    NSCursor.closedHand.push()
                }
                let delta = Double(value.translation.width) / project.pixelsPerSecond
                clip.offset = max(-clip.trimStart, dragStartOffset + delta)
            }
            .onEnded { _ in
                NSCursor.pop()
                isDragging = false
                if clip.offset != dragStartOffset {
                    let finalOff = clip.offset
                    clip.offset = dragStartOffset
                    project.setClipOffset(clip, to: finalOff, undoManager: undoManager)
                }
            }
    }

    private func trimStartGesture() -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                if !isDraggingTrimStart {
                    isDraggingTrimStart = true
                    trimDragOrigin = clip.trimStart
                }
                let delta = Double(value.translation.width) / project.pixelsPerSecond
                clip.trimStart = max(0, min(trimDragOrigin + delta, clip.sourceDuration - clip.trimEnd - 0.1))
            }
            .onEnded { _ in
                isDraggingTrimStart = false
                let finalTrim = clip.trimStart
                clip.trimStart = trimDragOrigin
                project.setClipTrimStart(clip, to: finalTrim, undoManager: undoManager)
            }
    }

    private func trimEndGesture() -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                if !isDraggingTrimEnd {
                    isDraggingTrimEnd = true
                    trimDragOrigin = clip.trimEnd
                }
                let delta = -Double(value.translation.width) / project.pixelsPerSecond
                clip.trimEnd = max(0, min(trimDragOrigin + delta, clip.sourceDuration - clip.trimStart - 0.1))
            }
            .onEnded { _ in
                isDraggingTrimEnd = false
                let finalTrim = clip.trimEnd
                clip.trimEnd = trimDragOrigin
                project.setClipTrimEnd(clip, to: finalTrim, undoManager: undoManager)
            }
    }
}
