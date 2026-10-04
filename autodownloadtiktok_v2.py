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
import subprocess
import sys
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization


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

    key = input("Tempel License Key (kosong = batal): ").strip()
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
    print("    Pilih menu 1 untuk aktivasi.\n")
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
    value = input(f"{prompt}{suffix}: ").strip()
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
        choice = input("Pilih nomor / path (kosong = batal): ").strip()
    else:
        print(f"\nTidak ada links_*.csv di {output_dir}")
        choice = input("Ketik path CSV (kosong = batal): ").strip()

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
                    text = f"{percent:5.1f}% | {speed} | {size}"
                    print("\r  " + text.ljust(60), end="", flush=True)
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

def main():
    print("=" * 56)
    print(f"  {APP_NAME}")
    print("  BY : RENDI AFKAR")
    print("=" * 56)

    # Seperti versi GUI: wajib aktivasi sebelum dipakai
    info = get_license_info()

    while not info:
        print("\n[!] License belum aktif.")
        if not activate_license_flow():
            if input("\nCoba lagi? [Y/n]: ").strip().lower() == "n":
                print("Keluar.")
                return
        info = get_license_info()

    print()
    print_license_status(info)

    output_dir = DEFAULT_OUTPUT

    while True:
        print("\n" + "-" * 56)
        print(" 1. Cek license / Machine ID")
        print(" 2. Scrape profil TikTok -> CSV")
        print(" 3. Download dari CSV")
        print(" 4. Ubah folder output")
        print(" 0. Keluar")
        print("-" * 56)
        print(f"Output: {output_dir}")

        choice = input("Pilih menu: ").strip()

        if choice == "1":
            info = get_license_info()
            print()
            if info:
                print_license_status(info)
                print(f"\nMachine ID: {get_machine_id()}\n")
                print_contact()
            else:
                print("[X] License tidak aktif / expired.")
                activate_license_flow()

        elif choice == "2":
            do_scrape(output_dir)

        elif choice == "3":
            do_download(output_dir)

        elif choice == "4":
            output_dir = os.path.expanduser(ask("Folder output baru", output_dir))

        elif choice == "0":
            print("Selesai.")
            return

        else:
            print("[X] Pilihan tidak valid.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nDibatalkan.")