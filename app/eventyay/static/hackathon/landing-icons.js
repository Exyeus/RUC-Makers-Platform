function sampleSegment(p1, p2, count) {
    const pts = [];
    for (let i = 0; i <= count; i++) {
        const t = i / count;
        pts.push([
            p1[0] + (p2[0] - p1[0]) * t,
            p1[1] + (p2[1] - p1[1]) * t
        ]);
    }
    return pts;
}

function sampleArc(cx, cy, r, startAngle, endAngle, count) {
    const pts = [];
    for (let i = 0; i <= count; i++) {
        const a = startAngle + (endAngle - startAngle) * (i / count);
        pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]);
    }
    return pts;
}

function sampleRoundedRect(x, y, w, h, r, perSide = 5) {
    const left = x - w / 2, right = x + w / 2, top = y + h / 2, bottom = y - h / 2;
    return [
        ...sampleSegment([left + r, top], [right - r, top], perSide),
        ...sampleArc(right - r, top - r, r, Math.PI / 2, 0, 3),
        ...sampleSegment([right, top - r], [right, bottom + r], perSide),
        ...sampleArc(right - r, bottom + r, r, 0, -Math.PI / 2, 3),
        ...sampleSegment([right - r, bottom], [left + r, bottom], perSide),
        ...sampleArc(left + r, bottom + r, r, -Math.PI / 2, -Math.PI, 3),
        ...sampleSegment([left, bottom + r], [left, top - r], perSide),
        ...sampleArc(left + r, top - r, r, Math.PI, Math.PI / 2, 3)
    ];
}

function buildFluentTemplates() {
    return [
        {
            id: 'file',
            anchors: [[-0.75, 1.05], [0.25, 1.05], [0.75, 0.55], [0.75, -1.05], [-0.75, -1.05], [0.25, 0.55]],
            strokes: [
                sampleSegment([-0.75, 1.05], [0.25, 1.05], 7),
                sampleSegment([0.25, 1.05], [0.75, 0.55], 5),
                sampleSegment([0.75, 0.55], [0.75, -1.05], 10),
                sampleSegment([0.75, -1.05], [-0.75, -1.05], 9),
                sampleSegment([-0.75, -1.05], [-0.75, 1.05], 12),
                sampleSegment([0.25, 1.05], [0.25, 0.55], 4),
                sampleSegment([0.25, 0.55], [0.75, 0.55], 4),
                sampleSegment([-0.42, 0.15], [0.42, 0.15], 6),
                sampleSegment([-0.42, -0.25], [0.42, -0.25], 6),
                sampleSegment([-0.42, -0.62], [0.15, -0.62], 4)
            ]
        },
        {
            id: 'image',
            anchors: [[-1.05, 0.82], [1.05, 0.82], [1.05, -0.82], [-1.05, -0.82], [-0.52, 0.35], [-0.15, 0.12], [0.42, -0.08]],
            strokes: [
                sampleRoundedRect(0, 0, 2.1, 1.65, 0.24, 7),
                sampleArc(-0.52, 0.32, 0.22, 0, Math.PI * 2, 12),
                sampleSegment([-0.92, -0.65], [-0.18, 0.15], 8),
                sampleSegment([-0.18, 0.15], [0.18, -0.25], 5),
                sampleSegment([0.18, -0.25], [0.48, 0.05], 4),
                sampleSegment([0.48, 0.05], [0.95, -0.65], 6)
            ]
        },
        {
            id: 'tool',
            anchors: [[-0.48, 0.65], [-1.08, 0.0], [-0.48, -0.65], [0.48, 0.65], [1.08, 0.0], [0.48, -0.65], [0.22, 0.88], [-0.22, -0.88]],
            strokes: [
                sampleSegment([-0.48, 0.65], [-1.08, 0.0], 8),
                sampleSegment([-1.08, 0.0], [-0.48, -0.65], 8),
                sampleSegment([0.48, 0.65], [1.08, 0.0], 8),
                sampleSegment([1.08, 0.0], [0.48, -0.65], 8),
                sampleSegment([0.24, 0.88], [-0.24, -0.88], 12)
            ]
        },
        {
            id: 'game',
            anchors: [[-0.65, 0.52], [0.65, 0.52], [-0.95, -0.65], [0.95, -0.65], [-0.48, 0.08], [0.48, 0.08]],
            strokes: [
                sampleSegment([-0.58, 0.52], [0.58, 0.52], 8),
                sampleArc(-0.62, 0.15, 0.42, Math.PI * 0.5, Math.PI * 1.15, 7),
                sampleArc(0.62, 0.15, 0.42, Math.PI * 0.5, -Math.PI * 0.15, 7),
                sampleSegment([-1.02, 0.02], [-0.95, -0.58], 6),
                sampleArc(-0.72, -0.55, 0.24, Math.PI, Math.PI * 1.85, 6),
                sampleSegment([1.02, 0.02], [0.95, -0.58], 6),
                sampleArc(0.72, -0.55, 0.24, 0, -Math.PI * 0.85, 6),
                sampleSegment([-0.48, -0.35], [0.48, -0.35], 7),
                sampleSegment([-0.64, 0.08], [-0.32, 0.08], 4),
                sampleSegment([-0.48, 0.24], [-0.48, -0.08], 4),
                [[0.48, 0.24], [0.64, 0.08], [0.48, -0.08], [0.32, 0.08]]
            ]
        },
        {
            id: 'platform',
            anchors: [[-0.48, 0.48], [0.56, 0.56], [-0.48, -0.48], [0.48, -0.48]],
            strokes: [
                sampleRoundedRect(-0.48, 0.48, 0.72, 0.72, 0.16, 4),
                sampleRoundedRect(0.54, 0.54, 0.72, 0.72, 0.16, 4),
                sampleRoundedRect(-0.48, -0.48, 0.72, 0.72, 0.16, 4),
                sampleRoundedRect(0.48, -0.48, 0.72, 0.72, 0.16, 4)
            ]
        },
        {
            id: 'music',
            anchors: [[-0.55, -0.62], [0.45, -0.45], [-0.32, 0.72], [0.68, 0.92]],
            strokes: [
                sampleArc(-0.56, -0.62, 0.26, 0, Math.PI * 2, 12),
                sampleArc(0.44, -0.45, 0.26, 0, Math.PI * 2, 12),
                sampleSegment([-0.30, -0.62], [-0.30, 0.75], 10),
                sampleSegment([0.70, -0.45], [0.70, 0.92], 10),
                sampleSegment([-0.30, 0.75], [0.70, 0.92], 9),
                sampleSegment([-0.30, 0.52], [0.70, 0.69], 9)
            ]
        },
        {
            id: 'ai',
            anchors: [[0.0, 1.05], [1.05, 0.0], [0.0, -1.05], [-1.05, 0.0], [0.68, 0.72], [-0.68, -0.68]],
            strokes: [
                sampleArc(0.55, 0.55, 0.55, Math.PI, Math.PI * 1.5, 8),
                sampleArc(0.55, -0.55, 0.55, Math.PI * 0.5, Math.PI, 8),
                sampleArc(-0.55, -0.55, 0.55, 0, Math.PI * 0.5, 8),
                sampleArc(-0.55, 0.55, 0.55, -Math.PI * 0.5, 0, 8),
                sampleSegment([0.68, 0.96], [0.68, 0.48], 5),
                sampleSegment([0.44, 0.72], [0.92, 0.72], 5),
                [[0, 0], [0.12, 0], [-0.12, 0], [0, 0.12], [0, -0.12]]
            ]
        },
        {
            id: 'web',
            anchors: [[0, 1.02], [0, -1.02], [-1.02, 0], [1.02, 0], [0, 0]],
            strokes: [
                sampleArc(0, 0, 1.02, 0, Math.PI * 2, 26),
                sampleSegment([0, 1.02], [0, -1.02], 12),
                sampleSegment([-1.02, 0], [1.02, 0], 12),
                ...(() => {
                    const m1 = [], m2 = [];
                    for (let i = 0; i <= 14; i++) {
                        const a = -Math.PI / 2 + (Math.PI * i) / 14;
                        m1.push([Math.cos(a) * 0.52, Math.sin(a) * 1.02]);
                        m2.push([-Math.cos(a) * 0.52, Math.sin(a) * 1.02]);
                    }
                    return [m1, m2];
                })(),
                sampleSegment([-0.86, 0.5], [0.86, 0.5], 9),
                sampleSegment([-0.86, -0.5], [0.86, -0.5], 9)
            ]
        },
        {
            id: 'hardware',
            anchors: [[-0.65, 0.65], [0.65, 0.65], [0.65, -0.65], [-0.65, -0.65], [0, 0]],
            strokes: [
                sampleRoundedRect(0, 0, 1.35, 1.35, 0.2, 5),
                sampleRoundedRect(0, 0, 0.65, 0.65, 0.1, 3),
                sampleSegment([-0.38, 0.68], [-0.38, 1.05], 3),
                sampleSegment([0.0, 0.68], [0.0, 1.05], 3),
                sampleSegment([0.38, 0.68], [0.38, 1.05], 3),
                sampleSegment([-0.38, -0.68], [-0.38, -1.05], 3),
                sampleSegment([0.0, -0.68], [0.0, -1.05], 3),
                sampleSegment([0.38, -0.68], [0.38, -1.05], 3),
                sampleSegment([-0.68, 0.38], [-1.05, 0.38], 3),
                sampleSegment([-0.68, 0.0], [-1.05, 0.0], 3),
                sampleSegment([-0.68, -0.38], [-1.05, -0.38], 3),
                sampleSegment([0.68, 0.38], [1.05, 0.38], 3),
                sampleSegment([0.68, 0.0], [1.05, 0.0], 3),
                sampleSegment([0.68, -0.38], [1.05, -0.38], 3)
            ]
        },
        {
            id: 'alchemy',
            anchors: [[-0.32, 0.98], [0.32, 0.98], [-0.22, 0.42], [0.22, 0.42], [-0.88, -0.78], [0.88, -0.78]],
            strokes: [
                sampleSegment([-0.35, 0.98], [0.35, 0.98], 5),
                sampleSegment([-0.22, 0.98], [-0.22, 0.42], 5),
                sampleSegment([0.22, 0.98], [0.22, 0.42], 5),
                sampleSegment([-0.22, 0.42], [-0.88, -0.72], 9),
                sampleSegment([0.22, 0.42], [0.88, -0.72], 9),
                sampleArc(-0.72, -0.72, 0.18, Math.PI, Math.PI * 1.5, 4),
                sampleArc(0.72, -0.72, 0.18, -Math.PI * 0.5, 0, 4),
                sampleSegment([-0.72, -0.90], [0.72, -0.90], 10),
                sampleSegment([-0.55, -0.22], [0.55, -0.22], 8),
                sampleArc(-0.18, -0.52, 0.11, 0, Math.PI * 2, 6),
                sampleArc(0.22, -0.45, 0.09, 0, Math.PI * 2, 6),
                sampleArc(0.05, 0.08, 0.08, 0, Math.PI * 2, 6)
            ]
        }
    ];
}


export const ARTIFACT_TEMPLATES = buildFluentTemplates();
