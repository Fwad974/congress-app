import Foundation
import WebKit

/// Shared state between SwiftUI and the underlying WKWebView.
final class WebViewModel: ObservableObject {
    @Published var isLoading = false
    @Published var loadFailed = false

    weak var webView: WKWebView?

    /// Reload the current session (used by the retry button and pull-to-refresh).
    func reloadHome() {
        loadFailed = false
        webView?.load(URLRequest(url: AppConfig.baseURL))
    }
}
