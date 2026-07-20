import SwiftUI

struct ContentView: View {
    @StateObject private var model = WebViewModel()

    var body: some View {
        ZStack {
            Color(hex: 0x050709).ignoresSafeArea()

            WebView(model: model)
                .ignoresSafeArea(edges: .bottom)

            if model.isLoading && !model.loadFailed {
                ProgressView()
                    .progressViewStyle(.circular)
                    .tint(Color(hex: 0x0FB5AE))
            }

            if model.loadFailed {
                offlineView
            }
        }
        .preferredColorScheme(.dark)
    }

    private var offlineView: some View {
        VStack(spacing: 16) {
            Text("Can’t reach the server")
                .font(.title3.bold())
                .foregroundColor(Color(hex: 0xE8ECF2))

            Text("Check your connection and make sure the congress server is running, then try again.")
                .multilineTextAlignment(.center)
                .foregroundColor(Color(hex: 0x8B95A8))
                .padding(.horizontal, 32)

            Button(action: { model.reloadHome() }) {
                Text("Retry")
                    .fontWeight(.semibold)
                    .padding(.horizontal, 28)
                    .padding(.vertical, 12)
                    .background(Color(hex: 0x0FB5AE))
                    .foregroundColor(Color(hex: 0x050709))
                    .clipShape(Capsule())
            }
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(hex: 0x050709))
    }
}

extension Color {
    /// Create a Color from a 0xRRGGBB integer (matches the web theme tokens).
    init(hex: UInt, alpha: Double = 1.0) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            opacity: alpha
        )
    }
}

#Preview {
    ContentView()
}
