import Foundation

/// Points the app at the FastAPI server.
///
/// The iOS Simulator shares the Mac's network, so `localhost` reaches a server
/// running on your machine. For a physical device, set this to your Mac's LAN IP
/// (e.g. http://192.168.1.20:8000) and add that host to the ATS exception in
/// Info.plist, or serve over HTTPS.
enum AppConfig {
    static let baseURL: URL = {
        #if DEBUG
        return URL(string: "http://localhost:8000")!
        #else
        // TODO: replace with your deployed HTTPS URL before shipping a release build.
        return URL(string: "https://your-deployed-domain.example")!
        #endif
    }()
}
