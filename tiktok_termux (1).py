import atexit
import base64
import csv
import glob
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization


# ============================================================
# THEME : GHOST TERMINAL
# ============================================================
# Warna  : hitam #000000 | putih #FFFFFF | aksen #00FF88
#          error #FF4444 | warning #FFD700
# Matikan warna   : NO_COLOR=1
# Matikan animasi : NO_EFFECTS=1
# Paksa warna     : FORCE_COLOR=1

try:
    import readline  # noqa: F401  (membuat prompt berwarna aman di input())
    HAS_READLINE = True
except ImportError:
    HAS_READLINE = False

USE_COLOR = bool(
    (sys.stdout.isatty() or os.environ.get("FORCE_COLOR"))
    and not os.environ.get("NO_COLOR")
)
EFFECTS = USE_COLOR and not os.environ.get("NO_EFFECTS")

SET_TERMINAL_COLORS = True   # paksa background hitam & teks putih (OSC)
BOX_W = 46                   # lebar kotak (layar lebih sempit -> otomatis mengecil)
DEFAULT_USER = "ghost"       # dipakai sebelum client memperkenalkan nama
PROMPT_SUFFIX = " > "          # tanda setelah nama client di prompt

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


WHITE = rgb(255, 255, 255)
ACCENT = rgb(0, 255, 136)
ACCENT_DIM = rgb(0, 140, 80)
ERROR = rgb(255, 68, 68)
WARN = rgb(255, 215, 0)
YELLOW = rgb(255, 215, 0)
BLUE = rgb(80, 170, 255)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# Huruf besar gaya "ANSI Shadow" (tinggi 6 baris)
FONT = {
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    "D": ["██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "F": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "██║     ", "╚═╝     "],
    "K": ["██╗  ██╗", "██║ ██╔╝", "█████╔╝ ", "██╔═██╗ ", "██║  ██╗", "╚═╝  ╚═╝"],
}


# ---------- dasar warna ----------

def c(text, *codes):
    """Bungkus teks dengan warna ANSI (otomatis mati jika bukan terminal)."""
    if not USE_COLOR or not codes:
        return text
    return "".join(codes) + text + RESET


def vlen(text):
    """Panjang teks yang terlihat (tanpa kode warna)."""
    return len(ANSI_RE.sub("", text))


def rl(text):
    """Tandai kode warna agar readline tidak salah menghitung lebar prompt."""
    if HAS_READLINE and sys.stdin.isatty() and sys.stdout.isatty():
        return ANSI_RE.sub(lambda m: "\001" + m.group(0) + "\002", text)
    return text


def P(text, code=ACCENT):
    """Prompt berwarna untuk input()."""
    return rl(c(text, BOLD, code))


USER = {"name": ""}


def user_file():
    return os.path.join(LICENSE_DIR, "user.name")


def load_user_name():
    try:
        with open(user_file(), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def save_user_name(name):
    try:
        os.makedirs(LICENSE_DIR, exist_ok=True)
        with open(user_file(), "w", encoding="utf-8") as f:
            f.write(name)
        return True
    except Exception:
        return False


def set_user(name):
    USER["name"] = name.strip()


def prompt_text():
    """Prompt = nama client (sebelum perkenalan: ghost)."""
    name = USER["name"] or DEFAULT_USER
    return c(name, BOLD, ACCENT) + c(PROMPT_SUFFIX, BOLD, YELLOW)


def term_width():
    return shutil.get_terminal_size((56, 24)).columns


def box_w():
    return max(30, min(BOX_W, term_width()))


# ---------- efek ----------

def clear_screen():
    if USE_COLOR:
        # H = kursor ke atas, 2J = hapus layar, 3J = hapus scrollback
        sys.stdout.write("\033[H\033[2J\033[3J")
        sys.stdout.flush()


def apply_terminal_theme():
    if SET_TERMINAL_COLORS and USE_COLOR and sys.stdout.isatty():
        sys.stdout.write("\033]11;#000000\a\033]10;#FFFFFF\a")
        sys.stdout.flush()
        atexit.register(reset_terminal_theme)


def reset_terminal_theme():
    try:
        sys.stdout.write("\033]111\a\033]110\a")
        sys.stdout.flush()
    except Exception:
        pass


def type_out(command, delay=0.02):
    """Efek mengetik command setelah prompt ghost@termux:~$"""
    sys.stdout.write(prompt_text())
    for ch in command:
        sys.stdout.write(ch)
        sys.stdout.flush()
        if EFFECTS:
            time.sleep(delay)
    sys.stdout.write("\n")
    sys.stdout.flush()


def bar(pct, width=20):
    pct = max(0.0, min(100.0, pct))
    filled = int(width * pct / 100)
    return c("█" * filled, ACCENT) + c("░" * (width - filled), DIM, BLUE)


def run_with_spinner(label, fn, pct=None, min_time=0.22):
    """Jalankan fn() sambil menampilkan spinner. fn -> (level, teks)."""
    holder = {}

    def worker():
        try:
            holder["r"] = fn()
        except Exception as e:
            holder["r"] = ("err", str(e))

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    start = time.time()
    i = 0
    while t.is_alive() or (EFFECTS and time.time() - start < min_time):
        if EFFECTS:
            line = c(SPINNER[i % len(SPINNER)], ACCENT) + " " + c(label + " ...", BOLD, YELLOW)
            if pct is not None:
                line += "  " + bar(pct, 12)
            sys.stdout.write("\r" + line + "\033[K")
            sys.stdout.flush()
        time.sleep(0.07)
        i += 1

    if EFFECTS:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    return holder["r"]


def format_progress(percent, speed, size):
    """Progress bar untuk satu file yang sedang didownload."""
    parts = [bar(percent, 20 if term_width() >= 60 else 12),
             c(f"{percent:5.1f}%", BOLD, YELLOW)]
    if speed != "-":
        parts.append(c(speed, BOLD, BLUE))
    if size != "-":
        parts.append(c(size, BOLD, ACCENT))
    return "  ".join(parts) + ("\033[K" if USE_COLOR else "")


# ---------- kotak ASCII ----------

def center_styled(text, inner):
    gap = max(inner - vlen(text), 0)
    left = gap // 2
    return " " * left + text + " " * (gap - left)


def left_styled(text, inner, indent=2):
    text = " " * indent + text
    return text + " " * max(inner - vlen(text), 0)


def draw_box(rows, width, double=True):
    if double:
        tl, tr, bl, br, h, v = "╔", "╗", "╚", "╝", "═", "║"
    else:
        tl, tr, bl, br, h, v = "┌", "┐", "└", "┘", "─", "│"

    def border(s):
        return c(s, ACCENT_DIM)

    inner = width - 2
    print(border(tl + h * inner + tr))
    for row in rows:
        print(border(v) + row + border(v))
    print(border(bl + h * inner + br))


def build_word(word):
    return ["".join(FONT[ch][r] for ch in word) for r in range(6)]


def paint_art(row):
    row = re.sub(r"[╔╗╚╝║═]+", lambda m: c(m.group(), ACCENT_DIM), row)
    return re.sub(r"█+", lambda m: c(m.group(), BOLD, ACCENT), row)


def show_compact_header():
    w = box_w()
    inner = w - 2
    title = APP_NAME
    if len(title) > inner - 2:
        title = title.replace(" (Termux)", "")[: inner - 2]
    draw_box([
        center_styled(c("R E N D I   A F K A R", BOLD, ACCENT), inner),
        center_styled(c(title, BOLD, YELLOW), inner),
    ], w)


def show_banner():
    """Banner besar RENDI AFKAR (saat startup)."""
    w = box_w()
    if w < BOX_W:                 # layar terlalu sempit untuk huruf besar
        show_compact_header()
        return

    inner = w - 2
    blank = " " * inner
    rows = [blank]

    for index, word in enumerate(("RENDI", "AFKAR")):
        for art in build_word(word):
            rows.append(center_styled(paint_art(art), inner))
        if index == 0:
            rows.append(blank)

    rows.append(blank)
    rows.append(center_styled(c(APP_NAME[: inner - 2], BOLD, YELLOW), inner))
    draw_box(rows, w)


def show_menu():
    w = box_w()
    inner = w - 2
    items = [
        ("1", "DOWNLOAD", "dari CSV"),
        ("2", "SCRAPE", "channel -> CSV"),
        ("3", "LICENSE", "aktivasi / cek"),
        ("4", "SETTINGS", "folder & nama"),
        ("5", "EXIT", ""),
    ]
    rows = []
    for num, name, hint in items:
        if inner < 34:
            hint = ""
        name_color = ERROR if name == "EXIT" else YELLOW
        line = (c(num + ".", BOLD, ACCENT) + " "
                + c(name.ljust(10), BOLD, name_color)
                + c(hint, BOLD, BLUE))
        rows.append(left_styled(line, inner))
    draw_box(rows, w, double=False)


def short_path(path):
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]
    limit = box_w() - 12
    if len(path) > limit:
        path = "..." + path[-(limit - 3):]
    return path


def show_home_body(output_dir):
    show_menu()
    print(c("  output: ", BOLD, YELLOW) + c(short_path(output_dir), BOLD, BLUE))
    print()


# ---------- status sistem ----------

def status_line(level, label, text):
    tag, col = {
        "ok": ("[+]", ACCENT),
        "warn": ("[!]", WARN),
        "err": ("[-]", ERROR),
    }[level]
    value_color = YELLOW if level == "ok" else col
    print(c(tag, BOLD, col) + " " + c(label.ljust(8), BOLD, BLUE)
          + c(": ", BOLD, BLUE) + c(text, BOLD, value_color))


def check_system():
    return ("ok", "ONLINE")


def check_network():
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=2).close()
        return ("ok", "CONNECTED")
    except Exception:
        return ("err", "OFFLINE")


def check_python():
    return ("ok", f"READY (v{platform.python_version()})")


def check_ytdlp():
    if get_ytdlp():
        return ("ok", "READY")
    return ("err", "MISSING (pkg install yt-dlp)")


def check_ffmpeg():
    if shutil.which("ffmpeg"):
        return ("ok", "READY")
    return ("warn", "MISSING (pkg install ffmpeg)")


def check_license():
    info = get_license_info()
    if info:
        return ("ok", "AKTIF - " + info["payload"].get("plan", "-"))
    return ("warn", "BELUM AKTIF")


def show_welcome():
    """Selamat Datang + nama client (tebal), diketik pelan."""
    segments = [("Selamat Datang", (BOLD, YELLOW))]
    if USER["name"]:
        segments.append((", ", (BOLD, YELLOW)))
        segments.append((USER["name"], (BOLD, ACCENT)))

    total = sum(len(text) for text, _ in segments)
    sys.stdout.write(" " * max((box_w() - total) // 2, 0))

    for text, codes in segments:
        if USE_COLOR:
            sys.stdout.write("".join(codes))
        for ch in text:
            sys.stdout.write(ch)
            sys.stdout.flush()
            if EFFECTS:
                time.sleep(0.03)
        if USE_COLOR:
            sys.stdout.write(RESET)

    sys.stdout.write("\n")
    sys.stdout.flush()


def boot_sequence():
    """Banner + animasi loading + status sistem. Mengembalikan status network."""
    clear_screen()
    show_banner()
    print()
    show_welcome()
    print()

    checks = [
        ("SYSTEM", check_system),
        ("NETWORK", check_network),
        ("PYTHON", check_python),
        ("YT-DLP", check_ytdlp),
        ("FFMPEG", check_ffmpeg),
        ("LICENSE", check_license),
    ]

    net = ("ok", "CONNECTED")
    for i, (label, fn) in enumerate(checks):
        level, text = run_with_spinner(label, fn, pct=i * 100 / len(checks))
        if label == "NETWORK":
            net = (level, text)
        status_line(level, label, text)

    print()
    return net


def draw_home(output_dir, net):
    """Clear screen lalu tampilkan header, status, dan menu."""
    clear_screen()
    show_compact_header()
    print()
    live = [
        ("SYSTEM", check_system()),
        ("NETWORK", net),
        ("PYTHON", check_python()),
        ("YT-DLP", check_ytdlp()),
        ("FFMPEG", check_ffmpeg()),
        ("LICENSE", check_license()),
    ]
    for label, (level, text) in live:
        status_line(level, label, text)
    print()
    show_home_body(output_dir)


# ---------- warna otomatis untuk print() lama ----------

_builtin_print = print


def _colorize(text):
    lead = len(text) - len(text.lstrip("\n"))
    body = text[lead:]

    if body.startswith("[OK]"):
        body = c("[+]", BOLD, ACCENT) + c(body[4:], BOLD, YELLOW)
    elif body.startswith("[X]"):
        body = c("[-]" + body[3:], BOLD, ERROR)
    elif body.startswith("[!]"):
        body = c(body, BOLD, WARN)
    elif re.fullmatch(r"[=\-]{10,}", body):
        body = c(body, ACCENT_DIM)
    elif body.startswith(">>"):
        body = c(body, BOLD, ACCENT)
    elif body.startswith("---") and body.endswith("---"):
        body = c(body, BOLD, ACCENT)
    elif body.startswith("License : AKTIF"):
        body = c(body, BOLD, ACCENT)
    elif body.startswith(("SELESAI", "PROSES", "SCRAPE DIMULAI", "DOWNLOAD DIMULAI")):
        body = c(body, BOLD, ACCENT)
    elif body and "\033" not in body and not body.startswith("\r"):
        m = re.match(r"^(\s*[^:\[\]]{1,18}:)(\s.*)$", body)
        if m:
            body = c(m.group(1), BOLD, BLUE) + c(m.group(2), BOLD, YELLOW)
        else:
            body = c(body, BOLD, YELLOW)

    return text[:lead] + body


def print(*args, **kwargs):  # noqa: A001 - sengaja menimpa print bawaan
    if USE_COLOR and args and isinstance(args[0], str):
        args = (_colorize(args[0]),) + args[1:]
    _builtin_print(*args, **kwargs)


# ============================================================
# APP CONFIG
# ============================================================

APP_ID = "tiktok-scraper-downloader-pro"
APP_NAME = "TikTok Scraper & Downloader Pro (Termux)"

_STORAGE_DL = os.path.expanduser("~/storage/downloads")

DEFAULT_OUTPUT = os.path.join(
    _STORAGE_DL if os.path.isdir(_STORAGE_DL) else os.path.expanduser("~"),
    "TikTokDownloader",
)

MAX_RETRY = 3


# ============================================================
# LICENSE CONFIG
# ============================================================

PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAI8COWafKSK7Jv6Vn6Ptrajn0ETOa5M5kbPnbeIH/4Vc=
-----END PUBLIC KEY-----"""

LICENSE_DIR = os.path.join(os.path.expanduser("~"), ".afkar_tiktok_license")
LICENSE_FILE = os.path.join(LICENSE_DIR, "license.key")
CLOCK_FILE = os.path.join(LICENSE_DIR, "clock.dat")
MACHINE_FILE = os.path.join(LICENSE_DIR, "machine.id")

VALID_PLANS = {"1 Hari", "1 Minggu", "1 Bulan", "1 Tahun", "Lifetime"}


CONTACT_EMAIL = "rendiafkar.tools@gmail.com"


def print_contact():
    print(f"Hubungi : {CONTACT_EMAIL}")
    print("  - Kirim Machine ID untuk meminta License Key")
    print("  - Hubungi juga untuk perpanjangan License Key")


# ============================================================
# MACHINE ID
# ============================================================

def get_machine_id():
    """
    ID acak yang disimpan sekali di file lalu di-hash.
    (uuid.getnode() tidak stabil di Android/Termux.)
    Kalau data Termux dihapus, Machine ID berubah dan license
    harus dibuat ulang.
    """
    os.makedirs(LICENSE_DIR, exist_ok=True)

    raw_id = ""
    if os.path.exists(MACHINE_FILE):
        with open(MACHINE_FILE, "r", encoding="utf-8") as f:
            raw_id = f.read().strip()

    if not raw_id:
        raw_id = secrets.token_hex(16)
        with open(MACHINE_FILE, "w", encoding="utf-8") as f:
            f.write(raw_id)

    raw = f"{raw_id}|{platform.system()}|{platform.machine()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# BASE64
# ============================================================

def b64url_encode(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode_strict(value):
    if not value:
        raise ValueError("Base64 kosong.")

    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Format Base64 license tidak valid.")

    padding = "=" * (-len(value) % 4)
    decoded = base64.urlsafe_b64decode(value + padding)

    if b64url_encode(decoded) != value:
        raise ValueError("Base64 license tidak canonical.")

    return decoded


# ============================================================
# DATETIME
# ============================================================

def parse_iso_datetime(value):
    if value is None:
        return None
    if not value:
        raise ValueError("Tanggal license kosong.")

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        raise ValueError("Format tanggal tidak valid.")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def format_license_date(dt):
    if dt is None:
        return "Lifetime"
    try:
        return dt.astimezone(timezone.utc).strftime("%d-%m-%Y %H:%M UTC")
    except Exception:
        return "Tanggal tidak valid"


def get_remaining_text(expires_at):
    if expires_at is None:
        return "TIDAK TERBATAS"

    remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()

    if remaining <= 0:
        return "EXPIRED"

    days = int(remaining // 86400)
    hours = int((remaining % 86400) // 3600)
    minutes = int((remaining % 3600) // 60)

    if days > 0:
        return f"{days} hari {hours} jam"
    if hours > 0:
        return f"{hours} jam {minutes} menit"
    return f"{minutes} menit"


# ============================================================
# CLOCK ROLLBACK PROTECTION
# ============================================================

def check_clock_rollback(now=None):
    if now is None:
        now = datetime.now(timezone.utc)

    now_ts = now.timestamp()

    try:
        os.makedirs(LICENSE_DIR, exist_ok=True)

        previous = None

        if os.path.exists(CLOCK_FILE):
            with open(CLOCK_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    previous = float(content)

        if previous is not None:
            # Mundur lebih dari 5 menit
            if now_ts < previous - 300:
                return False
            # Mundur sedikit
            if now_ts < previous:
                return True

        with open(CLOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(max(now_ts, previous or 0)))

        return True

    except Exception:
        return False


# ============================================================
# VERIFY LICENSE
# ============================================================

def verify_license_key(license_key):
    try:
        if not license_key:
            return False, "License kosong.", None

        license_key = license_key.strip()

        if not license_key.startswith("AFKAR-"):
            return False, "Prefix license tidak valid.", None

        parts = license_key[6:].split(".")

        if len(parts) != 2:
            return False, "Format license tidak valid.", None

        payload_bytes = b64url_decode_strict(parts[0])
        signature = b64url_decode_strict(parts[1])

        if len(signature) != 64:
            return False, "Signature license tidak valid.", None

        # Verifikasi signature lebih dulu
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)

        try:
            public_key.verify(signature, payload_bytes)
        except InvalidSignature:
            return False, "Signature license tidak valid.", None

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            return False, "Payload license rusak.", None

        if not isinstance(payload, dict):
            return False, "Payload license tidak valid.", None

        for field in ("app_id", "license_id", "plan",
                      "machine_id", "created_at", "expires_at"):
            if field not in payload:
                return False, f"Field license hilang: {field}", None

        if payload.get("app_id") != APP_ID:
            return False, "License bukan untuk aplikasi ini.", None

        if payload.get("machine_id") != get_machine_id():
            return False, "License bukan untuk perangkat ini.", None

        plan = payload.get("plan")

        if plan not in VALID_PLANS:
            return False, "Plan license tidak valid.", None

        try:
            created_at = parse_iso_datetime(payload.get("created_at"))
        except Exception:
            return False, "Tanggal pembuatan license tidak valid.", None

        expires_raw = payload.get("expires_at")

        if plan == "Lifetime":
            if expires_raw is not None:
                return False, "License Lifetime memiliki tanggal expired.", None
            expires_at = None
        else:
            if expires_raw is None:
                return False, "License berjangka tidak memiliki expired.", None
            try:
                expires_at = parse_iso_datetime(expires_raw)
            except Exception:
                return False, "Tanggal expired license tidak valid.", None

        now = datetime.now(timezone.utc)

        if not check_clock_rollback(now):
            return False, "Waktu perangkat terdeteksi mundur.", None

        if now < created_at:
            return False, "Waktu perangkat tidak valid.", None

        if expires_at is not None and now >= expires_at:
            return False, "License sudah expired.", None

        info = {
            "payload": payload,
            "created_at": created_at,
            "expires_at": expires_at,
        }

        return True, "License valid.", info

    except Exception as e:
        return False, f"License tidak valid: {e}", None


# ============================================================
# LICENSE FILE
# ============================================================

def save_license_key(license_key):
    try:
        os.makedirs(LICENSE_DIR, exist_ok=True)
        with open(LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(license_key.strip())
        return True
    except Exception:
        return False


def load_saved_license():
    try:
        if not os.path.exists(LICENSE_FILE):
            return ""
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def get_license_info():
    key = load_saved_license()
    if not key:
        return None
    valid, _message, info = verify_license_key(key)
    return info if valid else None


def print_license_status(info):
    payload = info["payload"]
    print(
        f"LICENSE : {payload.get('plan', '-')}"
        f" | ID : {payload.get('license_id', '-')}"
        f" | SISA : {get_remaining_text(info['expires_at'])}"
    )
    print(f"Expired : {format_license_date(info['expires_at'])}")


def activate_license_flow():
    print("\n--- AKTIVASI LICENSE ---")
    print("Machine ID perangkat ini:\n")
    print(get_machine_id())
    print()
    print_contact()
    print()

    key = input(P("Tempel License Key (kosong = batal): ")).strip()
    if not key:
        return False

    valid, message, info = verify_license_key(key)
    if not valid:
        print(f"\n[X] License ditolak: {message}")
        return False

    if not save_license_key(key):
        print("\n[X] License valid, tetapi gagal menyimpan.")
        return False

    print("\n[OK] License berhasil diaktifkan.")
    print_license_status(info)
    return True


def require_license():
    """Dipanggil sebelum scrape/download (cek expired tiap kali)."""
    if get_license_info():
        return True

    print("\n[X] License belum aktif atau sudah expired.")
    print("    Pilih menu 3 (LICENSE) untuk aktivasi.\n")
    print_contact()
    return False


# ============================================================
# HELPERS
# ============================================================

def get_ytdlp():
    return shutil.which("yt-dlp")


def check_tools():
    if not get_ytdlp():
        print("\n[X] yt-dlp tidak ditemukan.")
        print("    Install dengan: pkg install yt-dlp")
        print("    atau          : pip install -U yt-dlp")
        return False

    if not shutil.which("ffmpeg"):
        print("\n[!] ffmpeg tidak ditemukan. Video+audio tidak bisa digabung.")
        print("    Install dengan: pkg install ffmpeg")

    return True


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    value = input(P(f"{prompt}{suffix}: ")).strip()
    return value or default


def clean_username(value):
    return re.sub(r"[^a-zA-Z0-9_.]", "", value.replace("@", "").strip())


def parse_range(start_value, end_value, total):
    s_val = start_value.strip()
    e_val = end_value.strip()

    start = int(s_val) if s_val.isdigit() else 1
    end = int(e_val) if e_val.isdigit() else total

    if start < 1:
        start = 1
    if end > total:
        end = total

    if start > end:
        raise ValueError("Range tidak valid / melebihi jumlah video.")

    return start, end


def parse_progress_line(line):
    percent = None
    speed = "-"
    size = "-"

    m = re.search(r"(\d+(?:\.\d+)?)%", line)
    if m:
        try:
            percent = float(m.group(1))
        except Exception:
            percent = None

    m = re.search(r"\bat\s+([0-9.]+\s*[KMG]?i?B/s)", line, re.IGNORECASE)
    if m:
        speed = m.group(1)

    m = re.search(r"\bof\s+([0-9.]+\s*[KMG]?i?B)", line, re.IGNORECASE)
    if m:
        size = m.group(1)

    return percent, speed, size


def stop_process(proc):
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ============================================================
# SCRAPE
# ============================================================

def do_scrape(output_dir):
    if not require_license() or not check_tools():
        return

    username = clean_username(ask("Username TikTok (kosong = batal)"))
    if not username:
        return

    s_val = ask("Scrape dari nomor", "1")
    e_val = ask("Sampai nomor (kosong = semua)", "")

    print(f"\nMengumpulkan data dari @{username}")
    print("(Mohon tunggu, banyak video = lama. Ctrl+C untuk berhenti)\n")

    cmd = [
        get_ytdlp(),
        "--flat-playlist",
        "--print",
        "%(url)s||%(description)s||%(title)s",
        f"https://www.tiktok.com/@{username}",
    ]

    proc = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        out, _ = proc.communicate()

    except KeyboardInterrupt:
        stop_process(proc)
        print("\n[!] Scrape dibatalkan.")
        return

    except Exception as e:
        print(f"[X] Error saat menjalankan yt-dlp: {e}")
        return

    if proc.returncode != 0:
        print("[X] Scrape gagal. Pastikan username benar "
              "dan yt-dlp sudah versi terbaru (pip install -U yt-dlp).")
        return

    links = []
    titles = []

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split("||")

        if not parts[0].startswith("http"):
            continue

        desc = parts[1].strip() if len(parts) > 1 else ""
        title = parts[2].strip() if len(parts) > 2 else ""

        if desc.upper() == "NA":
            desc = ""
        if title.upper() == "NA":
            title = ""

        full_title = desc or title or f"tiktok_video_{len(links) + 1}"

        links.append(parts[0])
        titles.append(full_title)

    if not links:
        print("[X] Tidak ada video ditemukan di profil ini.")
        return

    try:
        start, end = parse_range(s_val, e_val, len(links))
    except ValueError as e:
        print(f"[X] {e}")
        return

    links = links[start - 1:end]
    titles = titles[start - 1:end]

    os.makedirs(output_dir, exist_ok=True)

    links_file = os.path.join(output_dir, f"links_{username}.csv")
    titles_file = os.path.join(output_dir, f"titles_{username}.csv")

    try:
        with open(links_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for link in links:
                writer.writerow([link])

        with open(titles_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for title in titles:
                writer.writerow([title])

    except Exception as e:
        print(f"[X] Gagal membuat CSV: {e}")
        return

    print(f"[OK] Scrape selesai: {len(links)} video siap didownload!")
    print(f"Links  : {links_file}")
    print(f"Titles : {titles_file}")


# ============================================================
# DOWNLOAD
# ============================================================

def choose_csv(output_dir):
    files = sorted(glob.glob(os.path.join(output_dir, "links_*.csv")))

    if files:
        print("\nCSV links yang tersedia:")
        for i, path in enumerate(files, start=1):
            print(f"  {i}. {os.path.basename(path)}")
        print("  (atau ketik path CSV lengkap)")
        choice = input(P("Pilih nomor / path (kosong = batal): ")).strip()
    else:
        print(f"\nTidak ada links_*.csv di {output_dir}")
        choice = input(P("Ketik path CSV (kosong = batal): ")).strip()

    if not choice:
        return None

    if choice.isdigit() and files:
        idx = int(choice) - 1
        if 0 <= idx < len(files):
            return files[idx]
        print("[X] Nomor tidak valid.")
        return None

    path = os.path.expanduser(choice)
    if os.path.isfile(path):
        return path

    print("[X] File tidak ditemukan.")
    return None


def build_download_cmd(number, outdir, url):
    return [
        get_ytdlp(),
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--extractor-args", "tiktok:watermark=0",
        "--windows-filenames",
        "--trim-filenames", "100",
        "--retries", "5",
        "--fragment-retries", "5",
        "--retry-sleep", "http:2",
        "--retry-sleep", "fragment:2",
        "--socket-timeout", "60",
        "--continue",
        "--progress",
        "--newline",
        "-o", os.path.join(outdir, f"{number:03d}_%(title)s_[%(id)s].%(ext)s"),
        url,
    ]


def download_one(number, total_label, outdir, url):
    """Return True kalau sukses. Raise KeyboardInterrupt kalau dihentikan."""
    last_error = ""

    for attempt in range(1, MAX_RETRY + 1):
        if attempt == 1:
            print(f"\n>> [{number}/{total_label}] Sedang mendownload...")
        else:
            print(f"\n>> [{number}/{total_label}] Retry {attempt}/{MAX_RETRY}...")

        proc = None
        in_progress_line = False

        try:
            proc = subprocess.Popen(
                build_download_cmd(number, outdir, url),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1,
            )

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                percent, speed, size = parse_progress_line(line)

                if percent is not None and line.startswith("[download]"):
                    print("\r  " + format_progress(percent, speed, size),
                          end="", flush=True)
                    in_progress_line = True

                if "ERROR:" in line or "error:" in line:
                    last_error = line

            if in_progress_line:
                print()

            return_code = proc.wait()

        except KeyboardInterrupt:
            stop_process(proc)
            raise

        except Exception as ex:
            last_error = str(ex)
            print(f"[!] Error sistem: {last_error}")
            continue

        finally:
            stop_process(proc)

        if return_code == 0:
            print(f"[OK] [{number}/{total_label}] SUKSES")
            return True

        if not last_error:
            last_error = "Tidak ada detail error."

        if attempt < MAX_RETRY:
            print(f"[!] [{number}/{total_label}] Gagal, mencoba lagi...")
        else:
            print(f"[X] [{number}/{total_label}] GAGAL setelah {MAX_RETRY} percobaan")
            print(f"    -> {last_error}")

    return False


def do_download(output_dir):
    if not require_license() or not check_tools():
        return

    csv_file = choose_csv(output_dir)
    if not csv_file:
        return

    try:
        with open(csv_file, encoding="utf-8-sig", newline="") as f:
            links = [
                row[0].strip()
                for row in csv.reader(f)
                if row and row[0].strip().startswith("http")
            ]
    except Exception as e:
        print(f"[X] Gagal membaca CSV: {e}")
        return

    if not links:
        print("[X] Tidak ada link di dalam file CSV.")
        return

    print(f"\nTotal link di CSV: {len(links)}")

    try:
        start, end = parse_range(
            ask("Download dari nomor", "1"),
            ask("Sampai nomor (kosong = semua)", ""),
            len(links),
        )
    except ValueError as e:
        print(f"[X] {e}")
        return

    selected = links[start - 1:end]
    total = len(selected)

    name = os.path.splitext(os.path.basename(csv_file))[0]
    user = re.sub(r"^links_", "", name)
    user = re.sub(r"[^a-zA-Z0-9_.-]", "", user) or "tiktok"

    outdir = os.path.join(output_dir, user)
    os.makedirs(outdir, exist_ok=True)

    print(f"\nMemulai download {total} video...  (Ctrl+C untuk berhenti)")
    print(f"Output: {outdir}")

    success = 0
    failed = 0

    try:
        for offset, url in enumerate(selected):
            number = start + offset

            if download_one(number, end, outdir, url):
                success += 1
            else:
                failed += 1

            done = offset + 1
            print(c("TOTAL ", BOLD, YELLOW) + bar(done * 100 / total)
                  + c(f"  {done}/{total}", BOLD, BLUE))

    except KeyboardInterrupt:
        print("\n[!] Download dihentikan.")

    print("\n" + "=" * 50)
    print("PROSES DOWNLOAD SELESAI")
    print(f"Berhasil : {success}")
    print(f"Gagal    : {failed}")
    print(f"Output   : {outdir}")
    print("=" * 50)


# ============================================================
# MAIN
# ============================================================

def pause():
    print()
    input(P("Tekan Enter untuk kembali ke menu... ", YELLOW))


def run_command(text):
    clear_screen()
    type_out(text, delay=0.012)
    print()


def license_menu():
    info = get_license_info()
    print()
    if info:
        print_license_status(info)
        print(f"\nMachine ID: {get_machine_id()}\n")
        print_contact()
    else:
        print("[X] License tidak aktif / expired.")
        activate_license_flow()


def introduce_user():
    """Perkenalan nama setelah sesi lisensi. Nama ini jadi prompt client."""
    print()
    type_out("./whoami --setup")
    print()
    print("[OK] Sesi lisensi selesai.")
    print("Sebelum mulai, perkenalkan dirimu dulu.\n")

    while True:
        name = input(P("Nama kamu: ")).strip()[:20]
        if name:
            break
        print("[!] Nama tidak boleh kosong.")

    set_user(name)
    if not save_user_name(name):
        print("[!] Nama tidak bisa disimpan, akan ditanya lagi di sesi berikutnya.")

    print()
    print(f"[OK] Halo, {name}! Selamat datang.")
    if EFFECTS:
        time.sleep(0.9)


def do_settings(output_dir):
    print(f"Output saat ini : {output_dir}")
    print(f"Nama saat ini   : {USER['name'] or '-'}\n")

    new_dir = os.path.expanduser(ask("Folder output baru", output_dir))

    new_name = ask("Nama", USER["name"])[:20].strip()
    if new_name and new_name != USER["name"]:
        set_user(new_name)
        save_user_name(new_name)

    print(f"\n[OK] Output : {new_dir}")
    print(f"[OK] Nama   : {USER['name'] or '-'}")
    return new_dir


def main():
    output_dir = DEFAULT_OUTPUT

    saved_name = load_user_name()
    if saved_name:
        set_user(saved_name)

    apply_terminal_theme()
    net = boot_sequence()

    redraw = False

    # Wajib aktivasi sebelum dipakai
    info = get_license_info()
    while not info:
        print("[!] License belum aktif.")
        if not activate_license_flow():
            if input(P("Coba lagi? [Y/n]: ")).strip().lower() == "n":
                print("Keluar.")
                return
        info = get_license_info()
        redraw = True

    if redraw:
        pause()

    if not USER["name"]:
        introduce_user()
        redraw = True

    if not redraw:
        show_home_body(output_dir)

    while True:
        if redraw:
            draw_home(output_dir, net)
        redraw = True

        choice = input(rl(prompt_text())).strip().lower()

        if choice in ("1", "download"):
            run_command("./download --csv")
            do_download(output_dir)
            pause()

        elif choice in ("2", "scrape"):
            run_command("./scrape --tiktok")
            do_scrape(output_dir)
            pause()

        elif choice in ("3", "license"):
            run_command("./license --status")
            license_menu()
            pause()

        elif choice in ("4", "settings"):
            run_command("./settings")
            output_dir = do_settings(output_dir)
            pause()

        elif choice in ("5", "0", "q", "exit", "keluar"):
            print()
            print("[OK] Sesi ditutup. Sampai jumpa.")
            return

        else:
            print("[X] Pilihan tidak valid.")
            time.sleep(0.8)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nDibatalkan.")
