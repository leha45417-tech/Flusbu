import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "Rufus 4.6 + Ventoy Engineering Station (Multi-Lang Dark)"
APP_GEOMETRY = "500x785"
DEFAULT_VENTOY_PATH = r"D:\ventoy-1.1.18"
DEFAULT_LEHA_PATH = r"D:\by leha"


class ProcessRunner:
    """Low-level system command runner without console windows."""

    @staticmethod
    def is_admin():
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    @staticmethod
    def run_ps(command):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return res.stdout.strip(), res.stderr.strip(), res.returncode

    @staticmethod
    def run_cmd(args, cwd=None, encoding="cp866"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        res = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="ignore",
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return res.stdout, res.stderr, res.returncode

    @staticmethod
    def run_diskpart(lines, temp_filename="dp_station_script.txt"):
        temp_path = os.path.join(os.environ.get("TEMP", "."), temp_filename)
        try:
            with open(temp_path, "w", encoding="ascii") as f:
                f.write("\n".join(lines) + "\n")
            out, err, code = ProcessRunner.run_cmd(["diskpart", "/s", temp_path])
            return code == 0, out
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
class StorageUtils:
    """Disk, drive letter, cache flushing and size calculation helpers."""

    @staticmethod
    def get_dir_size(path):
        total = 0
        for root_dir, _, filenames in os.walk(path):
            for fname in filenames:
                full_p = os.path.join(root_dir, fname)
                if not os.path.islink(full_p):
                    try:
                        total += os.path.getsize(full_p)
                    except OSError:
                        pass
        return total

    @staticmethod
    def find_free_letter(preferred="W"):
        occupied = {f"{d}:" for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{d}:\\")}
        if f"{preferred}:" not in occupied:
            return preferred
        for letter in "WXVUTSRQPONMLKJIHGFED":
            if f"{letter}:" not in occupied:
                return letter
        return preferred

    @staticmethod
    def format_eta(seconds):
        if seconds < 0 or seconds > 86400:
            return "--:--"
        mins, sec = divmod(int(seconds), 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours:02d}:{mins:02d}:{sec:02d}"
        return f"{mins:02d}:{sec:02d}"

    @staticmethod
    def get_physical_disks():
        cmd = (
            "Get-Disk | Select Number,FriendlyName,BusType,PartitionStyle,SerialNumber,"
            "@{N='GB';E={[math]::Round($_.Size/1GB,2)}} | ConvertTo-Json"
        )
        out, _, _ = ProcessRunner.run_ps(cmd)
        if not out:
            return []
        data = json.loads(out)
        return [data] if isinstance(data, dict) else data

    @staticmethod
    def get_partition_letter(disk_number):
        cmd = (
            f"(Get-Partition -DiskNumber {disk_number} | "
            f"Where-Object {{$_.DriveLetter}} | Select-Object -First 1).DriveLetter"
        )
        let, _, _ = ProcessRunner.run_ps(cmd)
        return let.strip()

    @staticmethod
    def eject_volume(letter):
        cmd = (
            f"$shell = New-Object -ComObject Shell.Application; "
            f"$shell.Namespace(17).ParseName('{letter}:').InvokeVerb('Eject')"
        )
        ProcessRunner.run_ps(cmd)

    @staticmethod
    def close_explorer_windows_for_drives(letters):
        targets = ",".join([f"'{l.upper()}:\\'" for l in letters if l])
        cmd = (
            f"$targets = @({targets}); "
            "$shell = New-Object -ComObject Shell.Application; "
            "foreach ($w in @($shell.Windows())) { "
            "try { $path = $w.Document.Folder.Self.Path; "
            "foreach ($t in $targets) { if ($path -like \"$t*\") { $w.Quit() } } } catch {} }"
        )
        ProcessRunner.run_ps(cmd)

    @staticmethod
    def flush_volume_cache(letter):
        ProcessRunner.run_ps(f"Write-VolumeCache -DriveLetter '{letter}' -ErrorAction SilentlyContinue")
class EngineeringTools:
    """Backup tools for Ethernet/Apps/Drivers and ISO inspection."""

    @staticmethod
    def inspect_windows_iso(iso_path):
        editions = []
        vol_id, _, _ = ProcessRunner.run_ps(
            f"(Mount-DiskImage -ImagePath '{iso_path}' -NoDriveLetter -PassThru | Get-Volume).UniqueId"
        )
        iso_vol = vol_id.rstrip("\\")
        if not iso_vol:
            return editions

        try:
            wim_file = ""
            for candidate in ["install.wim", "install.esd", "install.swm"]:
                p = os.path.join(iso_vol, "sources", candidate)
                if os.path.exists(p):
                    wim_file = p
                    break
            if wim_file:
                out, _, _ = ProcessRunner.run_cmd(["dism", "/Get-WimInfo", f"/WimFile:{wim_file}"])
                for line in out.splitlines():
                    if ":" in line and ("Имя" in line or "Name" in line):
                        ed_name = line.split(":", 1).strip()
                        if ed_name and ed_name not in editions:
                            editions.append(ed_name)
        finally:
            ProcessRunner.run_ps(f"Dismount-DiskImage -ImagePath '{iso_path}'")
        return editions

    @staticmethod
    def backup_ethernet_and_system(target_folder):
        os.makedirs(target_folder, exist_ok=True)
        report_file = os.path.join(target_folder, "PC_Network_And_Apps_Backup.txt")

        ip_out, _, _ = ProcessRunner.run_cmd(["ipconfig", "/all"])
        route_out, _, _ = ProcessRunner.run_cmd(["route", "print", "-4"])
        apps_cmd = (
            "Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*,"
            "HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* | "
            "Where-Object {$_.DisplayName} | Select-Object DisplayName, DisplayVersion | "
            "Sort-Object DisplayName | Format-Table -AutoSize | Out-String -Width 200"
        )
        apps_out, _, _ = ProcessRunner.run_ps(apps_cmd)

        sections = [
            f"PC & ETHERNET NETWORK REPORT ({time.strftime('%Y-%m-%d %H:%M:%S')})",
            "=" * 75,
            f"Computer Name : {platform.node()}",
            f"OS Build      : {platform.platform()}",
            f"Processor     : {platform.processor()}",
            "",
            "--- ETHERNET NETWORK SETTINGS (IPCONFIG /ALL) ---",
            ip_out.strip(),
            "",
            "--- IPV4 ROUTING TABLE ---",
            route_out.strip(),
            "",
            "--- INSTALLED APPLICATIONS LIST ---",
            apps_out.strip(),
        ]
        with open(report_file, "w", encoding="utf-8") as f:
            f.write("\n".join(sections))
        return report_file

    @staticmethod
    def export_pc_drivers(target_folder):
        drv_dir = os.path.join(target_folder, "Drivers_Backup")
        os.makedirs(drv_dir, exist_ok=True)
        ProcessRunner.run_cmd(["dism", "/online", "/export-driver", f"/destination:{drv_dir}"])
        return drv_dir

    @staticmethod
    def build_unattend_xml():
        ps_script = (
            'powershell -NoProfile -ExecutionPolicy Bypass -Command "'
            "$desk = [Environment]::GetFolderPath('Desktop'); "
            "$src = Get-PSDrive -PSProvider FileSystem | ForEach-Object { Join-Path $_.Root '_leha_data\\от лехи' } | Where-Object { Test-Path $_ } | Select-Object -First 1; "
            "if ($src) { Copy-Item -Path $src -Destination $desk -Recurse -Force }"
            '"'
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://microsoft.com">\n'
            '  <settings pass="oobeSystem">\n'
            '    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">\n'
            '      <FirstLogonCommands>\n'
            '        <SynchronousCommand wcm:action="add">\n'
            '          <Order>1</Order>\n'
            f'          <CommandLine>{ps_script}</CommandLine>\n'
            '          <Description>Copy Leha Folder to User Desktop</Description>\n'
            '        </SynchronousCommand>\n'
            '      </FirstLogonCommands>\n'
            '    </component>\n'
            '  </settings>\n'
            '</unattend>\n'
        )
LANG_PACK = {
    "English": {
        "lang_lbl": "Language:",
        "sec_mode": "Operation Mode & Integration",
        "rad_rufus": "Rufus (Direct Write + AutoFolder)",
        "rad_ventoy": "Ventoy (Multiboot)",
        "btn_vdir": "Ventoy Dir",
        "sec_dev": "Device",
        "lbl_boot": "Boot selection",
        "boot_rufus": "Disk or ISO image (Please select)",
        "boot_ventoy": "Select one or multiple images for Ventoy",
        "btn_select": "SELECT",
        "no_iso": "No image selected",
        "no_ventoy_iso": "No Ventoy images selected (will ask on START)",
        "lbl_opt": "Image option",
        "opt_std": "Standard Windows installation",
        "opt_wtg": "Windows To Go",
        "lbl_scheme": "Partition scheme",
        "lbl_target": "Target system",
        "sec_fmt": "Format Options & Utilities",
        "lbl_vol": "Volume label",
        "lbl_fs": "File system",
        "lbl_cluster": "Cluster size",
        "chk_bad": "Check device for bad blocks before writing",
        "sec_status": "Status & Telemetry",
        "ready": "READY",
        "btn_log": "Log",
        "btn_bench": "USB Test",
        "btn_eject": "Eject",
        "btn_close": "Close",
        "btn_start": "START",
        "dlg_bkp_title": "Backup Options",
        "dlg_bkp_head": "Which backups to save into 'от лехи' folder?",
        "dlg_bkp_net": "Backup Ethernet network (IP, DNS, MAC) & installed apps",
        "dlg_bkp_drv": "Backup all PC drivers (via DISM)",
        "dlg_cont": "Continue",
        "dlg_cancel": "Cancel",
        "warn_dev": "Please select a target device!",
        "warn_iso": "Please select an ISO image to write!",
        "warn_vexe": "Ventoy2Disk.exe not found! Please select Ventoy folder.",
        "err_space": "Not enough free space on the selected USB drive!",
        "ask_v_iso": "Which images do you want to place on Ventoy USB?\n\n• YES — select one or multiple images (.iso / .wim)\n• NO — install Ventoy bootloader only\n• CANCEL — abort",
        "warn_non_usb": "WARNING: Selected drive is NOT a USB flash drive!\nAre you sure you want to wipe it?",
        "warn_wipe": "ALL DATA ON THIS DEVICE WILL BE DESTROYED.\n\nClick OK to continue.",
        "done_rufus": "Bootable USB drive is ready and verified!\nFolder 'от лехи' has been embedded.",
        "done_ventoy": "Ventoy multiboot USB drive created successfully!",
    },
    "Русский": {
        "lang_lbl": "Язык:",
        "sec_mode": "Режим работы и интеграция",
        "rad_rufus": "Rufus (Прямая запись + автопапка)",
        "rad_ventoy": "Ventoy (Мультизагрузка)",
        "btn_vdir": "Папка Ventoy",
        "sec_dev": "Устройство",
        "lbl_boot": "Метод загрузки",
        "boot_rufus": "Диск или ISO-образ (Выберите)",
        "boot_ventoy": "Выберите один или несколько образов для Ventoy",
        "btn_select": "ВЫБРАТЬ",
        "no_iso": "Образ не выбран",
        "no_ventoy_iso": "Образы для Ventoy не выбраны (спросит при старте)",
        "lbl_opt": "Параметры образа",
        "opt_std": "Стандартная установка Windows",
        "opt_wtg": "Windows To Go (Запуск с USB)",
        "lbl_scheme": "Схема раздела",
        "lbl_target": "Целевая система",
        "sec_fmt": "Параметры форматирования и утилиты",
        "lbl_vol": "Новая метка тома",
        "lbl_fs": "Файловая система",
        "lbl_cluster": "Размер кластера",
        "chk_bad": "Проверить флешку на битые сектора перед записью",
        "sec_status": "Статус и телеметрия",
        "ready": "ГОТОВ К РАБОТЕ",
        "btn_log": "Журнал",
        "btn_bench": "Тест USB",
        "btn_eject": "Извлечь",
        "btn_close": "Закрыть",
        "btn_start": "СТАРТ",
        "dlg_bkp_title": "Выбор резервного копирования",
        "dlg_bkp_head": "Какие бэкапы сохранить в папку 'от лехи'?",
        "dlg_bkp_net": "Бэкап сети Ethernet (IP, DNS, MAC) и списка программ",
        "dlg_bkp_drv": "Бэкап всех драйверов этого ПК (через DISM)",
        "dlg_cont": "Продолжить",
        "dlg_cancel": "Отмена",
        "warn_dev": "Выберите целевое устройство!",
        "warn_iso": "Выберите ISO-образ для записи!",
        "warn_vexe": "Не найден Ventoy2Disk.exe! Укажите папку с Ventoy.",
        "err_space": "Недостаточно свободного места на выбранной флешке!",
        "ask_v_iso": "Какие образы вам нужно поместить на флешку Ventoy?\n\n• ДА — выбрать один или несколько образов (.iso / .wim)\n• НЕТ — установить только пустой загрузчик Ventoy\n• ОТМЕНА — прервать запуск",
        "warn_non_usb": "ВНИМАНИЕ: Выбранный диск НЕ является USB-флешкой!\nТочно стереть его?",
        "warn_wipe": "ВСЕ ДАННЫЕ НА НАКОПИТЕЛЕ БУДУТ УНИЧТОЖЕНЫ.\n\nНажмите ОК для продолжения.",
        "done_rufus": "Загрузочная флешка полностью готова и проверена!\nПапка 'от лехи' вшита в установщик.",
        "done_ventoy": "Мультизагрузочная флешка Ventoy успешно создана!",
    },
    "Українська": {
        "lang_lbl": "Мова:",
        "sec_mode": "Режим роботи та інтеграція",
        "rad_rufus": "Rufus (Прямий запис + автопапка)",
        "rad_ventoy": "Ventoy (Мультизавантаження)",
        "btn_vdir": "Папка Ventoy",
        "sec_dev": "Пристрій",
        "lbl_boot": "Метод завантаження",
        "boot_rufus": "Диск або ISO-образ (Виберіть)",
        "boot_ventoy": "Виберіть один або декілька образів для Ventoy",
        "btn_select": "ВИБРАТИ",
        "no_iso": "Образ не вибрано",
        "no_ventoy_iso": "Образи для Ventoy не вибрано (запитає при старті)",
        "lbl_opt": "Параметри образу",
        "opt_std": "Стандартне встановлення Windows",
        "opt_wtg": "Windows To Go (Запуск з USB)",
        "lbl_scheme": "Схема розділу",
        "lbl_target": "Цільова система",
        "sec_fmt": "Параметри форматування та утиліти",
        "lbl_vol": "Нова мітка тому",
        "lbl_fs": "Файлова система",
        "lbl_cluster": "Розмір кластера",
        "chk_bad": "Перевірити флешку на биті сектори перед записом",
        "sec_status": "Статус і телеметрія",
        "ready": "ГОТОВИЙ ДО РОБОТИ",
        "btn_log": "Журнал",
        "btn_bench": "Тест USB",
        "btn_eject": "Витягти",
        "btn_close": "Закрити",
        "btn_start": "СТАРТ",
        "dlg_bkp_title": "Вибір резервного копіювання",
        "dlg_bkp_head": "Які бекапи зберегти в папку 'от лехи'?",
        "dlg_bkp_net": "Бекап мережі Ethernet (IP, DNS, MAC) та списку програм",
        "dlg_bkp_drv": "Бекап усіх драйверів цього ПК (через DISM)",
        "dlg_cont": "Продовжити",
        "dlg_cancel": "Скасувати",
        "warn_dev": "Виберіть цільовий пристрій!",
        "warn_iso": "Виберіть ISO-образ для запису!",
        "warn_vexe": "Не знайдено Ventoy2Disk.exe! Вкажіть папку з Ventoy.",
        "err_space": "Недостатньо вільного місця на вибраній флешці!",
        "ask_v_iso": "Які образи потрібно помістити на флешку Ventoy?\n\n• ТАК — вибрати один або декілька образів (.iso / .wim)\n• НІ — встановити лише порожній завантажувач Ventoy\n• СКАСУВАТИ — перервати запуск",
        "warn_non_usb": "УВАГА: Вибраний диск НЕ є USB-флешкою!\nТочно стерти його?",
        "warn_wipe": "УСІ ДАНІ НА НАКОПИЧУВАЧІ БУДУТЬ ЗНИЩЕНІ.\n\nНатисніть ОК для продовження.",
        "done_rufus": "Завантажувальна флешка повністю готова та перевірена!\nПапку 'от лехи' вшито в інсталятор.",
        "done_ventoy": "Мультизавантажувальну флешку Ventoy успішно створено!",
    },
}
class RufusVentoyEngineeringApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(APP_GEOMETRY)
        self.root.resizable(False, False)

        self.bg_main = "#181818"
        self.bg_input = "#28282b"
        self.bg_btn = "#35353a"
        self.bg_btn_hover = "#48484f"
        self.fg_text = "#eaeaea"
        self.fg_muted = "#9a9a9a"
        self.accent_blue = "#3794ff"
        self.accent_green = "#4ec9b0"
        self.accent_orange = "#ce9178"
        self.accent_red = "#f44747"

        self.root.configure(bg=self.bg_main)
        self.configure_dark_theme()

        if not ProcessRunner.is_admin():
            messagebox.showerror(
                "Administrator Rights Required",
                "Please run this application as Administrator to partition drives and write bootloaders!",
            )
            sys.exit(1)

        self.current_lang = "English"
        self.disks_data = []
        self.ventoy_dir = DEFAULT_VENTOY_PATH
        self.leha_source_dir = DEFAULT_LEHA_PATH
        self.mode_var = tk.StringVar(value="rufus")
        self.lang_var = tk.StringVar(value="English")
        self.backup_net_var = tk.BooleanVar(value=True)
        self.backup_drv_var = tk.BooleanVar(value=False)
        self.bad_blocks_var = tk.BooleanVar(value=False)
        self.iso_full_path = ""
        self.ventoy_iso_list = []
        self.last_written_letter = ""
        self.is_running = False
        self.logs = []

        self.build_ui()
        self.refresh_disks()
        self.auto_detect_ventoy()

    def tr(self, key):
        return LANG_PACK.get(self.current_lang, LANG_PACK["English"]).get(key, key)

    def configure_dark_theme(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Dark.TCombobox",
            fieldbackground=self.bg_input,
            background=self.bg_btn,
            foreground=self.fg_text,
            arrowcolor=self.fg_text,
            bordercolor="#424242",
            lightcolor="#303030",
            darkcolor="#1e1e1e",
            padding=4,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", self.bg_input), ("disabled", "#202020")],
            foreground=[("readonly", self.fg_text), ("disabled", "#666666")],
            background=[("active", self.bg_btn_hover)],
        )
        style.configure(
            "Dark.Horizontal.TProgressbar",
            troughcolor=self.bg_input,
            background="#0078d7",
            bordercolor="#383838",
            thickness=18,
        )
        style.configure("Dark.TSeparator", background="#383838")

    def add_section_header(self, title_key):
        frame = tk.Frame(self.root, bg=self.bg_main)
        frame.pack(fill="x", padx=14, pady=(7, 2))
        lbl = tk.Label(
            frame,
            text=self.tr(title_key),
            font=("Segoe UI", 9, "bold"),
            fg=self.accent_blue,
            bg=self.bg_main,
        )
        lbl.pack(side="left")
        ttk.Separator(frame, orient="horizontal", style="Dark.TSeparator").pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=2
        )
        return lbl
    def build_ui(self):
        # Top Language Bar
        f_top = tk.Frame(self.root, bg=self.bg_main)
        f_top.pack(fill="x", padx=14, pady=(8, 0))
        self.lbl_lang = tk.Label(f_top, text=self.tr("lang_lbl"), font=("Segoe UI", 8, "bold"), fg=self.fg_muted, bg=self.bg_main)
        self.lbl_lang.pack(side="left")

        self.lang_combo = ttk.Combobox(
            f_top,
            textvariable=self.lang_var,
            values=["English", "Русский", "Українська"],
            state="readonly",
            width=13,
            style="Dark.TCombobox",
            font=("Segoe UI", 8),
        )
        self.lang_combo.pack(side="left", padx=(6, 0))
        self.lang_combo.bind("<<ComboboxSelected>>", self.on_language_change)

        self.lbl_sec_mode = self.add_section_header("sec_mode")
        f_mode = tk.Frame(self.root, bg=self.bg_main)
        f_mode.pack(fill="x", padx=14)

        self.rad_rufus = tk.Radiobutton(
            f_mode,
            text=self.tr("rad_rufus"),
            variable=self.mode_var,
            value="rufus",
            bg=self.bg_main,
            fg=self.fg_text,
            selectcolor=self.bg_input,
            activebackground=self.bg_main,
            activeforeground=self.fg_text,
            font=("Segoe UI", 9, "bold"),
            command=self.on_mode_switch,
        )
        self.rad_rufus.pack(side="left")

        self.rad_ventoy = tk.Radiobutton(
            f_mode,
            text=self.tr("rad_ventoy"),
            variable=self.mode_var,
            value="ventoy",
            bg=self.bg_main,
            fg=self.fg_text,
            selectcolor=self.bg_input,
            activebackground=self.bg_main,
            activeforeground=self.fg_text,
            font=("Segoe UI", 9, "bold"),
            command=self.on_mode_switch,
        )
        self.rad_ventoy.pack(side="right")

        f_vdir = tk.Frame(self.root, bg=self.bg_main)
        f_vdir.pack(fill="x", padx=14, pady=(4, 0))
        self.vdir_var = tk.StringVar(value=self.ventoy_dir)
        tk.Entry(
            f_vdir,
            textvariable=self.vdir_var,
            state="readonly",
            readonlybackground=self.bg_input,
            fg=self.fg_muted,
            relief="flat",
            font=("Segoe UI", 8),
        ).pack(side="left", fill="x", expand=True, padx=(0, 5), ipady=3)
        self.btn_vdir = tk.Button(
            f_vdir,
            text=self.tr("btn_vdir"),
            width=12,
            bg=self.bg_btn,
            fg=self.fg_text,
            activebackground=self.bg_btn_hover,
            activeforeground=self.fg_text,
            relief="flat",
            font=("Segoe UI", 8),
            command=self.browse_ventoy_folder,
        )
        self.btn_vdir.pack(side="right")

        self.lbl_sec_dev = self.add_section_header("sec_dev")
        f_dev = tk.Frame(self.root, bg=self.bg_main)
        f_dev.pack(fill="x", padx=14)
        self.disk_combo = ttk.Combobox(f_dev, state="readonly", style="Dark.TCombobox", font=("Segoe UI", 9))
        self.disk_combo.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.btn_refresh = tk.Button(
            f_dev,
            text="↻",
            width=3,
            bg=self.bg_btn,
            fg=self.fg_text,
            activebackground=self.bg_btn_hover,
            activeforeground=self.fg_text,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            command=self.refresh_disks,
        )
        self.btn_refresh.pack(side="right")

        self.lbl_boot = tk.Label(self.root, text=self.tr("lbl_boot"), font=("Segoe UI", 9), fg=self.fg_text, bg=self.bg_main)
        self.lbl_boot.pack(anchor="w", padx=14, pady=(6, 2))
        f_boot = tk.Frame(self.root, bg=self.bg_main)
        f_boot.pack(fill="x", padx=14)
        self.boot_label_var = tk.StringVar(value=self.tr("boot_rufus"))
        tk.Entry(
            f_boot,
            textvariable=self.boot_label_var,
            state="readonly",
            readonlybackground=self.bg_input,
            fg=self.fg_text,
            relief="flat",
            font=("Segoe UI", 9),
        ).pack(side="left", fill="x", expand=True, padx=(0, 5), ipady=3)

        self.btn_hash = tk.Button(
            f_boot,
            text="#",
            width=3,
            bg=self.bg_btn,
            fg=self.accent_blue,
            activebackground=self.bg_btn_hover,
            activeforeground=self.accent_blue,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            command=self.compute_iso_hashes,
        )
        self.btn_hash.pack(side="right", padx=(4, 0))

        self.btn_select = tk.Button(
            f_boot,
            text=self.tr("btn_select"),
            width=10,
            bg=self.bg_btn,
            fg=self.fg_text,
            activebackground=self.bg_btn_hover,
            activeforeground=self.fg_text,
            relief="flat",
            font=("Segoe UI", 8, "bold"),
            command=self.select_images,
        )
        self.btn_select.pack(side="right")

        self.iso_info_var = tk.StringVar(value=self.tr("no_iso"))
        tk.Label(self.root, textvariable=self.iso_info_var, font=("Segoe UI", 8), fg=self.accent_green, bg=self.bg_main).pack(anchor="w", padx=14, pady=(2, 2))

        self.lbl_opt = tk.Label(self.root, text=self.tr("lbl_opt"), font=("Segoe UI", 9), fg=self.fg_text, bg=self.bg_main)
        self.lbl_opt.pack(anchor="w", padx=14, pady=(2, 2))
        self.opt_combo = ttk.Combobox(
            self.root,
            values=[self.tr("opt_std"), self.tr("opt_wtg")],
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 9),
        )
        self.opt_combo.current(0)
        self.opt_combo.pack(fill="x", padx=14)

        f_part = tk.Frame(self.root, bg=self.bg_main)
        f_part.pack(fill="x", padx=14, pady=(6, 0))
        self.lbl_scheme = tk.Label(f_part, text=self.tr("lbl_scheme"), font=("Segoe UI", 9), fg=self.fg_text, bg=self.bg_main)
        self.lbl_scheme.grid(row=0, column=0, sticky="w")
        self.lbl_target = tk.Label(f_part, text=self.tr("lbl_target"), font=("Segoe UI", 9), fg=self.fg_text, bg=self.bg_main)
        self.lbl_target.grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.scheme_combo = ttk.Combobox(f_part, values=["GPT", "MBR"], state="readonly", width=18, style="Dark.TCombobox", font=("Segoe UI", 9))
        self.scheme_combo.current(0)
        self.scheme_combo.bind("<<ComboboxSelected>>", self.on_scheme_switch)
        self.scheme_combo.grid(row=1, column=0, sticky="w", pady=2)

        self.target_sys_var = tk.StringVar(value="UEFI (non CSM)")
        tk.Entry(
            f_part,
            textvariable=self.target_sys_var,
            state="readonly",
            readonlybackground=self.bg_input,
            fg=self.fg_text,
            relief="flat",
            width=41,
            font=("Segoe UI", 9),
        ).grid(row=1, column=1, sticky="w", padx=(12, 0), pady=2, ipady=3)
        self.lbl_sec_fmt = self.add_section_header("sec_fmt")
        self.lbl_vol = tk.Label(self.root, text=self.tr("lbl_vol"), font=("Segoe UI", 9), fg=self.fg_text, bg=self.bg_main)
        self.lbl_vol.pack(anchor="w", padx=14, pady=(2, 2))
        self.vol_label_var = tk.StringVar(value="WIN_USB")
        self.vol_label_entry = tk.Entry(
            self.root,
            textvariable=self.vol_label_var,
            bg=self.bg_input,
            fg=self.fg_text,
            insertbackground=self.fg_text,
            relief="flat",
            font=("Segoe UI", 9),
        )
        self.vol_label_entry.pack(fill="x", padx=14, ipady=3)

        f_fs = tk.Frame(self.root, bg=self.bg_main)
        f_fs.pack(fill="x", padx=14, pady=(6, 0))
        self.lbl_fs = tk.Label(f_fs, text=self.tr("lbl_fs"), font=("Segoe UI", 9), fg=self.fg_text, bg=self.bg_main)
        self.lbl_fs.grid(row=0, column=0, sticky="w")
        self.lbl_cluster = tk.Label(f_fs, text=self.tr("lbl_cluster"), font=("Segoe UI", 9), fg=self.fg_text, bg=self.bg_main)
        self.lbl_cluster.grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.fs_combo = ttk.Combobox(f_fs, values=["NTFS", "FAT32", "exFAT"], state="readonly", width=23, style="Dark.TCombobox", font=("Segoe UI", 9))
        self.fs_combo.current(0)
        self.fs_combo.grid(row=1, column=0, sticky="w", pady=2)

        self.cluster_combo = ttk.Combobox(f_fs, values=["4096 bytes (Default)", "8192 bytes", "16 kilobytes"], state="readonly", width=35, style="Dark.TCombobox", font=("Segoe UI", 9))
        self.cluster_combo.current(0)
        self.cluster_combo.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=2)

        self.chk_bad = tk.Checkbutton(
            self.root,
            text=self.tr("chk_bad"),
            variable=self.bad_blocks_var,
            bg=self.bg_main,
            fg=self.fg_text,
            selectcolor=self.bg_input,
            activebackground=self.bg_main,
            activeforeground=self.fg_text,
            font=("Segoe UI", 9),
        )
        self.chk_bad.pack(anchor="w", padx=12, pady=(6, 0))

        self.lbl_sec_status = self.add_section_header("sec_status")
        self.status_var = tk.StringVar(value=self.tr("ready"))
        self.status_lbl = tk.Label(
            self.root,
            textvariable=self.status_var,
            font=("Segoe UI", 9, "bold"),
            fg=self.accent_green,
            bg=self.bg_input,
            relief="flat",
            anchor="w",
            padx=8,
            pady=4,
        )
        self.status_lbl.pack(fill="x", padx=14, pady=(2, 6))

        self.progress = ttk.Progressbar(self.root, mode="determinate", maximum=100, style="Dark.Horizontal.TProgressbar")
        self.progress.pack(fill="x", padx=14, pady=(0, 10))

        f_btn = tk.Frame(self.root, bg=self.bg_main)
        f_btn.pack(fill="x", padx=14, pady=(4, 12))
        self.btn_log = tk.Button(
            f_btn,
            text=self.tr("btn_log"),
            width=8,
            bg=self.bg_btn,
            fg=self.fg_text,
            activebackground=self.bg_btn_hover,
            activeforeground=self.fg_text,
            relief="flat",
            command=self.show_log_viewer,
        )
        self.btn_log.pack(side="left")

        self.btn_bench = tk.Button(
            f_btn,
            text=self.tr("btn_bench"),
            width=9,
            bg=self.bg_btn,
            fg=self.accent_orange,
            activebackground=self.bg_btn_hover,
            activeforeground=self.accent_orange,
            relief="flat",
            command=self.run_usb_benchmark,
        )
        self.btn_bench.pack(side="left", padx=(4, 0))

        self.btn_eject = tk.Button(
            f_btn,
            text=self.tr("btn_eject"),
            width=8,
            bg=self.bg_btn,
            fg=self.accent_blue,
            activebackground=self.bg_btn_hover,
            activeforeground=self.accent_blue,
            relief="flat",
            command=self.eject_active_usb,
        )
        self.btn_eject.pack(side="left", padx=(4, 0))

        self.btn_close = tk.Button(
            f_btn,
            text=self.tr("btn_close"),
            width=9,
            bg=self.bg_btn,
            fg=self.fg_text,
            activebackground=self.bg_btn_hover,
            activeforeground=self.fg_text,
            relief="flat",
            command=self.on_exit_request,
        )
        self.btn_close.pack(side="right")

        self.btn_start = tk.Button(
            f_btn,
            text=self.tr("btn_start"),
            width=10,
            bg="#0078d7",
            fg="white",
            activebackground="#1c97ea",
            activeforeground="white",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            command=self.validate_and_start,
        )
        self.btn_start.pack(side="right", padx=(0, 5))

        self.on_mode_switch()

    def on_language_change(self, event=None):
        self.current_lang = self.lang_var.get()
        self.lbl_lang.config(text=self.tr("lang_lbl"))
        self.lbl_sec_mode.config(text=self.tr("sec_mode"))
        self.rad_rufus.config(text=self.tr("rad_rufus"))
        self.rad_ventoy.config(text=self.tr("rad_ventoy"))
        self.btn_vdir.config(text=self.tr("btn_vdir"))
        self.lbl_sec_dev.config(text=self.tr("sec_dev"))
        self.lbl_boot.config(text=self.tr("lbl_boot"))
        self.btn_select.config(text=self.tr("btn_select"))
        self.lbl_opt.config(text=self.tr("lbl_opt"))
        self.opt_combo.config(values=[self.tr("opt_std"), self.tr("opt_wtg")])
        self.opt_combo.current(0)
        self.lbl_scheme.config(text=self.tr("lbl_scheme"))
        self.lbl_target.config(text=self.tr("lbl_target"))
        self.lbl_sec_fmt.config(text=self.tr("sec_fmt"))
        self.lbl_vol.config(text=self.tr("lbl_vol"))
        self.lbl_fs.config(text=self.tr("lbl_fs"))
        self.lbl_cluster.config(text=self.tr("lbl_cluster"))
        self.chk_bad.config(text=self.tr("chk_bad"))
        self.lbl_sec_status.config(text=self.tr("sec_status"))
        self.btn_log.config(text=self.tr("btn_log"))
        self.btn_bench.config(text=self.tr("btn_bench"))
        self.btn_eject.config(text=self.tr("btn_eject"))
        self.btn_close.config(text=self.tr("btn_close"))
        self.btn_start.config(text=self.tr("btn_start"))
        if not self.is_running:
            self.status_var.set(self.tr("ready"))
        self.on_mode_switch()
    def log(self, msg, is_error=False):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.logs.append(line)
        try:
            log_file = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "rufus_station.log")
            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write(line + "\n")
        except OSError:
            pass

        self.root.after(0, lambda: self.status_var.set(msg[:64]))
        color = self.accent_red if is_error else self.accent_green
        self.root.after(0, lambda: self.status_lbl.config(fg=color))

    def show_log_viewer(self):
        win = tk.Toplevel(self.root)
        win.title("Engineering Station Log")
        win.geometry("600x420")
        win.configure(bg=self.bg_main)
        txt = tk.Text(win, font=("Consolas", 8), bg=self.bg_input, fg=self.fg_text, relief="flat", padx=8, pady=8)
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", "\n".join(self.logs))
        txt.config(state="disabled")

    def auto_detect_ventoy(self):
        if os.path.exists(self.ventoy_dir):
            for root_dir, _, files in os.walk(self.ventoy_dir):
                if "Ventoy2Disk.exe" in files:
                    self.ventoy_dir = root_dir
                    self.vdir_var.set(root_dir)
                    self.log(f"Ventoy ready: {root_dir}")
                    return
        self.log(self.tr("ready"))

    def browse_ventoy_folder(self):
        folder = filedialog.askdirectory(title="Select Ventoy Folder")
        if folder:
            for root_dir, _, files in os.walk(folder):
                if "Ventoy2Disk.exe" in files:
                    self.ventoy_dir = root_dir
                    self.vdir_var.set(root_dir)
                    self.log(f"Ventoy folder: {root_dir}")
                    return
            messagebox.showerror("Error", self.tr("warn_vexe"))

    def run_usb_benchmark(self):
        if self.is_running or not self.disks_data or self.disk_combo.current() == -1:
            return
        disk = self.disks_data[self.disk_combo.current()]
        let = StorageUtils.get_partition_letter(disk["Number"])
        if not let or not os.path.exists(f"{let}:\\"):
            return messagebox.showwarning("USB Benchmark", "No mounted drive letter found for selected disk.")

        def bench_worker():
            self.set_ui_locked(True)
            test_file = os.path.join(f"{let}:\\", "_usb_bench.tmp")
            chunk = os.urandom(1024 * 1024)
            blocks = 64
            try:
                self.log(f"Testing write speed on ({let}:) — 64 MB...")
                t0 = time.time()
                with open(test_file, "wb") as f:
                    for i in range(blocks):
                        f.write(chunk)
                        self.root.after(0, lambda p=((i + 1) / blocks) * 50: self.progress.config(value=p))
                    f.flush()
                    os.fsync(f.fileno())
                w_speed = blocks / max(time.time() - t0, 0.01)

                self.log(f"Testing read speed on ({let}:)...")
                t1 = time.time()
                with open(test_file, "rb") as f:
                    for i in range(blocks):
                        _ = f.read(1024 * 1024)
                        self.root.after(0, lambda p=50 + ((i + 1) / blocks) * 50: self.progress.config(value=p))
                r_speed = blocks / max(time.time() - t1, 0.01)

                self.log(f"USB ({let}:): Write {w_speed:.1f} MB/s | Read {r_speed:.1f} MB/s")
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "USB Speed Benchmark",
                        f"Device: {disk['FriendlyName']} ({let}:)\n\n"
                        f"Write Speed: {w_speed:.2f} MB/s\n"
                        f"Read Speed : {r_speed:.2f} MB/s",
                    ),
                )
            except Exception as e:
                self.log(f"USB Test Error: {e}", is_error=True)
            finally:
                if os.path.exists(test_file):
                    try:
                        os.remove(test_file)
                    except OSError:
                        pass
                self.root.after(0, lambda: (self.progress.config(value=0), self.set_ui_locked(False)))

        threading.Thread(target=bench_worker, daemon=True).start()

    def eject_active_usb(self):
        if self.is_running:
            return
        if self.last_written_letter and os.path.exists(f"{self.last_written_letter}:\\"):
            StorageUtils.close_explorer_windows_for_drives([self.last_written_letter])
            StorageUtils.flush_volume_cache(self.last_written_letter)
            StorageUtils.eject_volume(self.last_written_letter)
            self.log(f"USB ({self.last_written_letter}:) safely ejected")
            messagebox.showinfo("Safe Eject", f"Drive ({self.last_written_letter}:) is now safe to remove.")
        else:
            messagebox.showinfo("Safe Eject", "No active written drive to eject.")

    def compute_iso_hashes(self):
        target_iso = self.ventoy_iso_list if (self.mode_var.get() == "ventoy" and self.ventoy_iso_list) else self.iso_full_path
        if not target_iso or not os.path.exists(target_iso):
            return messagebox.showinfo("Checksums", self.tr("warn_iso"))
        if self.is_running:
            return

        def hash_worker():
            self.set_ui_locked(True)
            self.log("Calculating SHA-256 and MD5 checksums...")
            sha256 = hashlib.sha256()
            md5 = hashlib.md5()
            total = os.path.getsize(target_iso) or 1
            done = 0
            try:
                with open(target_iso, "rb") as f:
                    while True:
                        buf = f.read(4 * 1024 * 1024)
                        if not buf:
                            break
                        sha256.update(buf)
                        md5.update(buf)
                        done += len(buf)
                        pct = (done / total) * 100
                        self.root.after(0, lambda p=pct: self.progress.config(value=p))
                s_hex = sha256.hexdigest().upper()
                m_hex = md5.hexdigest().upper()
                self.log("Checksums calculated successfully")
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "ISO Checksums",
                        f"File: {os.path.basename(target_iso)}\n\nMD5:\n{m_hex}\n\nSHA-256:\n{s_hex}",
                    ),
                )
            except Exception as e:
                self.log(f"Hash Error: {e}", is_error=True)
            finally:
                self.root.after(0, lambda: (self.progress.config(value=0), self.set_ui_locked(False)))

        threading.Thread(target=hash_worker, daemon=True).start()
    def on_scheme_switch(self, event=None):
        self.target_sys_var.set("UEFI (non CSM)" if self.scheme_combo.get() == "GPT" else "BIOS or UEFI-CSM")

    def on_mode_switch(self):
        is_ventoy = self.mode_var.get() == "ventoy"
        if is_ventoy:
            self.boot_label_var.set(self.tr("boot_ventoy"))
            self.opt_combo.config(state="disabled")
            self.fs_combo.config(state="disabled")
            self.cluster_combo.config(state="disabled")
            self.vol_label_entry.config(state="disabled")
            self.chk_bad.config(state="disabled")
            if self.ventoy_iso_list:
                total_gb = sum(os.path.getsize(p) for p in self.ventoy_iso_list if os.path.exists(p)) / (1024 ** 3)
                names = ", ".join(os.path.basename(p) for p in self.ventoy_iso_list)
                self.iso_info_var.set(f"Ventoy ({len(self.ventoy_iso_list)} files, {total_gb:.2f} GB): {names[:44]}")
            else:
                self.iso_info_var.set(self.tr("no_ventoy_iso"))
        else:
            self.boot_label_var.set(self.tr("boot_rufus"))
            self.opt_combo.config(state="readonly")
            self.fs_combo.config(state="readonly")
            self.cluster_combo.config(state="readonly")
            self.vol_label_entry.config(state="normal")
            self.chk_bad.config(state="normal")
            if self.iso_full_path and os.path.exists(self.iso_full_path):
                base = os.path.basename(self.iso_full_path)
                size_gb = os.path.getsize(self.iso_full_path) / (1024 ** 3)
                self.iso_info_var.set(f"{base[:46]} ({size_gb:.2f} GB)")
            else:
                self.iso_info_var.set(self.tr("no_iso"))

    def refresh_disks(self):
        if self.is_running:
            return
        try:
            self.disks_data = StorageUtils.get_physical_disks()
            items, usb_idx = [], 0
            for idx, d in enumerate(self.disks_data):
                bus = d.get("BusType", "N/A")
                tag = "" if bus == "USB" else f"[{bus}] "
                items.append(f"{tag}{d['FriendlyName']} ({d['GB']} GB) [Disk {d['Number']}]")
                if bus == "USB":
                    usb_idx = idx
            self.disk_combo["values"] = items
            if items:
                self.disk_combo.current(usb_idx)
            self.log(self.tr("ready"))
        except Exception as e:
            self.log(f"Disk read error: {e}", is_error=True)

    def select_images(self):
        if self.mode_var.get() == "ventoy":
            paths = filedialog.askopenfilenames(
                title="Select images for Ventoy USB (Multiple allowed)",
                filetypes=[("Bootable images", "*.iso *.wim *.img *.vhd *.vhdx *.efi"), ("All files", "*.*")],
            )
            if paths:
                self.ventoy_iso_list = list(paths)
                total_gb = sum(os.path.getsize(p) for p in self.ventoy_iso_list) / (1024 ** 3)
                names = ", ".join(os.path.basename(p) for p in self.ventoy_iso_list)
                self.iso_info_var.set(f"Selected: {len(self.ventoy_iso_list)} ({total_gb:.2f} GB) — {names[:38]}")
                self.log(f"Ventoy queue ({len(self.ventoy_iso_list)}): {names}")
            return

        path = filedialog.askopenfilename(
            title="Select ISO image",
            filetypes=[("ISO images", "*.iso"), ("All files", "*.*")],
        )
        if path:
            self.iso_full_path = path
            base = os.path.basename(path)
            size_gb = os.path.getsize(path) / (1024 ** 3)
            self.iso_info_var.set(f"{base[:38]} ({size_gb:.2f} GB) — inspecting editions...")
            self.log(f"Selected image: {base} ({size_gb:.2f} GB)")

            def analyze():
                eds = EngineeringTools.inspect_windows_iso(path)
                if eds:
                    summary = ", ".join(eds[:3]) + (f" (+{len(eds)-3})" if len(eds) > 3 else "")
                    self.root.after(0, lambda: self.iso_info_var.set(f"{size_gb:.2f} GB | Editions: {summary[:44]}"))
                    self.log(f"Editions found: {', '.join(eds)}")
                else:
                    self.root.after(0, lambda: self.iso_info_var.set(f"{base[:46]} ({size_gb:.2f} GB)"))

            threading.Thread(target=analyze, daemon=True).start()

    def set_ui_locked(self, locked):
        self.is_running = locked
        state_btn = "disabled" if locked else "normal"
        state_cmb = "disabled" if locked else "readonly"
        self.btn_start.config(state=state_btn)
        self.btn_select.config(state=state_btn)
        self.btn_hash.config(state=state_btn)
        self.btn_refresh.config(state=state_btn)
        self.btn_vdir.config(state=state_btn)
        self.btn_eject.config(state=state_btn)
        self.btn_bench.config(state=state_btn)
        self.lang_combo.config(state=state_cmb)
        self.disk_combo.config(state=state_cmb)
        self.scheme_combo.config(state=state_cmb)
        if not locked:
            self.on_mode_switch()
        else:
            self.opt_combo.config(state="disabled")
            self.fs_combo.config(state="disabled")
            self.cluster_combo.config(state="disabled")
            self.vol_label_entry.config(state="disabled")
            self.chk_bad.config(state="disabled")

    def on_exit_request(self):
        if self.is_running:
            if not messagebox.askyesno("Writing in progress", "Write operation is still running! Abort and exit?"):
                return
        self.root.destroy()

    def ask_backup_options(self):
        dlg = tk.Toplevel(self.root)
        dlg.title(self.tr("dlg_bkp_title"))
        dlg.geometry("450x195")
        dlg.resizable(False, False)
        dlg.configure(bg=self.bg_main)
        dlg.grab_set()

        tk.Label(
            dlg,
            text=self.tr("dlg_bkp_head"),
            font=("Segoe UI", 9, "bold"),
            fg=self.accent_blue,
            bg=self.bg_main,
        ).pack(anchor="w", padx=16, pady=(14, 8))

        v_net = tk.BooleanVar(value=True)
        v_drv = tk.BooleanVar(value=False)

        tk.Checkbutton(
            dlg,
            text=self.tr("dlg_bkp_net"),
            variable=v_net,
            bg=self.bg_main,
            fg=self.fg_text,
            selectcolor=self.bg_input,
            activebackground=self.bg_main,
            activeforeground=self.fg_text,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=16, pady=2)

        tk.Checkbutton(
            dlg,
            text=self.tr("dlg_bkp_drv"),
            variable=v_drv,
            bg=self.bg_main,
            fg=self.fg_text,
            selectcolor=self.bg_input,
            activebackground=self.bg_main,
            activeforeground=self.fg_text,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=16, pady=2)

        res = {"confirmed": False}

        def on_ok():
            self.backup_net_var.set(v_net.get())
            self.backup_drv_var.set(v_drv.get())
            res["confirmed"] = True
            dlg.destroy()

        f_btn = tk.Frame(dlg, bg=self.bg_main)
        f_btn.pack(fill="x", padx=16, pady=(18, 12))
        tk.Button(
            f_btn,
            text=self.tr("dlg_cont"),
            width=12,
            bg="#0078d7",
            fg="white",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            command=on_ok,
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            f_btn,
            text=self.tr("dlg_cancel"),
            width=10,
            bg=self.bg_btn,
            fg=self.fg_text,
            relief="flat",
            command=dlg.destroy,
        ).pack(side="right")

        self.root.wait_window(dlg)
        return res["confirmed"]
    def verify_bad_sectors(self, drive_letter):
        self.log("Checking USB drive for bad sectors...")
        test_file = os.path.join(f"{drive_letter}:\\", "_sector_verify.tmp")
        pattern = os.urandom(1024 * 1024)
        blocks = 64
        try:
            with open(test_file, "wb") as f:
                for i in range(blocks):
                    f.write(pattern)
                    pct = (i / blocks) * 10
                    self.root.after(0, lambda p=pct: self.progress.config(value=p))
            with open(test_file, "rb") as f:
                for i in range(blocks):
                    read_back = f.read(1024 * 1024)
                    if read_back != pattern:
                        raise Exception("Bad sectors or fake USB capacity detected!")
        finally:
            if os.path.exists(test_file):
                try:
                    os.remove(test_file)
                except OSError:
                    pass
        self.log("Bad sector verification passed")

    def copy_tree_telemetric(self, src_dir, dst_dir, start_pct, end_pct):
        total_bytes = StorageUtils.get_dir_size(src_dir) or 1
        copied_bytes = 0
        chunk_size = 4 * 1024 * 1024
        start_time = time.time()
        last_update = start_time

        for root_path, _, files in os.walk(src_dir):
            rel_path = os.path.relpath(root_path, src_dir)
            target_root = dst_dir if rel_path == "." else os.path.join(dst_dir, rel_path)
            os.makedirs(target_root, exist_ok=True)

            for file_name in files:
                src_file = os.path.join(root_path, file_name)
                dst_file = os.path.join(target_root, file_name)
                try:
                    with open(src_file, "rb") as fsrc, open(dst_file, "wb") as fdst:
                        while True:
                            buf = fsrc.read(chunk_size)
                            if not buf:
                                break
                            fdst.write(buf)
                            copied_bytes += len(buf)

                            now = time.time()
                            if now - last_update >= 0.4:
                                last_update = now
                                elapsed = max(now - start_time, 0.1)
                                speed_mbs = (copied_bytes / (1024 * 1024)) / elapsed
                                rem_bytes = max(total_bytes - copied_bytes, 0)
                                eta_sec = rem_bytes / max(copied_bytes / elapsed, 1)
                                ratio = min(copied_bytes / total_bytes, 1.0)
                                pct = start_pct + (end_pct - start_pct) * ratio
                                self.root.after(0, lambda p=pct: self.progress.config(value=p))
                                self.log(
                                    f"Writing: {pct:.0f}% | {speed_mbs:.1f} MB/s | ETA: {StorageUtils.format_eta(eta_sec)}"
                                )
                except OSError as err:
                    self.log(f"Skipped file {file_name}: {err}")

    def copy_files_batch_telemetric(self, file_list, target_drive, start_pct, end_pct):
        valid_files = [f for f in file_list if os.path.exists(f)]
        total_bytes = sum(os.path.getsize(f) for f in valid_files) or 1
        copied_bytes = 0
        chunk_size = 4 * 1024 * 1024
        start_time = time.time()
        last_update = start_time

        for idx, src_file in enumerate(valid_files, start=1):
            base_name = os.path.basename(src_file)
            dst_file = os.path.join(f"{target_drive}:\\", base_name)
            self.log(f"Ventoy [{idx}/{len(valid_files)}]: {base_name}...")

            with open(src_file, "rb") as fsrc, open(dst_file, "wb") as fdst:
                while True:
                    buf = fsrc.read(chunk_size)
                    if not buf:
                        break
                    fdst.write(buf)
                    copied_bytes += len(buf)
                    now = time.time()
                    if now - last_update >= 0.4:
                        last_update = now
                        elapsed = max(now - start_time, 0.1)
                        speed_mbs = (copied_bytes / (1024 * 1024)) / elapsed
                        eta_sec = max(total_bytes - copied_bytes, 0) / max(copied_bytes / elapsed, 1)
                        ratio = min(copied_bytes / total_bytes, 1.0)
                        pct = start_pct + (end_pct - start_pct) * ratio
                        self.root.after(0, lambda p=pct: self.progress.config(value=p))
                        self.log(
                            f"Ventoy [{idx}/{len(valid_files)}]: {pct:.0f}% | {speed_mbs:.1f} MB/s | ETA: {StorageUtils.format_eta(eta_sec)}"
                        )
    def validate_and_start(self):
        if self.is_running:
            return
        if not self.disks_data or self.disk_combo.current() == -1:
            return messagebox.showwarning("Rufus", self.tr("warn_dev"))

        disk = self.disks_data[self.disk_combo.current()]
        disk_gb = float(disk.get("GB", 0))
        is_ventoy = self.mode_var.get() == "ventoy"

        if not is_ventoy:
            if not self.iso_full_path or not os.path.exists(self.iso_full_path):
                return messagebox.showwarning("Rufus", self.tr("warn_iso"))
            iso_gb = os.path.getsize(self.iso_full_path) / (1024 ** 3)
            if iso_gb > (disk_gb - 0.3):
                return messagebox.showerror("Space Error", self.tr("err_space"))

            if not self.ask_backup_options():
                return
        else:
            v_exe = os.path.join(self.ventoy_dir, "Ventoy2Disk.exe")
            if not os.path.exists(v_exe):
                return messagebox.showwarning("Ventoy", self.tr("warn_vexe"))

            if not self.ventoy_iso_list:
                ans = messagebox.askyesnocancel("Ventoy Images", self.tr("ask_v_iso"))
                if ans is None:
                    return
                if ans is True:
                    paths = filedialog.askopenfilenames(
                        title="Select images for Ventoy USB",
                        filetypes=[("Bootable images", "*.iso *.wim *.img *.vhd *.vhdx *.efi"), ("All files", "*.*")],
                    )
                    if not paths:
                        return
                    self.ventoy_iso_list = list(paths)
                    total_gb = sum(os.path.getsize(p) for p in self.ventoy_iso_list) / (1024 ** 3)
                    names = ", ".join(os.path.basename(p) for p in self.ventoy_iso_list)
                    self.iso_info_var.set(f"Selected: {len(self.ventoy_iso_list)} ({total_gb:.2f} GB) — {names[:38]}")

            if self.ventoy_iso_list:
                total_iso_gb = sum(os.path.getsize(p) for p in self.ventoy_iso_list) / (1024 ** 3)
                if total_iso_gb > (disk_gb - 0.1):
                    return messagebox.showerror("Space Error", self.tr("err_space"))

        if disk.get("BusType") != "USB":
            if not messagebox.askyesno("WARNING", self.tr("warn_non_usb"), icon="warning"):
                return

        if not messagebox.askyesno("WARNING", f"{disk['FriendlyName']} ({disk['GB']} GB)\n\n{self.tr('warn_wipe')}", icon="warning"):
            return

        self.set_ui_locked(True)
        self.progress["value"] = 0
        is_gpt = self.scheme_combo.get() == "GPT"
        worker = self.run_ventoy_pipeline if is_ventoy else self.run_rufus_pipeline
        threading.Thread(target=worker, args=(disk["Number"], is_gpt), daemon=True).start()

    def run_rufus_pipeline(self, disk_num, is_gpt):
        usb_let = StorageUtils.find_free_letter("W")
        efi_let = StorageUtils.find_free_letter("V")
        label = (self.vol_label_var.get().strip() or "WIN_USB")[:11]
        fs = self.fs_combo.get().upper()
        style = "GPT" if is_gpt else "MBR"
        iso_mounted = False

        try:
            self.log("Closing Explorer locks on USB drive...")
            StorageUtils.close_explorer_windows_for_drives([usb_let, efi_let])
            time.sleep(0.5)

            self.log(f"Step 1/5: Partitioning disk {disk_num} ({style} / {fs})...")
            self.root.after(0, lambda: self.progress.config(value=4))

            if is_gpt and fs != "FAT32":
                dp_lines = [
                    f"select disk {disk_num}",
                    "clean",
                    "convert gpt",
                    "create partition primary",
                    f"format fs={fs.lower()} quick label={label}",
                    "shrink desired=250",
                    f"assign letter={usb_let}",
                    "create partition efi size=240",
                    "format fs=fat32 quick label=UEFI_BOOT",
                    f"assign letter={efi_let}",
                    "exit",
                ]
                ok, out = False, ""
                for attempt in range(1, 4):
                    ok, out = ProcessRunner.run_diskpart(dp_lines, f"dp_rufus_gpt_{attempt}.txt")
                    if ok and os.path.exists(f"{usb_let}:\\"):
                        break
                    self.log(f"Retrying partition layout ({attempt}/3)...")
                    time.sleep(1.5)
                if not ok:
                    raise Exception(f"Diskpart GPT layout failed:\n{out}")
            else:
                ps_fmt = (
                    f"Clear-Disk -Number {disk_num} -RemoveData -RemoveOEM -Confirm:$false -ErrorAction SilentlyContinue; "
                    f"Initialize-Disk -Number {disk_num} -PartitionStyle {style} -Confirm:$false; "
                    f"$p = New-Partition -DiskNumber {disk_num} -UseMaximumSize -DriveLetter {usb_let}; "
                    f"Format-Volume -Partition $p -FileSystem {fs} -NewFileSystemLabel '{label}' -Confirm:$false"
                )
                _, err, code = ProcessRunner.run_ps(ps_fmt)
                if code != 0 and not os.path.exists(f"{usb_let}:\\"):
                    raise Exception(f"Volume format failed:\n{err}")

            time.sleep(1.5)
            if not os.path.exists(f"{usb_let}:\\"):
                raise Exception(f"Partition {usb_let}: did not appear after formatting.")

            self.last_written_letter = usb_let

            if not is_gpt:
                ProcessRunner.run_diskpart(
                    [f"select disk {disk_num}", "select partition 1", "active", "exit"],
                    "dp_active.txt",
                )

            if self.bad_blocks_var.get():
                self.verify_bad_sectors(usb_let)

            self.log("Step 2/5: Mounting ISO silently (no drive letter)...")
            self.root.after(0, lambda: self.progress.config(value=12))

            vol_id, err, _ = ProcessRunner.run_ps(
                f"(Mount-DiskImage -ImagePath '{self.iso_full_path}' -NoDriveLetter -PassThru | Get-Volume).UniqueId"
            )
            iso_vol = vol_id.rstrip("\\")
            if not iso_vol:
                raise Exception(f"Failed to mount ISO silently:\n{err}")
            iso_mounted = True

            self.log("Step 3/5: Copying OS files to USB...")
            self.copy_tree_telemetric(iso_vol, f"{usb_let}:\\", 15, 78)

            if self.backup_net_var.get():
                self.log("Backing up Ethernet network & installed apps...")
                EngineeringTools.backup_ethernet_and_system(self.leha_source_dir)

            if self.backup_drv_var.get():
                self.log("Exporting PC drivers (DISM)...")
                EngineeringTools.export_pc_drivers(self.leha_source_dir)

            if os.path.exists(self.leha_source_dir):
                self.log("Step 4/5: Packaging 'от лехи' folder for User Desktop...")
                dst_leha = os.path.join(f"{usb_let}:\\", "_leha_data", "от лехи")
                self.copy_tree_telemetric(self.leha_source_dir, dst_leha, 78, 90)

                unattend_path = os.path.join(f"{usb_let}:\\", "autounattend.xml")
                with open(unattend_path, "w", encoding="utf-8") as f:
                    f.write(EngineeringTools.build_unattend_xml())

            if is_gpt and fs != "FAT32" and os.path.exists(f"{efi_let}:\\"):
                self.log("Step 5/5: Writing UEFI bootloader to EFI partition...")
                self.root.after(0, lambda: self.progress.config(value=93))
                for folder in ["efi", "boot"]:
                    src_f = os.path.join(iso_vol, folder)
                    if os.path.exists(src_f):
                        shutil.copytree(src_f, os.path.join(f"{efi_let}:\\", folder), dirs_exist_ok=True)
                bootmgr = os.path.join(iso_vol, "bootmgr.efi")
                if os.path.exists(bootmgr):
                    shutil.copy2(bootmgr, os.path.join(f"{efi_let}:\\", "bootmgr.efi"))

            bs_exe = os.path.join(iso_vol, "boot", "bootsect.exe")
            if os.path.exists(bs_exe):
                bs_args = [bs_exe, "/nt60", f"{usb_let}:", "/force"] if is_gpt else [bs_exe, "/nt60", f"{usb_let}:", "/mbr", "/force"]
                ProcessRunner.run_cmd(bs_args)

            self.log("Verifying bootloader integrity...")
            if not os.path.exists(os.path.join(f"{usb_let}:\\", "sources", "boot.wim")):
                raise Exception("Verification error: critical file sources\\boot.wim is missing!")

            self.log("Flushing write cache to USB...")
            StorageUtils.flush_volume_cache(usb_let)
            if is_gpt and fs != "FAT32":
                StorageUtils.flush_volume_cache(efi_let)

            ProcessRunner.run_ps(f"Dismount-DiskImage -ImagePath '{self.iso_full_path}'")
            iso_mounted = False

            if is_gpt and fs != "FAT32":
                StorageUtils.close_explorer_windows_for_drives([efi_let])
                ProcessRunner.run_diskpart(
                    [f"select volume {efi_let}", f"remove letter={efi_let}", "exit"],
                    "dp_hide_efi.txt",
                )

            self.root.after(0, lambda: (self.progress.config(value=100), self.root.bell()))
            self.log("READY — WRITE & VERIFICATION COMPLETE")
            messagebox.showinfo("Rufus Engineering Station", self.tr("done_rufus"))

        except Exception as e:
            self.log(f"ERROR: {e}", is_error=True)
            messagebox.showerror("Write Error", str(e))
            if iso_mounted:
                ProcessRunner.run_ps(f"Dismount-DiskImage -ImagePath '{self.iso_full_path}'")
        finally:
            self.root.after(0, lambda: self.set_ui_locked(False))
    def run_ventoy_pipeline(self, disk_num, is_gpt):
        v_exe = os.path.join(self.ventoy_dir, "Ventoy2Disk.exe")
        try:
            self.log("Installing Ventoy core to USB drive...")
            self.root.after(0, lambda: self.progress.config(value=25))

            args = [v_exe, "VTOYCLI", "/I", f"/PhyDrive:{disk_num}"]
            if is_gpt:
                args.append("/GPT")

            ProcessRunner.run_cmd(args, cwd=self.ventoy_dir)
            time.sleep(3)

            v_let = StorageUtils.get_partition_letter(disk_num)
            if v_let:
                self.last_written_letter = v_let
                v_cfg_dir = os.path.join(f"{v_let}:\\", "ventoy")
                os.makedirs(v_cfg_dir, exist_ok=True)
                v_json = {
                    "control": [
                        {"VTOY_DEFAULT_MENU_MODE": "0"},
                        {"VTOY_WIN11_BYPASS_CHECK": "1"},
                        {"VTOY_WIN11_BYPASS_NRO": "1"},
                    ]
                }
                with open(os.path.join(v_cfg_dir, "ventoy.json"), "w", encoding="utf-8") as jf:
                    json.dump(v_json, jf, indent=4)

                if self.ventoy_iso_list:
                    self.copy_files_batch_telemetric(self.ventoy_iso_list, v_let, 35, 98)

                StorageUtils.flush_volume_cache(v_let)

            self.root.after(0, lambda: (self.progress.config(value=100), self.root.bell()))
            self.log("READY — VENTOY INSTALLED")
            messagebox.showinfo("Ventoy Engineering Station", self.tr("done_ventoy"))

        except Exception as e:
            self.log(f"Ventoy ERROR: {e}", is_error=True)
            messagebox.showerror("Ventoy Error", str(e))
        finally:
            self.root.after(0, lambda: self.set_ui_locked(False))


if __name__ == "__main__":
    root = tk.Tk()
    app = RufusVentoyEngineeringApp(root)
    root.mainloop()
