# Default ProGuard/R8 rules for the WebView wrapper.
# Keep any @JavascriptInterface members if you add a JS bridge later.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
