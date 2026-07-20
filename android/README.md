# DSCC 2027 — Android App

A lightweight **Android WebView wrapper** for the Dubai Stem Cell Congress 2027
attendee site. It loads the existing FastAPI web app (landing, login, signup,
home, profile, settings, certificates) inside a native shell and adds:

- **Persistent login** — the JWT session cookie is stored via Android's
  `CookieManager` and survives app restarts (no backend changes required).
- **Pull-to-refresh**, a top loading bar, and a native **offline / retry** screen.
- **External links** (other domains, `mailto:`, `tel:`) open in the browser/phone
  app instead of the WebView.
- **File uploads** (e.g. profile photo `<input type="file">`) via the system picker.
- App icon, dark splash screen, and status bar matching the web theme
  (bg `#050709`, teal `#0FB5AE`, gold `#D4A843`).

The app is a thin client: **all screens and logic live on the server.** Making the
placeholder home tiles (Schedule, Papers, CME credits, etc.) functional is separate
backend work.

## Requirements

- **Android Studio** (Ladybug / 2024.2+ recommended) — bundles the JDK and Android SDK.
- Android SDK Platform **35**, min supported device API **24** (Android 7.0).

## Project layout

```
android/
├── settings.gradle.kts / build.gradle.kts / gradle.properties
├── gradlew / gradlew.bat / gradle/wrapper/       # Gradle 8.9 wrapper
└── app/
    ├── build.gradle.kts                          # BASE_URL is set here per build type
    └── src/main/
        ├── AndroidManifest.xml
        ├── java/com/dscc/conference/MainActivity.kt
        └── res/                                  # layout, theme, colors, icons, network config
```

## Configure the server URL

The app points at the FastAPI server via a Gradle `BuildConfig` field in
`app/build.gradle.kts`:

| Build type | `BASE_URL` default | Use it for |
|-----------|--------------------|------------|
| **debug** | `http://10.0.2.2:8000` | Emulator → server running on your host machine |
| **release** | `https://your-deployed-domain.example` | **Edit this** to your real HTTPS URL before shipping |

`10.0.2.2` is the Android emulator's alias for `localhost` on the host.
Cleartext HTTP is allowed **only** for `10.0.2.2` / `localhost` / `127.0.0.1`
(see `res/xml/network_security_config.xml`); everything else must be HTTPS.

## Run it against a local backend

1. **Start the backend** from the repo root:
   ```bash
   docker-compose up            # app + postgres, serves http://localhost:8000
   # or, without Docker:
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
2. **Open the `android/` folder in Android Studio** and let it sync Gradle
   (this generates `local.properties` with your SDK path automatically).
3. **Run** the `app` configuration on an emulator → it loads `http://10.0.2.2:8000/`.

### Physical device

Connect over USB, then forward the port so the phone can reach your machine:

```bash
adb reverse tcp:8000 tcp:8000
```

`http://10.0.2.2:8000` is not needed here; with the reverse tunnel the debug
build's `localhost`-family rules apply. (Alternatively set `BASE_URL` to your
machine's LAN IP and add it to the network security config.)

## Build from the command line

```bash
cd android
./gradlew assembleDebug        # outputs app/build/outputs/apk/debug/app-debug.apk
./gradlew installDebug         # build + install on a connected device/emulator
```

> **Note:** The first build downloads the Gradle 8.9 distribution and Android
> Gradle Plugin. If you build in a restricted/offline network, do the first
> sync in Android Studio, which manages the SDK and distribution for you.
