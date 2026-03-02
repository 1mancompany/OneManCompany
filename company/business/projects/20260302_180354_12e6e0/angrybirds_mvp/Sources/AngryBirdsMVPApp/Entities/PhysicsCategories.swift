import Foundation

enum PhysicsCategory {
    static let bird: UInt32  = 1 << 0
    static let pig: UInt32   = 1 << 1
    static let block: UInt32 = 1 << 2
    static let world: UInt32 = 1 << 3
}
