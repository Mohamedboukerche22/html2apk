# html2apk

Turn an HTML page (or a whole folder of HTML, CSS and JS) into a signed Android
APK. The page runs in a WebView, so everything you wrote for the browser just
works on the phone.

This is my own little CLI tool, no web hosting, no app stores, no email signup.
You get an `.apk` file on your disk, ready to install.

## Quick start

```sh
html2apk my-site/                      # builds my-site.apk from index.html
html2apk my-site/ -n "My App"          # with a proper app name
html2apk my-site/ -l logo.png -o app.apk
html2apk single-page.html              # yes, a lone HTML file is fine too
```

When it finishes it prints the path of the APK. Put that file on your phone and
install it (Android will ask you to allow unknown sources, which is normal).

## Install

The whole tool is one Python file.

```sh
git clone https://github.com/Mohamedboukerche22/html2apk.git
cd html2apk
ln -s "$(pwd)/html2apk.py" ~/.local/bin/html2apk
pip install pillow      # used only to draw the icon
```

Requirements:

- Python 3.9+
- Pillow (for icon generation, optional if you always pass `--logo`)
- A JDK 17-21 (Arch: `pacman -S jdk21-openjdk`, Ubuntu: `apt install openjdk-21-jdk`)

The Android SDK is downloaded automatically the first time you build
(it needs `sdkmanager`, build-tools and the platform jar, roughly a few hundred
MB, one time). If you already have the SDK, set `ANDROID_SDK_ROOT` and
html2apk will use it instead of downloading anything.

## What you can feed it

- A folder: anything inside gets bundled. The entry file defaults to
  `index.html`, change it with `--entry`.
- A single `.html` file: it becomes `index.html` in the app.
- A logo image (png, jpg, svg) via `--logo`.

If you do not pass a logo, the tool draws one itself: the first letter of the
app name, in white, on a background colour that is picked from your app name.
So a name like "Hydra" always gets the same colour, and it won't collide with
"Hamster".

## How it works

Two halves, put together.

**The Android side.** A tiny app with one Activity and one WebView. The
Activity loads your files from the app's own protected storage, at
`file:///android_asset/www/index.html`. JavaScript is on, localStorage works,
the back button walks the WebView history before it closes the app. Nothing
else. No framework, no runtime to download.

**The APK side.** The APK is put together with the standard Android command
line tools, run directly:

1. `aapt2` compiles `res/` and links it with the manifest into the base APK,
   and emits `R.java`.
2. `javac` compiles `MainActivity.java` and `R.java` against `android.jar`.
3. `d8` converts the `.class` files into Android's `classes.dex`.
4. The dex file is packed into the APK.
5. `zipalign` fixes up the byte alignment so the APK installs cleanly.
6. `apksigner` signs it with a debug keystore. The keystore lives at
   `~/.html2apk/debug.keystore` and is created the first time.

There is no Gradle anywhere in this. Fewer moving parts, faster builds.

Everything runs with the JDK in the 17-21 range because Android's build tools
still expect that.

## Options

| Option | What it does |
| --- | --- |
| `input` | folder or HTML file |
| `-n, --name` | app name shown under the icon. Default is the folder/file name. |
| `-l, --logo` | path to the logo image. Default is a drawn "first letter" icon. |
| `-o, --output` | where the APK goes. Default is `<name>.apk` in the current dir. |
| `--entry` | entry HTML file inside the folder. Default `index.html`. |
| `--package` | Android package id. Default `com.html2apk.<name>`. |
| `--icon-bg` | hex colour for the icon background, e.g. `--icon-bg 0F9D58`. |
| `--version-name`, `--version-code` | the version shown to Android. |
| `--min-sdk` | minimum Android version (API level). Default 24. |
| `--keystore`, `--key-alias`, `--store-pass`, `--key-pass` | sign with your own key instead of the debug key. |
| `--work-dir` | keep the intermediate build files, useful to debug. |
| `--sdk` | point at a specific Android SDK. |

## Notes and limitations

- The app has the Internet permission, so pages can call APIs or load remote
  content. Everything bundled locally loads instantly, offline too.
- Icons are generated for every screen density (legacy + adaptive for newer
  Android).
- The tool builds the app against Android 14 (API 34). It installs on
  anything from Android 7 up.

## License

MIT, see [LICENSE](LICENSE). Do what you like with it, I would not mind a
mention in your README.