#!/usr/bin/env python3
"""html2apk - wrap an HTML/CSS/JS site into a signed Android APK (WebView)."""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES = SCRIPT_DIR / "templates"

CMDLINE_TOOLS_URL = (
    "https://dl.google.com/android/repository/"
    "commandlinetools-linux-11076708_latest.zip"
)
BUILD_TOOLS_VERSION = "34.0.0"
PLATFORM_VERSION = "android-34"

DENSITY_SCALE = {
    "mdpi": 1.0,
    "hdpi": 1.5,
    "xhdpi": 2.0,
    "xxhdpi": 3.0,
    "xxxhdpi": 4.0,
}
LEGACY_DP = 48
FOREGROUND_DP = 108

PALETTE = [
    "#E53935", "#D81B60", "#8E24AA", "#5E35B1", "#3949AB",
    "#1E88E5", "#039BE5", "#00897B", "#43A047", "#7CB342",
    "#FB8C00", "#F4511E", "#6D4C41", "#546E7A", "#00838F",
]

FONT_CANDIDATES = [
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
]

JDK_CANDIDATES = [
    "/usr/lib/jvm/java-21-openjdk",
    "/usr/lib/jvm/java-17-openjdk",
    "/usr/lib/jvm/java-11-openjdk",
    "/usr/lib/jvm/temurin-21-jdk-amd64",
]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

class BuildError(Exception):
    pass


def log(msg):
    print(f"[html2apk] {msg}", flush=True)


def run(cmd, env=None, cwd=None, capture=True):
    if capture:
        proc = subprocess.run(
            cmd, env=env, cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )
        if proc.returncode != 0:
            raise BuildError(
                f"command failed ({proc.returncode}): {' '.join(map(str, cmd))}\n"
                f"{proc.stdout}"
            )
        return proc.stdout
    subprocess.run(cmd, env=env, cwd=cwd, check=True)
    return ""


def find_sdk():
    for var in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        val = os.environ.get(var)
        if val and (Path(val) / "platforms").exists():
            return Path(val)
    candidate = Path.home() / "Android" / "Sdk"
    if candidate.exists():
        return candidate
    return candidate


def build_tools_dir(sdk):
    return sdk / "build-tools" / BUILD_TOOLS_VERSION


def sdk_ready(sdk):
    bt = build_tools_dir(sdk)
    return (
        (bt / "aapt2").exists()
        and (bt / "d8").exists()
        and (bt / "zipalign").exists()
        and (bt / "apksigner").exists()
        and (sdk / "platforms" / PLATFORM_VERSION / "android.jar").exists()
    )


def pick_jdk():
    env_home = os.environ.get("JAVA_HOME")
    if env_home and (Path(env_home) / "bin" / "javac").exists():
        return Path(env_home)
    for cand in JDK_CANDIDATES:
        if (Path(cand) / "bin" / "javac").exists():
            return Path(cand)
    javac = shutil.which("javac")
    if javac:
        return Path(javac).resolve().parent.parent
    raise BuildError(
        "No JDK found. Install a JDK (17-21 recommended) or set JAVA_HOME.")


def tool_env(sdk, jdk):
    env = dict(os.environ)
    env["ANDROID_SDK_ROOT"] = str(sdk)
    env["ANDROID_HOME"] = str(sdk)
    if jdk:
        env["JAVA_HOME"] = str(jdk)
        env["PATH"] = f"{jdk / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    return env


# --------------------------------------------------------------------------- #
# SDK bootstrap
# --------------------------------------------------------------------------- #

def ensure_sdk(sdk, yes=False):
    if sdk_ready(sdk):
        return
    log(f"Android SDK not found at {sdk}. Bootstrapping (one-time download)...")
    sdk.mkdir(parents=True, exist_ok=True)
    sdkmanager = sdk / "cmdline-tools" / "latest" / "bin" / "sdkmanager"

    if not sdkmanager.exists():
        log("Downloading Android command-line tools...")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            zpath = tmp / "cmdline-tools.zip"
            run(["curl", "-L", "--fail", "-o", str(zpath), CMDLINE_TOOLS_URL])
            run(["unzip", "-q", str(zpath), "-d", str(tmp / "x")])
            dest = sdk / "cmdline-tools" / "latest"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(tmp / "x" / "cmdline-tools"), str(dest))

    jdk = pick_jdk()
    env = tool_env(sdk, jdk)

    log("Accepting SDK licenses...")
    subprocess.run(
        [str(sdkmanager), "--licenses"],
        input=("y\n" * 30), env=env, text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    log(f"Installing platforms;{PLATFORM_VERSION} and build-tools;{BUILD_TOOLS_VERSION}...")
    run([
        str(sdkmanager), "--install",
        f"platforms;{PLATFORM_VERSION}",
        f"build-tools;{BUILD_TOOLS_VERSION}",
    ], env=env)

    if not sdk_ready(sdk):
        raise BuildError("SDK bootstrap finished but required tools are missing.")
    log("SDK ready.")


# --------------------------------------------------------------------------- #
# Naming / input helpers
# --------------------------------------------------------------------------- #

def slugify(text, fallback="app"):
    text = re.sub(r"[^a-zA-Z0-9]+", "", text or "").lower()
    if not text:
        text = fallback
    if not text[0].isalpha():
        text = "a" + text
    return text


def make_package(name):
    return f"com.html2apk.{slugify(name)}"


def xml_escape(text):
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace('"', "&quot;").replace("'", "\\'")
    return text


def first_letter(name):
    for ch in name or "":
        if ch.isalnum():
            return ch.upper()
    return "A"


def pick_bg_color(name):
    digest = hashlib.md5((name or "app").encode("utf-8")).hexdigest()
    return PALETTE[int(digest, 16) % len(PALETTE)]


# --------------------------------------------------------------------------- #
# Icon generation
# --------------------------------------------------------------------------- #

def find_font():
    from PIL import ImageFont
    for cand in FONT_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


def load_logo(path):
    from PIL import Image
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        conv = shutil.which("magick") or shutil.which("convert")
        if not conv:
            raise BuildError(f"Could not open logo image: {path}")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            out = tmp.name
        run([conv, str(path), out])
        img = Image.open(out).convert("RGBA")
        os.unlink(out)
        return img


def fit_cover(img, size):
    from PIL import Image
    w, h = img.size
    scale = max(size / w, size / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - size) // 2
    top = (nh - size) // 2
    return img.crop((left, top, left + size, top + size))


def rounded_mask(size, radius):
    from PIL import Image, ImageDraw
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def draw_letter(size, letter, color, font_path):
    from PIL import Image, ImageDraw, ImageFont
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    target = int(size * 0.58)
    font_size = int(size * 0.9)
    font = None
    while font_size > 4:
        font = (ImageFont.truetype(font_path, font_size)
                if font_path else ImageFont.load_default())
        bbox = draw.textbbox((0, 0), letter, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= target and h <= target:
            break
        font_size -= max(1, font_size // 20)
    draw.text((size / 2, size / 2), letter, font=font, fill=color, anchor="mm")
    return canvas


def generate_icons(res_dir, name, logo_path, bg_color):
    from PIL import Image

    res_dir = Path(res_dir)
    letter = first_letter(name)
    font_path = find_font()
    logo = load_logo(logo_path) if logo_path else None
    custom_bg = f"#{bg_color.lstrip('#').upper()}" if bg_color else None
    bg = custom_bg or ("#FFFFFF" if logo else pick_bg_color(name))

    for density, scale in DENSITY_SCALE.items():
        legacy = int(round(LEGACY_DP * scale))
        fg_size = int(round(FOREGROUND_DP * scale))
        out = res_dir / f"mipmap-{density}"
        out.mkdir(parents=True, exist_ok=True)

        # --- adaptive foreground (transparent, safe-zone content) ---------- #
        if logo:
            fg = Image.new("RGBA", (fg_size, fg_size), (0, 0, 0, 0))
            inner = int(fg_size * 0.62)
            art = fit_cover(logo, inner)
            off = (fg_size - inner) // 2
            fg.alpha_composite(art, (off, off))
        else:
            fg = draw_letter(fg_size, letter, (255, 255, 255, 255), font_path)

        # --- legacy launcher icon (opaque, rounded corners) --------------- #
        if logo:
            legacy_img = fit_cover(logo, legacy).convert("RGBA")
        else:
            legacy_img = Image.new("RGBA", (legacy, legacy), (0, 0, 0, 0))
            bg_rgb = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
            legacy_img.paste(bg_rgb + (255,), (0, 0, legacy, legacy))
            glyph = draw_letter(legacy, letter, (255, 255, 255, 255), font_path)
            legacy_img.alpha_composite(glyph)
        legacy_img.putalpha(rounded_mask(legacy, int(legacy * 0.22)))

        legacy_img.save(out / "ic_launcher.png")
        legacy_img.save(out / "ic_launcher_round.png")
        fg.save(out / "ic_launcher_foreground.png")

    return bg


# --------------------------------------------------------------------------- #
# Template rendering
# --------------------------------------------------------------------------- #

def render_templates(work_dir, package, app_name, version_code,
                     version_name, bg_color, entry):
    pkg_path = package.replace(".", "/")
    java_dir = work_dir / "java" / pkg_path
    java_dir.mkdir(parents=True, exist_ok=True)

    def read(rel):
        return (TEMPLATES / rel).read_text(encoding="utf-8")

    def write(rel, content):
        dest = work_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    write(f"java/{pkg_path}/MainActivity.java",
          (read("MainActivity.java").replace("__PACKAGE__", package)
           .replace("__ENTRY__", entry)))

    manifest = (read("AndroidManifest.xml")
                .replace("__PACKAGE__", package)
                .replace("__VERSION_CODE__", str(version_code))
                .replace("__VERSION_NAME__", version_name))
    write("AndroidManifest.xml", manifest)

    write("res/values/strings.xml",
          read("res/values/strings.xml").replace("__APP_NAME__",
                                                  xml_escape(app_name)))
    write("res/values/colors.xml",
          read("res/values/colors.xml").replace("__BG_COLOR__", bg_color))
    shutil.copy(TEMPLATES / "res/values/styles.xml", work_dir / "res/values/styles.xml")
    (work_dir / "res/mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
    for fname in ("ic_launcher.xml", "ic_launcher_round.xml"):
        shutil.copy(TEMPLATES / "res/mipmap-anydpi-v26" / fname,
                    work_dir / "res/mipmap-anydpi-v26" / fname)

    return java_dir


def stage_assets(work_dir, entry, source):
    assets = work_dir / "assets" / "www"
    assets.mkdir(parents=True, exist_ok=True)

    src = Path(source)
    if src.is_dir():
        for item in src.iterdir():
            if item.resolve() == assets.resolve():
                continue
            dest = assets / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        if not (assets / entry).exists():
            raise BuildError(
                f"entry file '{entry}' not found in {src}. "
                f"Use --entry to set the HTML entry point.")
    else:
        if not src.is_file():
            raise BuildError(f"input not found: {src}")
        shutil.copy2(src, assets / entry)


# --------------------------------------------------------------------------- #
# APK build pipeline
# --------------------------------------------------------------------------- #

def build_apk(args, sdk, jdk):
    bt = build_tools_dir(sdk)
    aapt2 = bt / "aapt2"
    d8 = bt / "d8"
    zipalign = bt / "zipalign"
    apksigner = bt / "apksigner"
    android_jar = sdk / "platforms" / PLATFORM_VERSION / "android.jar"
    env = tool_env(sdk, jdk)

    input_path = Path(args.input).resolve()
    app_name = args.name or (input_path.stem if input_path.is_file()
                             else input_path.name)
    package = args.package or make_package(app_name)
    version_name = args.version_name
    version_code = args.version_code

    if args.work_dir:
        work_dir = Path(args.work_dir).resolve()
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="html2apk-"))

    log(f"App name : {app_name}")
    log(f"Package  : {package}")
    log(f"Version  : {version_name} ({version_code})")

    bg_color = generate_icons(work_dir / "res", app_name,
                              args.logo, args.icon_bg)
    log(f"Icon     : {'custom logo' if args.logo else 'letter ' + first_letter(app_name)} (bg {bg_color})")

    java_dir = render_templates(work_dir, package, app_name,
                                version_code, version_name, bg_color,
                                args.entry)
    stage_assets(work_dir, args.entry, input_path)

    # 1. compile resources
    log("Compiling resources (aapt2)...")
    run([str(aapt2), "compile", "--dir", str(work_dir / "res"),
         "-o", str(work_dir / "res.zip")], env=env)

    # 2. link resources -> base.apk + R.java
    log("Linking resources...")
    gen_dir = work_dir / "gen"
    gen_dir.mkdir(exist_ok=True)
    run([
        str(aapt2), "link",
        "-o", str(work_dir / "base.apk"),
        "-I", str(android_jar),
        "--manifest", str(work_dir / "AndroidManifest.xml"),
        "-R", str(work_dir / "res.zip"),
        "-A", str(work_dir / "assets"),
        "--java", str(gen_dir),
        "--min-sdk-version", str(args.min_sdk),
        "--target-sdk-version", "34",
        "--version-code", str(version_code),
        "--version-name", version_name,
        "--auto-add-overlay",
    ], env=env)

    # 3. compile java
    log("Compiling Java...")
    classes_dir = work_dir / "classes"
    classes_dir.mkdir(exist_ok=True)
    sources = [str(p) for p in gen_dir.rglob("*.java")]
    sources.append(str(java_dir / "MainActivity.java"))
    run([
        str(jdk / "bin" / "javac"),
        "-source", "8", "-target", "8",
        "-classpath", str(android_jar),
        "-d", str(classes_dir),
        *sources,
    ], env=env)

    # 4. dex
    log("Converting to DEX (d8)...")
    dex_dir = work_dir / "dex"
    dex_dir.mkdir(exist_ok=True)
    class_files = [str(p) for p in classes_dir.rglob("*.class")]
    run([
        str(d8), "--lib", str(android_jar),
        "--min-api", str(args.min_sdk),
        "--output", str(dex_dir),
        *class_files,
    ], env=env)

    # 5. package dex into apk
    log("Packaging APK...")
    unsigned = work_dir / "unsigned.apk"
    shutil.copy2(work_dir / "base.apk", unsigned)
    run(["zip", "-q", "-j", str(unsigned), str(dex_dir / "classes.dex")])

    # 6. align
    aligned = work_dir / "aligned.apk"
    run([str(zipalign), "-f", "-p", "4", str(unsigned), str(aligned)])

    # 7. sign
    log("Signing APK...")
    keystore = Path(args.keystore) if args.keystore else \
        Path.home() / ".html2apk" / "debug.keystore"
    if not keystore.exists():
        keystore.parent.mkdir(parents=True, exist_ok=True)
        run([
            str(jdk / "bin" / "keytool"), "-genkeypair",
            "-keystore", str(keystore),
            "-storepass", args.store_pass,
            "-keypass", args.key_pass,
            "-alias", args.key_alias,
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=html2apk Debug,O=html2apk,C=US",
        ], env=env)

    out_apk = Path(args.output).resolve() if args.output else \
        Path.cwd() / f"{slugify(app_name)}.apk"
    out_apk.parent.mkdir(parents=True, exist_ok=True)
    run([
        str(apksigner), "sign",
        "--ks", str(keystore),
        "--ks-key-alias", args.key_alias,
        "--ks-pass", f"pass:{args.store_pass}",
        "--key-pass", f"pass:{args.key_pass}",
        "--out", str(out_apk),
        str(aligned),
    ], env=env)

    return out_apk, work_dir


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_setup(args):
    sdk = Path(args.sdk) if args.sdk else find_sdk()
    ensure_sdk(sdk)
    print(f"SDK ready at {sdk}")


def cmd_build(args):
    sdk = Path(args.sdk) if args.sdk else find_sdk()
    ensure_sdk(sdk, yes=args.yes)
    jdk = pick_jdk()
    out, work_dir = build_apk(args, sdk, jdk)
    size = out.stat().st_size / 1024
    log(f"Done: {out} ({size:.0f} KB)")
    if not args.work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)
    print(str(out))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="html2apk",
        description="Wrap an HTML/CSS/JS site into a signed Android APK.")
    parser.add_argument("--version", action="version", version="html2apk 1.0")
    sub = parser.add_subparsers(dest="command")

    p_setup = sub.add_parser("setup", help="Download/install the Android SDK")
    p_setup.add_argument("--sdk", help="Android SDK path (default: auto)")
    p_setup.set_defaults(func=cmd_setup)

    p = sub.add_parser("build", help="Build an APK from HTML")
    p.add_argument("--sdk", help="Android SDK path (default: auto)")
    p.add_argument("input", help="HTML file or directory containing the site")
    p.add_argument("-o", "--output", help="Output APK path")
    p.add_argument("-n", "--name", help="App display name")
    p.add_argument("-l", "--logo", help="Logo image (png/jpg/svg). "
                                        "Default: first letter of the name")
    p.add_argument("--entry", default="index.html",
                   help="Entry HTML file (default: index.html)")
    p.add_argument("--package", help="Android package id (default: com.html2apk.<name>)")
    p.add_argument("--icon-bg", help="Hex background color for the icon")
    p.add_argument("--version-name", default="1.0")
    p.add_argument("--version-code", type=int, default=1)
    p.add_argument("--min-sdk", type=int, default=24)
    p.add_argument("--keystore", help="Custom keystore path")
    p.add_argument("--key-alias", default="html2apk")
    p.add_argument("--store-pass", default="android")
    p.add_argument("--key-pass", default="android")
    p.add_argument("--work-dir", help="Keep intermediate build files here")
    p.add_argument("--yes", action="store_true", help="Assume yes for SDK install")
    p.set_defaults(func=cmd_build)

    # Allow `html2apk <input>` shorthand for `html2apk build <input>`
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] not in ("setup", "build", "-h", "--help", "--version"):
        argv = ["build"] + argv

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
