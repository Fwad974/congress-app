import SwiftUI
import WebKit

/// SwiftUI wrapper around WKWebView that loads the DSCC 2027 attendee site.
///
/// - Uses the default (persistent) website data store, so the JWT session cookie
///   survives app restarts — no manual cookie handling needed.
/// - Keeps same-host navigation in the app and hands external links / mailto /
///   tel off to the system.
/// - Adds pull-to-refresh and reports loading/offline state back to SwiftUI.
struct WebView: UIViewRepresentable {
    @ObservedObject var model: WebViewModel

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default() // persistent cookies/storage

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.isOpaque = false
        webView.backgroundColor = .black
        webView.scrollView.backgroundColor = .black

        let refresh = UIRefreshControl()
        refresh.addTarget(
            context.coordinator,
            action: #selector(Coordinator.handleRefresh(_:)),
            for: .valueChanged
        )
        webView.scrollView.refreshControl = refresh

        model.webView = webView
        webView.load(URLRequest(url: AppConfig.baseURL))
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        private let model: WebViewModel

        init(model: WebViewModel) { self.model = model }

        @objc func handleRefresh(_ sender: UIRefreshControl) {
            model.webView?.reload()
        }

        // MARK: Loading state

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            model.isLoading = true
            model.loadFailed = false
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            model.isLoading = false
            webView.scrollView.refreshControl?.endRefreshing()
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            model.isLoading = false
            webView.scrollView.refreshControl?.endRefreshing()
        }

        func webView(_ webView: WKWebView,
                     didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            model.isLoading = false
            model.loadFailed = true
            webView.scrollView.refreshControl?.endRefreshing()
        }

        // MARK: Link routing

        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            if let scheme = url.scheme?.lowercased(),
               scheme == "mailto" || scheme == "tel" || isExternal(url) {
                UIApplication.shared.open(url)
                decisionHandler(.cancel)
                return
            }

            decisionHandler(.allow)
        }

        // target="_blank" links: WKWebView won't open a new window, so route them here.
        func webView(_ webView: WKWebView,
                     createWebViewWith configuration: WKWebViewConfiguration,
                     for navigationAction: WKNavigationAction,
                     windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = navigationAction.request.url {
                if isExternal(url) {
                    UIApplication.shared.open(url)
                } else {
                    webView.load(navigationAction.request)
                }
            }
            return nil
        }

        /// True when the URL points outside our own backend host.
        private func isExternal(_ url: URL) -> Bool {
            guard let host = url.host else { return false }
            guard let baseHost = AppConfig.baseURL.host else { return true }
            return host != baseHost
        }
    }
}
