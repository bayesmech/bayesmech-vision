import UIKit

enum AppColors {
    static let bgDark = UIColor(hex: 0x111827)
    static let bgHeader = UIColor(hex: 0x111827)
    static let bgPanel = UIColor(hex: 0x1F2937)
    static let cardBackground = UIColor(hex: 0x374151)
    static let divider = UIColor(hex: 0x4B5563)
    static let accentRed = UIColor(hex: 0xEF4444)
    static let analysisBlue = UIColor(hex: 0x2563EB)
    static let textPrimary = UIColor.white
    static let textSecondary = UIColor(hex: 0x9CA3AF)
    static let navInactive = UIColor(hex: 0x6B7280)
}

private extension UIColor {
    convenience init(hex: UInt32, alpha: CGFloat = 1.0) {
        let red = CGFloat((hex >> 16) & 0xFF) / 255.0
        let green = CGFloat((hex >> 8) & 0xFF) / 255.0
        let blue = CGFloat(hex & 0xFF) / 255.0
        self.init(red: red, green: green, blue: blue, alpha: alpha)
    }
}
