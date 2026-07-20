# DSCC 2027 — iOS App

A lightweight **iOS WebView wrapper** (SwiftUI + `WKWebView`) for the Dubai Stem
Cell Congress 2027 attendee site — the iOS counterpart to [`../android`](../android).
It loads the existing FastAPI web app inside a native shell and adds:

- **Persistent login** — uses the default (non-ephemeral) `WKWebsiteDataStore`, so
  the JWT session cookie survives app restarts. No backend changes required.
- **Pull-to-refresh**, a loading indicator, and a native **offline / retry** screen.
- **External links** (other domains, `mailto:`, `tel:`) open in Safari / the phone app;
  everything on the congress host stays in the WebView.
- Back/forward swipe gestures and dark theming matching the web app
  (bg `#050709`, teal `#0FB5AE`).

All screens and logic live on the server — this is a thin client. The placeholder
home tiles (Schedule, Papers, CME credits, etc.) are shown as-is; making them
functional is separate backend work.

## Requirements

- **macOS** with **Xcode 15+** (this project targets iOS 15.0+).
- A running instance of the FastAPI backend (see the repo root README).

## Project layout

```
ios/
├── DSCC2027.xcodeproj/        # committed Xcode project — open this
├── project.yml                # XcodeGen spec (fallback to regenerate the project)
└── DSCC2027/
    ├── DSCC2027App.swift       # @main App entry point
    ├── ContentView.swift       # WebView host + offline/retry UI + theme colors
    ├── WebView.swift           # UIViewRepresentable wrapping WKWebView
    ├── WebViewModel.swift      # shared loading/offline state
    ├── Config.swift            # BASE_URL (per Debug/Release build)
    ├── Info.plist              # ATS exception for local HTTP, launch screen
    └── Assets.xcassets/        # AppIcon (add a 1024px image), accent + launch colors
```

## Configure the server URL

Edit `DSCC2027/Config.swift`:

| Build | `baseURL` default | Use it for |
|-------|-------------------|------------|
| **Debug** | `http://localhost:8000` | Simulator against a server on your Mac |
| **Release** | `https://your-deployed-domain.example` | **Edit this** to your real HTTPS URL |

The iOS **Simulator shares the Mac's network**, so `localhost` reaches a server
running on your machine directly (no `10.0.2.2` needed, unlike Android).
For a **physical device**, set `baseURL` to your Mac's LAN IP (e.g.
`http://192.168.1.20:8000`) and add that host to the `NSExceptionDomains` in
`Info.plist`, or serve the site over HTTPS.

Cleartext HTTP is permitted only for `localhost` / `127.0.0.1` via the App
Transport Security exception in `Info.plist`; production traffic should be HTTPS.

## Run it

1. **Start the backend** from the repo root:
   ```bash
   docker-compose up            # serves http://localhost:8000
   # or: uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
2. **Open `ios/DSCC2027.xcodeproj`** in Xcode.
3. Select an **iPhone Simulator** and press **Run** (⌘R). The app loads
   `http://localhost:8000/`; sign up / log in and the session persists across
   relaunches.

### App icon
`Assets.xcassets/AppIcon.appiconset` has a single 1024×1024 slot with no image yet
(Xcode shows a warning and uses a placeholder). Drop a `1024×1024` PNG into that
slot in Xcode's asset catalog before submitting to TestFlight / the App Store.

### Regenerating the project (optional)
If you prefer to manage the project from `project.yml`:
```bash
brew install xcodegen
cd ios && xcodegen generate
```

## Notes / not verified here
This project was authored in a Linux environment without macOS/Xcode, so it has
**not been compiled**. The Swift sources and project structure are standard; do the
first build in Xcode, which will surface any signing/SDK specifics for your machine.
