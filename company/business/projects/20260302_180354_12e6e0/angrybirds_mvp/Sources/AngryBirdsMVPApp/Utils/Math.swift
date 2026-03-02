import CoreGraphics

extension CGPoint {
    static func - (lhs: CGPoint, rhs: CGPoint) -> CGVector {
        CGVector(dx: lhs.x - rhs.x, dy: lhs.y - rhs.y)
    }
}

extension CGVector {
    static func * (lhs: CGVector, rhs: CGFloat) -> CGVector {
        CGVector(dx: lhs.dx * rhs, dy: lhs.dy * rhs)
    }

    var length: CGFloat {
        sqrt(dx*dx + dy*dy)
    }

    func clamped(maxLength: CGFloat) -> CGVector {
        let len = length
        guard len > maxLength, len > 0 else { return self }
        let scale = maxLength / len
        return CGVector(dx: dx * scale, dy: dy * scale)
    }
}
