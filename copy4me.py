from __future__ import annotations
import subprocess
import os
import sys
import shutil
import logging
from logging.handlers import RotatingFileHandler
import zipfile
import json
import socket
import platform
import threading
import queue
import time
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, List, Tuple, Dict, Any
import tempfile
import getpass
from concurrent.futures import ThreadPoolExecutor

# Activar alta densidad de píxeles (High DPI) en Windows
if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# --- Carga condicional de dependencias opcionales ---
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, scrolledtext, simpledialog
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

# --- Constantes y Configuración Global ---
VERSION = "5.0"
APP_NAME = "Copy4Me"
MAX_BACKUPS = 10
EXCLUDE_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env',
    '.idea', '.vscode', 'System Volume Information', '$RECYCLE.BIN',
    '.Trash-1000', 'Thumbs.db', '.DS_Store'
}
EXCLUDE_EXTENSIONS = {'.tmp', '.log', '.bak'}
DEFAULT_COMPRESSION_LEVEL = 6
CHUNK_SIZE = 64 * 1024

def validar_espacio_disponible(origen: Path, destino: Path, tamano_total: Optional[int] = None) -> tuple[bool, str]:
    try:
        if tamano_total is None:
            tamano_total = calcular_tamano_origen(origen)
            
        dest_abs = destino.resolve()
        target_check = dest_abs
        while not target_check.exists() and target_check.parent != target_check:
            target_check = target_check.parent

        _, _, libre = shutil.disk_usage(target_check)

        if libre < tamano_total:
            tam_mb = tamano_total / (1024 * 1024)
            lib_mb = libre / (1024 * 1024)
            return False, f"Espacio insuficiente. Requerido: {tam_mb:.1f} MB | Disponible en destino: {lib_mb:.1f} MB"

        return True, "OK"
    except Exception as e:
        return True, f"No se pudo verificar el espacio: {e}"

def get_base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

BASE_DIR = get_base_dir()
DIR_BACKUPS = BASE_DIR / "copy4me_backups"
CONFIG_FILE = BASE_DIR / "config.json"

def setup_logging():
    try:
        DIR_BACKUPS.mkdir(parents=True, exist_ok=True)
        log_file = DIR_BACKUPS / "sync_history.log"
        handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=2, encoding='utf-8'
        )
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s] %(message)s', '%Y-%m-%d %H:%M:%S')
        handler.setFormatter(formatter)
        
        root_logger = logging.getLogger('')
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    except Exception as e:
        print(f"⚠️ No se pudo configurar el registro: {e}")

setup_logging()
logger = logging.getLogger("Copy4Me")

def formatear_tamano(tamano_bytes: int) -> str:
    size = float(tamano_bytes)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

def limpiar_nombre_ruta(nombre: str) -> str:
    if not nombre:
        return "proyecto_backup"
    nombre_limpio = re.sub(r'[\\/*?:"<>|()]', '_', nombre).strip()
    return nombre_limpio if nombre_limpio else "proyecto_backup"

def calcular_tamano_origen(origen: Path) -> int:
    if origen.is_file():
        return origen.stat().st_size
    total = 0
    for f in origen.rglob('*'):
        if f.is_file():
            try:
                total += f.stat().st_size
            except Exception:
                pass
    return total

def calcular_tamano_descomprimido(ruta_zip: Path) -> int:
    try:
        with zipfile.ZipFile(ruta_zip, 'r') as zf:
            return sum(file.file_size for file in zf.infolist())
    except Exception:
        return ruta_zip.stat().st_size

# --- Gestor de Configuración Atómico ---
class ConfigManager:
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.path = config_path
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        defaults = {
            "perfiles": {},
            "opciones": {
                "compresion": DEFAULT_COMPRESSION_LEVEL,
                "verificar_hash": True,
                "cifrar_backups": False,
                "max_backups": MAX_BACKUPS,
                "excluir_patrones": [],
                "excluir_regex": []
            }
        }
        if not self.path.exists():
            return defaults
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "perfiles" not in data or not isinstance(data["perfiles"], dict):
                    data["perfiles"] = {}
                if "opciones" not in data or not isinstance(data["opciones"], dict):
                    data["opciones"] = defaults["opciones"]
                else:
                    for k, v in defaults["opciones"].items():
                        data["opciones"].setdefault(k, v)
                return data
        except Exception as e:
            logger.error(f"Error al leer configuración: {e}")
            return defaults

    def save(self) -> bool:
        try:
            temp_file = self.path.with_suffix(".tmp")
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=4, ensure_ascii=False)
            temp_file.replace(self.path)
            return True
        except Exception as e:
            logger.error(f"Error al guardar configuración: {e}")
            return False

    def get_perfil(self, nombre: str) -> Optional[Dict]:
        return self._data.get("perfiles", {}).get(nombre)

    def set_perfil(self, nombre: str, ruta_local: Path, ruta_destino: Optional[Path] = None, metadatos: Optional[Dict] = None):
        perfil = {
            "ruta_local": str(ruta_local.resolve()),
            "ruta_destino": str(ruta_destino.resolve()) if ruta_destino else "",
            "ultimo_equipo": socket.gethostname(),
            "sistema_operativo": f"{platform.system()} {platform.release()}",
            "ultima_sincronizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        if metadatos:
            perfil.update(metadatos)
        self._data["perfiles"][nombre] = perfil
        self.save()

    def delete_perfil(self, nombre: str):
        if nombre in self._data.get("perfiles", {}):
            del self._data["perfiles"][nombre]
            self.save()

    def get_opcion(self, key: str, default=None):
        return self._data.get("opciones", {}).get(key, default)

    def set_opcion(self, key: str, value):
        self._data["opciones"][key] = value
        self.save()

# --- USB Detector ---
class USBDetector:
    @staticmethod
    def listar_unidades_extraibles() -> List[Path]:
        unidades = []
        sistema = platform.system()
        if sistema == "Windows":
            try:
                import ctypes
                bitmask = ctypes.windll.kernel32.GetLogicalDrives()
                for i in range(26):
                    if bitmask & (1 << i):
                        letter = chr(65 + i) + ":\\"
                        drive_type = ctypes.windll.kernel32.GetDriveTypeW(letter)
                        if drive_type == 2 or (drive_type == 3 and not letter.startswith("C")):
                            unidades.append(Path(letter))
            except Exception as e:
                logger.warning(f"Error escaneando unidades en Windows: {e}")
        elif sistema == "Linux":
            user = os.getenv("USER") or getpass.getuser()
            media_paths = [Path(f"/media/{user}"), Path(f"/run/media/{user}"), Path("/mnt")]
            for base in media_paths:
                if base.exists():
                    for item in base.iterdir():
                        if item.is_dir() and item.name not in ["cdrom", "floppy"]:
                            unidades.append(item)
        elif sistema == "Darwin":
            volumes = Path("/Volumes")
            if volumes.exists():
                for item in volumes.iterdir():
                    if item.is_dir() and not item.name.startswith("."):
                        unidades.append(item)
        return unidades

    @staticmethod
    def buscar_proyecto_en_usb(nombre: str) -> Optional[Path]:
        for usb in USBDetector.listar_unidades_extraibles():
            candidate = usb / "copy4me_backups" / nombre
            if candidate.exists():
                return candidate
        return None

# --- Seguridades y Utilidades ---
class SecurityUtils:
    @staticmethod
    def calcular_hash(archivo: Path, algoritmo="sha256") -> str:
        hash_func = hashlib.new(algoritmo)
        try:
            with open(archivo, 'rb') as f:
                while chunk := f.read(CHUNK_SIZE):
                    hash_func.update(chunk)
            return hash_func.hexdigest()
        except Exception as e:
            logger.warning(f"Error calculando hash {archivo}: {e}")
            return ""

    @staticmethod
    def calcular_hashes_paralelo(archivos: List[Path], max_workers: int = 4) -> Dict[Path, str]:
        resultados = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futuro_a_archivo = {executor.submit(SecurityUtils.calcular_hash, archivo): archivo for archivo in archivos}
            for futuro in futuro_a_archivo:
                archivo = futuro_a_archivo[futuro]
                try:
                    resultados[archivo] = futuro.result()
                except Exception:
                    resultados[archivo] = ""
        return resultados

    @staticmethod
    def cifrar_archivo(origen: Path, destino: Path, password: str, cancel_event: Optional[threading.Event] = None) -> bool:
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("PyCryptodome no instalada.")
        try:
            salt = os.urandom(16)
            key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000, dklen=32)
            iv = os.urandom(16)
            cipher = AES.new(key, AES.MODE_CBC, iv)

            with open(origen, 'rb') as f_in, open(destino, 'wb') as f_out:
                f_out.write(salt)
                f_out.write(iv)
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise InterruptedError("Operación cancelada")
                    chunk = f_in.read(CHUNK_SIZE)
                    if len(chunk) == CHUNK_SIZE:
                        f_out.write(cipher.encrypt(chunk))
                    else:
                        f_out.write(cipher.encrypt(pad(chunk, AES.block_size)))
                        break
            return True
        except Exception as e:
            logger.error(f"Error cifrando archivo {origen}: {e}")
            if destino.exists():
                try: destino.unlink()
                except Exception: pass
            return False

    @staticmethod
    def descifrar_archivo(origen: Path, destino: Path, password: str) -> bool:
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("PyCryptodome no instalada.")
        try:
            if origen.stat().st_size < 32:
                return False

            with open(origen, 'rb') as f_in, open(destino, 'wb') as f_out:
                salt = f_in.read(16)
                iv = f_in.read(16)
                key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000, dklen=32)
                cipher = AES.new(key, AES.MODE_CBC, iv)

                prev_chunk = None
                while True:
                    chunk = f_in.read(CHUNK_SIZE)
                    if not chunk:
                        if prev_chunk:
                            decrypted = cipher.decrypt(prev_chunk)
                            f_out.write(unpad(decrypted, AES.block_size))
                        break
                    if prev_chunk:
                        f_out.write(cipher.decrypt(prev_chunk))
                    prev_chunk = chunk
            return True
        except Exception as e:
            logger.error(f"Error descifrando archivo {origen}: {e}")
            if destino.exists():
                try: destino.unlink()
                except Exception: pass
            return False

# --- Motor de Sincronización ---
class SyncEngine:
    def __init__(self, config: ConfigManager):
        self.config = config
        self.max_backups = config.get_opcion("max_backups", MAX_BACKUPS)
        self.exclude_dirs = EXCLUDE_DIRS.copy()
        self.exclude_exts = EXCLUDE_EXTENSIONS.copy()
        for pat in config.get_opcion("excluir_patrones", []):
            self.exclude_exts.add(pat if pat.startswith('.') else f".{pat}")
            
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()

    def detener_operacion(self):
        self.cancel_event.set()

    def pausar_operacion(self, pausar: bool):
        if pausar:
            self.pause_event.clear()
        else:
            self.pause_event.set()

    def _excluir_archivo(self, ruta: Path) -> bool:
        if ruta.name.lower() in {d.lower() for d in self.exclude_dirs} or ruta.name in self.exclude_dirs:
            return True
        if ruta.suffix.lower() in self.exclude_exts:
            return True
        for pat in self.config.get_opcion("excluir_regex", []):
            try:
                if re.search(pat, str(ruta)):
                    return True
            except re.error:
                continue
        return False

    def _copiar_con_reintentos(self, src: Path, dst: Path, max_attempts=3, callback_log: Optional[Callable] = None) -> tuple[bool, str]:
        for attempt in range(max_attempts):
            if self.cancel_event.is_set():
                return False, "cancelado"
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    if src.stat().st_size == dst.stat().st_size and abs(src.stat().st_mtime - dst.stat().st_mtime) <= 2.0:
                        return True, "omitido"
                
                with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                    while True:
                        if self.cancel_event.is_set():
                            fdst.close()
                            if dst.exists():
                                try: dst.unlink()
                                except Exception: pass
                            return False, "cancelado"
                        
                        chunk = fsrc.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        fdst.write(chunk)
                
                shutil.copystat(src, dst)

                if self.config.get_opcion("verificar_hash", True):
                    if self.cancel_event.is_set():
                        return False, "cancelado"
                    h_src = SecurityUtils.calcular_hash(src)
                    h_dst = SecurityUtils.calcular_hash(dst)
                    if h_src and h_dst and h_src != h_dst:
                        raise ValueError("Incoincidencia de Hash SHA-256")

                return True, "copiado"

            except Exception as e:
                if self.cancel_event.is_set():
                    if dst.exists():
                        try: dst.unlink()
                        except Exception: pass
                    return False, "cancelado"

                logger.warning(f"Intento {attempt+1}/{max_attempts} fallido para {src.name}: {e}")
                if dst.exists():
                    try: dst.unlink()
                    except Exception: pass
                
                for _ in range(int((0.3 * (attempt + 1)) / 0.05)):
                    if self.cancel_event.is_set():
                        return False, "cancelado"
                    time.sleep(0.05)
                    
        return False, "error"

    def sincronizar(self, origen: Path, destino: Path, modo: str = "espejo",
                    callback_progreso: Optional[Callable] = None,
                    callback_log: Optional[Callable] = None) -> Tuple[int, int, int]:
        self.cancel_event.clear()
        self.pause_event.set()

        if callback_log:
            callback_log(f"🚀 Iniciando sincronización ({modo.upper()})")
            callback_log(f"📂 Origen:  {origen}")
            callback_log(f"🎯 Destino: {destino}")

        origen = Path(origen).resolve()
        destino = Path(destino).resolve()
        if destino == origen or origen in destino.parents:
            if callback_log:
                callback_log("❌ Error: La carpeta destino no puede estar dentro de la origen.")
            return 0, 0, 1
       
        archivos_origen = []
        directorios_origen = []
        
        for root, dirs, files in os.walk(origen):
            dirs[:] = [d for d in dirs if not self._excluir_archivo(Path(root) / d)]
            for d in dirs:
                r_dir = Path(root) / d
                if not self._excluir_archivo(r_dir):
                    directorios_origen.append(r_dir)

            for f in files:
                r = Path(root) / f
                if not self._excluir_archivo(r):
                    archivos_origen.append(r)

        carpetas_creadas = 0
        for dir_src in directorios_origen:
            if self.cancel_event.is_set(): break
            rel_dir = dir_src.relative_to(origen)
            dir_dst = destino / rel_dir
            try:
                if not dir_dst.exists():
                    dir_dst.mkdir(parents=True, exist_ok=True)
                    carpetas_creadas += 1
                else:
                    dir_dst.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Error creando carpeta {rel_dir}: {e}")

        total = len(archivos_origen)
        copiados, eliminados, errores = 0, 0, 0

        for idx, src in enumerate(archivos_origen, 1):
            if self.cancel_event.is_set():
                if callback_log: callback_log("🛑 Proceso cancelado.")
                break

            while not self.pause_event.is_set():
                if self.cancel_event.is_set(): break
                time.sleep(0.1)

            rel = src.relative_to(origen)
            dst = destino / rel
            if callback_progreso:
                callback_progreso(idx, total, copiados, eliminados, errores, str(rel))
            
            exito, estado = self._copiar_con_reintentos(src, dst, callback_log=callback_log)
            if exito:
                if estado == "copiado":
                    copiados += 1
                    if callback_log: callback_log(f"➕ Copiado: {rel}")
            else:
                if estado != "cancelado":
                    errores += 1
                    if callback_log: callback_log(f"❌ Error al copiar: {rel}")

        if modo in ("espejo", "bidireccional") and destino.exists() and not self.cancel_event.is_set():
            for root, _, files in os.walk(destino):
                if self.cancel_event.is_set(): break
                for f in files:
                    r_dst = Path(root) / f
                    rel = r_dst.relative_to(destino)
                    src_corr = origen / rel

                    if not src_corr.exists():
                        if modo == "espejo":
                            try:
                                r_dst.unlink()
                                eliminados += 1
                                if callback_log: callback_log(f"🗑️ Eliminado de destino (Espejo): {rel}")
                            except Exception:
                                errores += 1
                        elif modo == "bidireccional":
                            exito, estado = self._copiar_con_reintentos(r_dst, src_corr)
                            if exito and estado == "copiado":
                                copiados += 1
                                if callback_log: callback_log(f"🔄 Recuperado a Origen: {rel}")

        if callback_log:
            callback_log(f"✅ Finalizado. Copiados: {copiados}, Eliminados: {eliminados}, Errores: {errores}")
        return copiados, eliminados, errores

    def _rotar_backups(self, carpeta: Path, callback_log: Optional[Callable] = None):
        backups = sorted(
            [f for f in carpeta.iterdir() if f.is_file() and (f.name.endswith(".zip") or f.name.endswith(".zip.enc"))],
            key=lambda x: x.stat().st_mtime
        )
        while len(backups) >= self.max_backups:
            antiguo = backups.pop(0)
            try:
                antiguo.unlink()
                if callback_log: callback_log(f"♻️ Rotación: Eliminado antiguo {antiguo.name}")
            except Exception as e:
                logger.warning(f"Error rotando backup {antiguo}: {e}")

    def crear_backup_zip(self, carpeta_origen: Path, nombre_proyecto: str,
                         password: Optional[str] = None,
                         compression_level: int = DEFAULT_COMPRESSION_LEVEL,
                         callback_log: Optional[Callable] = None) -> Optional[Path]:
        if not carpeta_origen.exists():
            if callback_log: callback_log("❌ Error: Origen no existe.")
            return None

        proyecto_sano = limpiar_nombre_ruta(nombre_proyecto)
        host_sano = limpiar_nombre_ruta(socket.gethostname())

        carpeta_backups = DIR_BACKUPS / proyecto_sano
        carpeta_backups.mkdir(parents=True, exist_ok=True)
        self._rotar_backups(carpeta_backups, callback_log)

        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_zip = f"backup_{proyecto_sano}_{host_sano}_{fecha}.zip"
        ruta_zip = carpeta_backups / nombre_zip

        if callback_log: callback_log(f"📦 Generando paquete comprimido: {ruta_zip.name}")

        try:
            with zipfile.ZipFile(ruta_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=compression_level) as zf:
                manifest = {"version": VERSION, "fecha": datetime.now().isoformat(), "origen": str(carpeta_origen), "archivos": {}}
                archivos_a_procesar = []
                for root, dirs, files in os.walk(carpeta_origen):
                    dirs[:] = [d for d in dirs if not self._excluir_archivo(Path(root) / d)]
                    for f in files:
                        p = Path(root) / f
                        if not self._excluir_archivo(p): archivos_a_procesar.append(p)

                hashes = SecurityUtils.calcular_hashes_paralelo(archivos_a_procesar) if self.config.get_opcion("verificar_hash", True) else {}

                for r in archivos_a_procesar:
                    arcname = str(r.relative_to(carpeta_origen))
                    zf.write(r, arcname)
                    manifest["archivos"][arcname] = {"size": r.stat().st_size, "hash": hashes.get(r, "")}

                zf.writestr("manifest_backup.json", json.dumps(manifest, indent=4))

            if password and CRYPTO_AVAILABLE:
                ruta_cifrada = ruta_zip.with_suffix(".zip.enc")
                if SecurityUtils.cifrar_archivo(ruta_zip, ruta_cifrada, password):
                    ruta_zip.unlink()
                    ruta_zip = ruta_cifrada
                    if callback_log: callback_log("🔒 Paquete cifrado con AES-256")
                else: raise RuntimeError("Error cifrando con AES.")

            if callback_log: callback_log(f"✅ Backup .ZIP listo: {ruta_zip.name}")
            return ruta_zip
        except Exception as e:
            logger.error(f"Error creando backup zip: {e}")
            if ruta_zip.exists():
                try: ruta_zip.unlink()
                except Exception: pass
            return None

    def restaurar_desde_backup(self, ruta_zip: Path, destino: Path, password: Optional[str] = None,
                               callback_log: Optional[Callable] = None) -> bool:
        if callback_log: callback_log(f"📥 Restaurando paquete: {ruta_zip.name} -> {destino}")
        temp_zip_file = None
        target_zip = ruta_zip

        if ruta_zip.suffix == ".enc":
            if not CRYPTO_AVAILABLE: return False
            try:
                temp_zip_file = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
                temp_zip_path = Path(temp_zip_file.name)
                temp_zip_file.close()
                if not SecurityUtils.descifrar_archivo(ruta_zip, temp_zip_path, password):
                    raise ValueError("Clave incorrecta o backup dañado.")
                target_zip = temp_zip_path
            except Exception as e:
                if callback_log: callback_log(f"❌ Error de descifrado: {e}")
                return False

        try:
            destino_dir = destino.resolve()
            destino_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target_zip, 'r') as zf:
                for member in zf.infolist():
                    if member.filename == "manifest_backup.json" or member.is_dir(): continue
                    target_path = (destino_dir / member.filename).resolve()
                    if not str(target_path).startswith(str(destino_dir)): continue
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
            if callback_log: callback_log("✅ Restauración completada con éxito.")
            return True
        except Exception as e:
            logger.error(f"Error restaurando: {e}")
            return False
        finally:
            if temp_zip_file and Path(temp_zip_file.name).exists():
                try: Path(temp_zip_file.name).unlink()
                except Exception: pass

# --- NUEVO DISEÑO DIVIDIDO (SPLIT-SCREEN INTERFACE) ---
if GUI_AVAILABLE:
    class Copy4MeGUI(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"{APP_NAME} Split-Screen Engine ({VERSION})")
            self.geometry("1280x850")
            self.minsize(1050, 750)

            self.config = ConfigManager()
            self.engine = SyncEngine(self.config)
            self.ui_queue = queue.Queue()
            self.is_paused = False

            self._configurar_estilos()
            self._crear_interfaz_dividida()
            self._refresh_all()
            self.after(100, self._procesar_cola)
            self._detectar_usb()

        def _configurar_estilos(self):
            self.style = ttk.Style()
            self.style.theme_use('clam')
            self.configure(bg="#f8fafc")

            # Fuentes más grandes y legibles
            self.font_title = ("Segoe UI", 12, "bold")
            self.font_sub = ("Segoe UI", 10, "italic")
            self.font_bold = ("Segoe UI", 11, "bold")
            self.font_norm = ("Segoe UI", 11)
            self.font_big_btn = ("Segoe UI", 11, "bold")

            # Marcos e interfaz general más amplia
            self.style.configure('TLabelframe', background="#ffffff", relief="solid", borderwidth=1, bordercolor="#cbd5e1")
            self.style.configure('TLabelframe.Label', font=self.font_title, foreground="#0f172a", background="#ffffff")
            self.style.configure('TFrame', background="#f8fafc")
            self.style.configure('TLabel', background="#ffffff", foreground="#334155", font=self.font_norm)
            self.style.configure('TRadiobutton', background="#ffffff", font=self.font_norm)
            self.style.configure('TCheckbutton', background="#ffffff", font=self.font_norm)
            
            # Altura y padding para botones, selectores y entradas de texto
            self.style.configure('TButton', font=self.font_norm, padding=6)
            self.style.configure('TCombobox', font=self.font_norm, padding=4)
            self.style.configure('TEntry', font=self.font_norm, padding=4)

        def _crear_interfaz_dividida(self):
            # Barra Superior Herramientas
            top_bar = ttk.Frame(self, padding=(15, 8))
            top_bar.pack(fill=tk.X)
            
            ttk.Label(top_bar, text=f"📂 {APP_NAME} Enterprise", font=self.font_title, foreground="#0f172a").pack(side=tk.LEFT)
            
            btn_tools = ttk.Frame(top_bar)
            btn_tools.pack(side=tk.RIGHT)
            
            ttk.Button(btn_tools, text="🔍 Historial Backups", command=self._abrir_ventana_historial).pack(side=tk.LEFT, padx=3)
            ttk.Button(btn_tools, text="⚙️ Ajustes", command=self._abrir_ventana_ajustes).pack(side=tk.LEFT, padx=3)
            ttk.Button(btn_tools, text="🔌 Refrescar USB", command=self._refresh_all).pack(side=tk.LEFT, padx=3)

            # --- PANEL DIVIDIDO PRINCIPAL ---
            main_split = ttk.Frame(self, padding=(10, 0, 10, 5))
            main_split.pack(fill=tk.BOTH, expand=False)
            main_split.columnconfigure(0, weight=4) # Lado Origen
            main_split.columnconfigure(1, weight=2) # Centro (Botones Flechas)
            main_split.columnconfigure(2, weight=4) # Lado Destino

            # ---------------- LADO IZQUIERDO: ORIGEN (PC) ----------------
            card_origen = ttk.LabelFrame(main_split, text=" 💻 CARPETA ORIGEN (PC / LOCAL) ", padding=12)
            card_origen.grid(row=0, column=0, sticky="nsew", padx=5)

            ttk.Label(card_origen, text="Seleccionar Perfil Guardado o Configurado:").pack(anchor=tk.W)
            self.combo_perfiles = ttk.Combobox(card_origen, state="readonly")
            self.combo_perfiles.pack(fill=tk.X, pady=5)
            self.combo_perfiles.bind("<<ComboboxSelected>>", self._on_perfil_selected)

            row_btn_orig = ttk.Frame(card_origen)
            row_btn_orig.pack(fill=tk.X, pady=3)
            ttk.Button(row_btn_orig, text="📁 Explorar PC...", command=self._browse_origen).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            ttk.Button(row_btn_orig, text="✏️ Renombrar", command=self._renombrar_perfil).pack(side=tk.LEFT, padx=2)
            ttk.Button(row_btn_orig, text="❌ Borrar", command=self._eliminar_perfil).pack(side=tk.RIGHT, padx=2)

            ttk.Label(card_origen, text="Ruta de Origen Seleccionada:", font=self.font_bold).pack(anchor=tk.W, pady=(10, 2))
            self.entry_ruta_origen = ttk.Entry(card_origen)
            self.entry_ruta_origen.pack(fill=tk.X)

            # ---------------- CENTRO: BOTONES ACCIÓN CON FLECHAS ----------------
            card_centro = ttk.Frame(main_split, padding=5)
            card_centro.grid(row=0, column=1, sticky="nsew")

            ttk.Label(card_centro, text="Modo de Operación:", font=self.font_bold).pack(pady=(5, 2))
            self.var_modo = tk.StringVar(value="incremental")
            combo_modo = ttk.Combobox(card_centro, textvariable=self.var_modo, values=["incremental", "espejo", "bidireccional"], state="readonly", width=14)
            combo_modo.pack(pady=(0, 10))

            btn_copiar_der = ttk.Button(card_centro, text=" RESPALDAR ➡️\n  (Origen ➔ Destino)", command=self._ejecutar_respaldo_derecha)
            btn_copiar_der.pack(fill=tk.X, pady=6)

            btn_copiar_izq = ttk.Button(card_centro, text=" ⬅️ RESTAURAR\n  (Destino ➔ Origen)", command=self._ejecutar_restauracion_izquierda)
            btn_copiar_izq.pack(fill=tk.X, pady=6)

            box_opciones = ttk.LabelFrame(card_centro, text=" Opciones ", padding=5)
            box_opciones.pack(fill=tk.X, pady=5)

            self.var_hacer_zip = tk.BooleanVar(value=False)
            ttk.Checkbutton(box_opciones, text="📦 Crear .ZIP", variable=self.var_hacer_zip).pack(anchor=tk.W)

            self.var_cifrar = tk.BooleanVar(value=False)
            ttk.Checkbutton(box_opciones, text="🔒 Cifrar AES", variable=self.var_cifrar).pack(anchor=tk.W)

            # ---------------- LADO DERECHO: DESTINO (USB / PC) ----------------
            card_destino = ttk.LabelFrame(main_split, text=" 🔌 CARPETA DESTINO (USB / RESPALDO) ", padding=12)
            card_destino.grid(row=0, column=2, sticky="nsew", padx=5)

            ttk.Label(card_destino, text="Proyectos / Unidades USB Encontradas:").pack(anchor=tk.W)
            self.combo_proyectos_usb = ttk.Combobox(card_destino)
            self.combo_proyectos_usb.pack(fill=tk.X, pady=5)
            self.combo_proyectos_usb.bind("<<ComboboxSelected>>", self._on_destino_selected)

            row_btn_dest = ttk.Frame(card_destino)
            row_btn_dest.pack(fill=tk.X, pady=3)
            ttk.Button(row_btn_dest, text="📁 Explorar Destino...", command=self._browse_destino).pack(fill=tk.X, padx=2)

            ttk.Label(card_destino, text="Ruta de Destino Seleccionada:", font=self.font_bold).pack(anchor=tk.W, pady=(10, 2))
            self.entry_ruta_destino = ttk.Entry(card_destino)
            self.entry_ruta_destino.pack(fill=tk.X)

            # ---------------- ZONA INFERIOR: LOGS Y CONTROLES ----------------
            bottom_panel = ttk.Frame(self, padding=(10, 5))
            bottom_panel.pack(fill=tk.BOTH, expand=True)

            status_frame = ttk.LabelFrame(bottom_panel, text=" Estado y Progreso de la Operación ", padding=8)
            status_frame.pack(fill=tk.X, pady=(0, 5))

            self.progress_bar = ttk.Progressbar(status_frame, orient="horizontal", mode="determinate")
            self.progress_bar.pack(fill=tk.X, pady=2)

            ctrl_row = ttk.Frame(status_frame)
            ctrl_row.pack(fill=tk.X, pady=2)

            self.lbl_progreso = ttk.Label(ctrl_row, text="Estado: En espera", font=self.font_bold)
            self.lbl_progreso.pack(side=tk.LEFT)

            self.btn_cancelar = ttk.Button(ctrl_row, text="🛑 Cancelar", command=self._cancelar_tarea, state="disabled")
            self.btn_cancelar.pack(side=tk.RIGHT, padx=2)

            self.btn_pausa = ttk.Button(ctrl_row, text="⏸️ Pausar", command=self._toggle_pausa, state="disabled")
            self.btn_pausa.pack(side=tk.RIGHT, padx=2)

            self.lbl_archivo_actual = ttk.Label(status_frame, text="", font=self.font_sub)
            self.lbl_archivo_actual.pack(anchor=tk.W)

            log_frame = ttk.LabelFrame(bottom_panel, text=" Consola de Registros y Logs en Vivo ", padding=5)
            log_frame.pack(fill=tk.BOTH, expand=True)

            self.log_text = scrolledtext.ScrolledText(
                log_frame, height=10, state='disabled',
                bg='#0f172a', fg='#38bdf8', font=("Consolas", 11)
            )
            self.log_text.pack(fill=tk.BOTH, expand=True)

        # --- Manejo de la Cola de Interfaz ---
        def _procesar_cola(self):
            try:
                while True:
                    task, args = self.ui_queue.get_nowait()
                    if task == "log":
                        self._append_log(args[0])
                    elif task == "progress":
                        self._actualizar_progreso(*args)
                    elif task == "set_determinate":
                        self.progress_bar.stop()
                        self.progress_bar.config(mode="determinate", maximum=args[0] if args[0] > 0 else 1)
                    elif task == "status_text":
                        self.lbl_archivo_actual.config(text=args[0])
                    elif task == "stop_progress":
                        self.progress_bar.stop()
                        self.progress_bar['value'] = 0
                        self.lbl_progreso.config(text="Estado: En espera")
                        self.lbl_archivo_actual.config(text="")
                        self.btn_pausa.config(state="disabled", text="⏸️ Pausar")
                        self.btn_cancelar.config(state="disabled")
                        self.is_paused = False
                    elif task == "msgbox":
                        messagebox.showinfo(args[0], args[1])
                    elif task == "msgbox_error":
                        messagebox.showerror(args[0], args[1])
                    elif task == "refresh":
                        self._refresh_all()
                    elif task == "mostrar_reporte_detallado":
                        self._mostrar_ventana_reporte(args[0], args[1])
                    self.ui_queue.task_done()
            except queue.Empty:
                pass
            finally:
                self.after(100, self._procesar_cola)

        def log_gui(self, texto: str):
            self.ui_queue.put(("log", (texto,)))

        def _append_log(self, texto: str):
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {texto}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')

        def _actualizar_progreso(self, idx, total, copiados, eliminados, errores, archivo_actual):
            self.progress_bar['maximum'] = total if total > 0 else 1
            self.progress_bar['value'] = idx
            porcentaje = int((idx / total) * 100) if total > 0 else 0
            self.lbl_progreso.config(
                text=f"Procesando: {porcentaje}% ({idx}/{total})  |  Copiados: {copiados}  |  Eliminados: {eliminados}  |  Errores: {errores}"
            )
            if archivo_actual:
                self.lbl_archivo_actual.config(text=f"Archivo actual: {archivo_actual}")

        def _toggle_pausa(self):
            if not self.is_paused:
                self.engine.pausar_operacion(True)
                self.btn_pausa.config(text="▶️ Reanudar")
                self.lbl_progreso.config(text="Estado: PAUSADO por el usuario")
                self.is_paused = True
            else:
                self.engine.pausar_operacion(False)
                self.btn_pausa.config(text="⏸️ Pausar")
                self.is_paused = False

        def _cancelar_tarea(self):
            if messagebox.askyesno("Confirmar", "¿Desea detener la operación en curso?"):
                self.engine.detener_operacion()

        # --- Eventos Selección y Exploración ---
        def _on_perfil_selected(self, event):
            nombre = self.combo_perfiles.get()
            perfil = self.config.get_perfil(nombre)
            if perfil:
                self.entry_ruta_origen.delete(0, tk.END)
                self.entry_ruta_origen.insert(0, perfil.get('ruta_local', ''))
                
                ruta_dest = perfil.get('ruta_destino', '')
                if not ruta_dest:
                    dest_usb = USBDetector.buscar_proyecto_en_usb(nombre)
                    ruta_dest = str(dest_usb) if dest_usb else str(DIR_BACKUPS / nombre / "MASTER")
                
                self.entry_ruta_destino.delete(0, tk.END)
                self.entry_ruta_destino.insert(0, ruta_dest)

        def _on_destino_selected(self, event):
            nombre = self.combo_proyectos_usb.get()
            if not nombre: return
            cand = USBDetector.buscar_proyecto_en_usb(nombre)
            if cand:
                self.entry_ruta_destino.delete(0, tk.END)
                self.entry_ruta_destino.insert(0, str(cand))

        def _browse_origen(self):
            # 'parent=self' vincula el diálogo a la ventana principal solucionando
            # problemas de tamaño reducido o posicionamiento.
            folder = filedialog.askdirectory(
                parent=self, 
                title="Selecciona Carpeta de Origen"
            )
            if folder:
                self.entry_ruta_origen.delete(0, tk.END)
                self.entry_ruta_origen.insert(0, folder)
                nombre_sano = limpiar_nombre_ruta(Path(folder).name)
                self.config.set_perfil(nombre_sano, Path(folder))
                self._refresh_all()
                self.combo_perfiles.set(nombre_sano)

        def _browse_destino(self):
            folder = filedialog.askdirectory(
                parent=self, 
                title="Selecciona Carpeta de Destino"
            )
            if folder:
                self.entry_ruta_destino.delete(0, tk.END)
                self.entry_ruta_destino.insert(0, folder)

        def _eliminar_perfil(self):
            nombre = self.combo_perfiles.get()
            if nombre and messagebox.askyesno("Borrar Perfil", f"¿Eliminar el perfil '{nombre}'?"):
                self.config.delete_perfil(nombre)
                self._refresh_all()

        # --- LÓGICA DE RESPALDO (FLECHA DERECHA) ---
        def _ejecutar_respaldo_derecha(self):
            origen_str = self.entry_ruta_origen.get().strip()
            destino_str = self.entry_ruta_destino.get().strip()

            if not origen_str or not Path(origen_str).exists():
                messagebox.showerror("Error", "Seleccione una carpeta de origen válida en el panel izquierdo.")
                return

            if not destino_str:
                messagebox.showerror("Error", "Seleccione una carpeta de destino en el panel derecho.")
                return

            origen = Path(origen_str)
            destino = Path(destino_str)
            nombre = self.combo_perfiles.get() or origen.name

            modo = self.var_modo.get()
            hacer_zip = self.var_hacer_zip.get()
            cifrar = self.var_cifrar.get()
            password = None

            if cifrar:
                if not hacer_zip:
                    messagebox.showwarning("Atención", "El cifrado AES requiere tener marcada la opción 'Crear .ZIP'.")
                    return
                if not CRYPTO_AVAILABLE:
                    messagebox.showerror("Error", "Librería PyCryptodome no instalada.")
                    return
                password = simpledialog.askstring("Clave de Cifrado", "Introduce contraseña AES-256:", show='*')
                if not password: return

            if not messagebox.askyesno("Confirmar Respaldo", f"¿Iniciar Respaldo?\n\n• Modo: {modo.upper()}\n• Origen: {origen}\n• Destino: {destino}"):
                return

            self.btn_pausa.config(state="normal")
            self.btn_cancelar.config(state="normal")
            self.progress_bar.stop()
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(10)

            threading.Thread(
                target=self._worker_respaldo,
                args=(nombre, origen, destino, modo, password, self.config.get_opcion("compresion", 6), True, hacer_zip),
                daemon=True
            ).start()

        def _worker_respaldo(self, nombre, origen, destino, modo, password, compression_level, hacer_directo, hacer_zip):
            cambios_detallados = []
            def cb_progreso(idx, total, cop, del_, err, arch):
                self.ui_queue.put(("set_determinate", (total,)))
                self.ui_queue.put(("progress", (idx, total, cop, del_, err, arch)))

            def cb_log_custom(msg):
                self.log_gui(msg)
                self.ui_queue.put(("status_text", (msg,)))
                if any(icon in msg for icon in ["➕", "📁", "🗑️", "🔄", "❌"]):
                    cambios_detallados.append(msg)

            try:
                copiados, eliminados, errores = 0, 0, 0
                if hacer_directo:
                    es_valido, mensaje = validar_espacio_disponible(origen, destino)
                    if not es_valido:
                        self.ui_queue.put(("msgbox_error", ("Espacio Insuficiente", mensaje)))
                        return

                    copiados, eliminados, errores = self.engine.sincronizar(
                        origen, destino, modo, callback_progreso=cb_progreso, callback_log=cb_log_custom
                    )
                    self.config.set_perfil(nombre, origen, ruta_destino=destino)

                if hacer_zip and not self.engine.cancel_event.is_set():
                    self.engine.crear_backup_zip(origen, nombre, password, compression_level, cb_log_custom)

                header = f"Respaldo finalizado en '{nombre}'\nArchivos copiados: {copiados} | Eliminados: {eliminados} | Errores: {errores}"
                detalle = "\n".join(cambios_detallados) if cambios_detallados else "Archivos sincronizados sin cambios pendientes."
                self.ui_queue.put(("mostrar_reporte_detallado", (header, detalle)))

            except Exception as e:
                self.ui_queue.put(("msgbox_error", ("Error Crítico", f"Error durante el respaldo:\n{e}")))
            finally:
                self.ui_queue.put(("stop_progress", None))
                self.ui_queue.put(("refresh", None))

        # --- LÓGICA DE RESTAURACIÓN (FLECHA IZQUIERDA) ---
        def _ejecutar_restauracion_izquierda(self):
            origen_usb_str = self.entry_ruta_destino.get().strip()
            destino_pc_str = self.entry_ruta_origen.get().strip()

            if not origen_usb_str or not Path(origen_usb_str).exists():
                messagebox.showerror("Error", "Seleccione una carpeta válida en el panel derecho (Destino/USB).")
                return

            if not destino_pc_str:
                messagebox.showerror("Error", "Seleccione una carpeta de destino válida en el panel izquierdo (PC).")
                return

            origen_usb = Path(origen_usb_str)
            destino_pc = Path(destino_pc_str)

            if messagebox.askyesno("Restaurar a PC", f"¿Restaurar datos desde USB a PC?\n\n• Desde: {origen_usb}\n• Hacia: {destino_pc}"):
                self.btn_pausa.config(state="normal")
                self.btn_cancelar.config(state="normal")
                threading.Thread(target=self._worker_restauracion, args=(origen_usb, destino_pc), daemon=True).start()

        def _worker_restauracion(self, origen, destino):
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(10)
            try:
                self.log_gui("📊 Verificando espacio en PC para restauración...")
                es_valido, mensaje = validar_espacio_disponible(origen, destino)
                if not es_valido:
                    self.ui_queue.put(("msgbox_error", ("Espacio Insuficiente en PC", mensaje)))
                    return

                copiados, eliminados, errores = self.engine.sincronizar(origen, destino, modo="incremental", callback_log=self.log_gui)
                self.ui_queue.put(("msgbox", ("Restauración Completada", f"Restauración terminada.\nArchivos copiados: {copiados}\nErrores: {errores}")))
            except Exception as e:
                self.ui_queue.put(("msgbox_error", ("Error", f"Fallo al restaurar: {e}")))
            finally:
                self.ui_queue.put(("stop_progress", None))
                self.ui_queue.put(("refresh", None))

        # --- MODALES Y VENTANAS SECUNDARIAS ---
        def _abrir_ventana_historial(self):
            v = tk.Toplevel(self)
            v.title("Historial de Copias Comprimidas (.ZIP)")
            v.geometry("700x400")
            
            tree = ttk.Treeview(v, columns=("Fecha", "Tamaño", "Formato"), show="tree headings")
            tree.heading("#0", text="Proyecto / Archivo Backup")
            tree.heading("Fecha", text="Fecha de Creación")
            tree.heading("Tamaño", text="Tamaño")
            tree.heading("Formato", text="Estado Cifrado")
            tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            if DIR_BACKUPS.exists():
                for p in sorted(DIR_BACKUPS.iterdir()):
                    if p.is_dir():
                        node = tree.insert("", tk.END, text=p.name, open=True)
                        for b in sorted(p.glob("backup_*"), key=lambda x: x.stat().st_mtime, reverse=True):
                            if b.is_file():
                                fecha = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                                tam = formatear_tamano(b.stat().st_size)
                                est = "🔒 Cifrado AES" if b.suffix == ".enc" else "📦 ZIP Estándar"
                                tree.insert(node, tk.END, text=b.name, values=(fecha, tam, est))

        def _abrir_ventana_ajustes(self):
            v = tk.Toplevel(self)
            v.title("Ajustes Generales del Sistema")
            v.geometry("500x380")

            f = ttk.Frame(v, padding=15)
            f.pack(fill=tk.BOTH, expand=True)

            var_hash = tk.BooleanVar(value=self.config.get_opcion("verificar_hash", True))
            ttk.Checkbutton(f, text="Verificación estricta de integridad (SHA-256)", variable=var_hash,
                            command=lambda: self.config.set_opcion("verificar_hash", var_hash.get())).pack(anchor=tk.W, pady=5)

            ttk.Label(f, text="Nivel Compresión ZIP (0-9):").pack(anchor=tk.W, pady=(10, 2))
            spin_comp = tk.Spinbox(f, from_=0, to=9, width=5)
            spin_comp.delete(0, tk.END)
            spin_comp.insert(0, str(self.config.get_opcion("compresion", 6)))
            spin_comp.pack(anchor=tk.W)
            spin_comp.bind("<FocusOut>", lambda e: self.config.set_opcion("compresion", int(spin_comp.get())))

            ttk.Label(f, text="Extensiones Excluidas (sep. por comas):").pack(anchor=tk.W, pady=(10, 2))
            ent_excl = ttk.Entry(f)
            ent_excl.insert(0, ", ".join(self.config.get_opcion("excluir_patrones", [])))
            ent_excl.pack(fill=tk.X)

            ttk.Button(f, text="Guardar Exclusiones", command=lambda: self.config.set_opcion("excluir_patrones", [x.strip() for x in ent_excl.get().split(',') if x.strip()])).pack(anchor=tk.W, pady=8)

        def _refresh_all(self):
            perfiles = self.config._data.get("perfiles", {})
            self.combo_perfiles['values'] = sorted(perfiles.keys())

            proyectos_usb = set()
            for usb in USBDetector.listar_unidades_extraibles():
                backup_dir = usb / "copy4me_backups"
                if backup_dir.exists():
                    for d in backup_dir.iterdir():
                        if d.is_dir(): proyectos_usb.add(d.name)

            self.combo_proyectos_usb['values'] = sorted(proyectos_usb)

        def _detectar_usb(self):
            usb = USBDetector.listar_unidades_extraibles()
            if usb:
                self.log_gui(f"🔌 Unidades externas conectadas: {', '.join(str(u) for u in usb)}")
            else:
                self.log_gui("ℹ️ No se detectaron USBs al iniciar.")

        def _mostrar_ventana_reporte(self, encabezado: str, detalle: str):
            v = tk.Toplevel(self)
            v.title("Reporte de Cambios Realizados")
            v.geometry("700x450")

            ttk.Label(v, text=encabezado, font=self.font_bold).pack(anchor=tk.W, padx=15, pady=10)
            txt = scrolledtext.ScrolledText(v, wrap=tk.WORD, bg="#0f172a", fg="#34d399", font=("Consolas", 9))
            txt.insert(tk.END, detalle)
            txt.config(state='disabled')
            txt.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 10))

        def _renombrar_perfil(self):
            nombre_actual = self.combo_perfiles.get()
            if not nombre_actual:
                messagebox.showwarning("Atención", "Selecciona primero un perfil para renombrar.")
                return

            nuevo_nombre = simpledialog.askstring(
                "Renombrar Perfil", 
                f"Introduce el nuevo nombre para '{nombre_actual}':",
                parent=self
            )
            
            if nuevo_nombre:
                nuevo_nombre_sano = limpiar_nombre_ruta(nuevo_nombre)
                if nuevo_nombre_sano == nombre_actual:
                    return
                
                # Obtener los datos del perfil actual
                perfil_data = self.config.get_perfil(nombre_actual)
                if perfil_data:
                    # Guardar con el nuevo nombre y eliminar el antiguo
                    self.config._data["perfiles"][nuevo_nombre_sano] = perfil_data
                    self.config.delete_perfil(nombre_actual)
                    self._refresh_all()
                    self.combo_perfiles.set(nuevo_nombre_sano)
                    messagebox.showinfo("Éxito", f"Perfil renombrado a '{nuevo_nombre_sano}'.")    

def modo_tui():
    config = ConfigManager()
    engine = SyncEngine(config)

    def log_tui(msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    while True:
        print("\n" + "="*55)
        print(f"   💻 {APP_NAME} Enterprise - Modo Terminal ({VERSION})")
        print("="*55)
        print(" 1. ➡️ Sincronizar / Respaldar (Origen -> Destino)")
        print(" 2. ⬅️ Restaurar Datos (Destino -> Origen)")
        print(" 3. 📦 Crear Backup .ZIP Comprimido/Cifrado")
        print(" 4. 📂 Listar y Gestionar Perfiles Guardados")
        print(" 5. 🔌 Detectar Unidades USB")
        print(" 6. ⚙️ Configuración / Opciones")
        print(" 0. ❌ Salir")
        print("-" * 55)

        opc = input("Selecciona una opción [0-6]: ").strip()

        if opc == "1":
            print("\n--- RESPALDO DE DATOS ---")
            origen = input("Ruta Carpeta ORIGEN (PC): ").strip()
            destino = input("Ruta Carpeta DESTINO (USB/Disco): ").strip()

            if not origen or not Path(origen).exists():
                print("❌ La ruta origen no existe.")
                continue
            if not destino:
                print("❌ Debe ingresar una ruta destino.")
                continue

            print("\nModos: [1] Incremental | [2] Espejo | [3] Bidireccional")
            m_opc = input("Selecciona modo (por defecto 1): ").strip()
            modo = "espejo" if m_opc == "2" else ("bidireccional" if m_opc == "3" else "incremental")

            is_valid, msg = validar_espacio_disponible(Path(origen), Path(destino))
            if not is_valid:
                print(f"❌ Error: {msg}")
                continue

            engine.sincronizar(Path(origen), Path(destino), modo=modo, callback_log=log_tui)

            nombre_p = Path(origen).name
            config.set_perfil(nombre_p, Path(origen), Path(destino))

        elif opc == "2":
            print("\n--- RESTAURACIÓN DE DATOS ---")
            origen = input("Ruta Carpeta ORIGEN (Desde USB): ").strip()
            destino = input("Ruta Carpeta DESTINO (Hacia PC): ").strip()

            if not origen or not Path(origen).exists():
                print("❌ Ruta de respaldo origen no válida.")
                continue

            is_valid, msg = validar_espacio_disponible(Path(origen), Path(destino))
            if not is_valid:
                print(f"❌ Error: {msg}")
                continue

            engine.sincronizar(Path(origen), Path(destino), modo="incremental", callback_log=log_tui)

        elif opc == "3":
            print("\n--- BACKUP COMPRIMIDO (.ZIP) ---")
            origen = input("Ruta Carpeta a Comprimir: ").strip()
            if not origen or not Path(origen).exists():
                print("❌ Ruta no válida.")
                continue

            nombre = input("Nombre del Proyecto: ").strip() or Path(origen).name
            cifrar = input("¿Desea cifrar con AES-256? (s/n): ").strip().lower() == 's'
            password = None

            if cifrar:
                if not CRYPTO_AVAILABLE:
                    print("❌ PyCryptodome no instalada.")
                    continue
                password = input("Introduce Contraseña: ").strip()

            comp = config.get_opcion("compresion", 6)
            engine.crear_backup_zip(Path(origen), nombre, password=password, compression_level=comp, callback_log=log_tui)

        elif opc == "4":
            print("\n--- PERFILES CONFIGURADOS ---")
            perfiles = config._data.get("perfiles", {})
            if not perfiles:
                print("No hay perfiles guardados.")
            else:
                for k, v in perfiles.items():
                    print(f"• [{k}] Local: {v.get('ruta_local')} -> Destino: {v.get('ruta_destino')}")

        elif opc == "5":
            print("\n--- UNIDADES EXTRAÍBLES ---")
            usbs = USBDetector.listar_unidades_extraibles()
            if not usbs:
                print("No se encontraron unidades externas conectadas.")
            else:
                for u in usbs:
                    print(f"🔌 Unidad: {u}")

        elif opc == "6":
            print("\n--- CONFIGURACIÓN ---")
            hash_val = config.get_opcion("verificar_hash", True)
            print(f"1. Verificación SHA-256 actual: {hash_val}")
            print(f"2. Nivel de Compresión actual: {config.get_opcion('compresion', 6)}")
            
            sub = input("¿Desea cambiar la verificación SHA-256? (s/n): ").strip().lower()
            if sub == 's':
                config.set_opcion("verificar_hash", not hash_val)
                print(f"Verificación SHA-256 cambiada a: {not hash_val}")

        elif opc == "0":
            print("👋 Saliendo de Copy4Me...")
            break

if __name__ == "__main__":
    # Si Tkinter está instalado y hay entorno gráfico, arranca GUI; si no, lanza TUI automáticamente
    if GUI_AVAILABLE and os.environ.get('DISPLAY', '') != '' or platform.system() == "Windows":
        try:
            app = Copy4MeGUI()
            app.tk.call('tk', 'scaling', 2)  # Ajusta el número (1.5, 1.8, 2.0) según el tamaño deseado
            app.mainloop()
        except Exception:
            modo_tui()
    else:
        modo_tui()