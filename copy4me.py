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
VERSION = "5.4"
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

# --- Funciones de Directorio Base ---
def get_base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def get_user_app_dir() -> Path:
    sistema = platform.system()
    if sistema == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sistema == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

    app_dir = base / APP_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir

CONFIG_DIR = get_user_app_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
DIR_BACKUPS = CONFIG_DIR / "copy4me_backups"

# Validación de espacio disponible
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

# --- Calcular tamaños ---
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
                        if drive_type in (2, 3) and not letter.startswith("C"):
                            if Path(letter).exists():
                                unidades.append(Path(letter))
            except Exception as e:
                logger.warning(f"Error escaneando unidades en Windows: {e}")

        elif sistema == "Linux":
            user = os.getenv("USER") or getpass.getuser()
            media_paths = [
                Path(f"/media/{user}"), 
                Path(f"/run/media/{user}"), 
                Path("/mnt"), 
                Path("/media")
            ]
            for base in media_paths:
                if base.exists():
                    try:
                        for item in base.iterdir():
                            if item.is_dir() and item.name not in ["cdrom", "floppy"]:
                                unidades.append(item)
                    except PermissionError:
                        continue

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

        origen = Path(origen).resolve()
        destino = Path(destino).resolve()

        if destino == origen or origen in destino.parents:
            if callback_log: 
                callback_log("❌ Error: La carpeta destino no puede estar dentro de la origen.")
            return 0, 0, 1

        if callback_log:
            callback_log(f"🚀 Iniciando sincronización ({modo.upper()})")
            callback_log(f"📂 Carpeta A (PC): {origen}")
            callback_log(f"🎯 Carpeta B (USB/Destino): {destino}")

        copiados, eliminados, errores = 0, 0, 0

        # =========================================================
        # MODO BIDIRECCIONAL REAL
        # =========================================================
        if modo == "bidireccional":
            destino.mkdir(parents=True, exist_ok=True)
            
            # 1. Recrear estructura de directorios vacíos en ambos lados
            for root, dirs, _ in os.walk(origen):
                dirs[:] = [d for d in dirs if not self._excluir_archivo(Path(root) / d)]
                for d in dirs:
                    (destino / (Path(root) / d).relative_to(origen)).mkdir(parents=True, exist_ok=True)

            for root, dirs, _ in os.walk(destino):
                dirs[:] = [d for d in dirs if not self._excluir_archivo(Path(root) / d)]
                for d in dirs:
                    (origen / (Path(root) / d).relative_to(destino)).mkdir(parents=True, exist_ok=True)

            # 2. Mapear todos los archivos relativos
            rel_origen = {p.relative_to(origen): p for p in origen.rglob('*') if p.is_file() and not self._excluir_archivo(p)}
            rel_destino = {p.relative_to(destino): p for p in destino.rglob('*') if p.is_file() and not self._excluir_archivo(p)}

            todos_los_relativos = sorted(list(set(rel_origen.keys()).union(set(rel_destino.keys()))))
            total = len(todos_los_relativos)

            for idx, rel in enumerate(todos_los_relativos, 1):
                if self.cancel_event.is_set(): break
                while not self.pause_event.is_set():
                    if self.cancel_event.is_set(): break
                    time.sleep(0.1)

                src_file = rel_origen.get(rel)
                dst_file = rel_destino.get(rel)

                target_src = origen / rel
                target_dst = destino / rel

                if callback_progreso:
                    callback_progreso(idx, total, copiados, eliminados, errores, str(rel))

                # Caso A: Existe en PC pero NO en USB ➔ Copiar a USB
                if src_file and not dst_file:
                    exito, est = self._copiar_con_reintentos(src_file, target_dst, callback_log=callback_log)
                    if exito and est == "copiado":
                        copiados += 1
                        if callback_log: callback_log(f"➕ [PC ➔ USB] Nuevo: {rel}")
                    elif not exito and est != "cancelado":
                        errores += 1

                # Caso B: Existe en USB pero NO en PC ➔ Copiar a PC
                elif dst_file and not src_file:
                    exito, est = self._copiar_con_reintentos(dst_file, target_src, callback_log=callback_log)
                    if exito and est == "copiado":
                        copiados += 1
                        if callback_log: callback_log(f"🔄 [USB ➔ PC] Nuevo: {rel}")
                    elif not exito and est != "cancelado":
                        errores += 1

                # Caso C: Existe en AMBOS LADOS ➔ Comparar fechas
                elif src_file and dst_file:
                    try:
                        mtime_src = src_file.stat().st_mtime
                        mtime_dst = dst_file.stat().st_mtime

                        if mtime_src - mtime_dst > 2.0:
                            exito, est = self._copiar_con_reintentos(src_file, target_dst, callback_log=callback_log)
                            if exito and est == "copiado":
                                copiados += 1
                                if callback_log: callback_log(f"⬆️ [PC ➔ USB] Actualizado: {rel}")
                            elif not exito and est != "cancelado":
                                errores += 1

                        elif mtime_dst - mtime_src > 2.0:
                            exito, est = self._copiar_con_reintentos(dst_file, target_src, callback_log=callback_log)
                            if exito and est == "copiado":
                                copiados += 1
                                if callback_log: callback_log(f"⬇️ [USB ➔ PC] Actualizado: {rel}")
                            elif not exito and est != "cancelado":
                                errores += 1
                    except Exception as e:
                        errores += 1

            if callback_log:
                callback_log(f"✅ Sincronización Bidireccional completada. Copiados/Actualizados: {copiados}, Errores: {errores}")
            return copiados, eliminados, errores

        # =========================================================
        # MODOS INCREMENTAL Y ESPEJO
        # =========================================================
        archivos_origen = []
        directorios_origen = []

        for root, dirs, files in os.walk(origen):
            dirs[:] = [d for d in dirs if not self._excluir_archivo(Path(root) / d)]
            for d in dirs:
                r_dir = Path(root) / d
                if not self._excluir_archivo(r_dir):
                    directorios_origen.append(r_dir)
            for f in files:
                p = Path(root) / f
                if not self._excluir_archivo(p):
                    archivos_origen.append(p)

        # Recrear estructura de carpetas de Origen en Destino
        for dir_src in directorios_origen:
            if self.cancel_event.is_set(): break
            dir_dst = destino / dir_src.relative_to(origen)
            try:
                dir_dst.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Error creando carpeta {dir_dst}: {e}")

        total = len(archivos_origen)

        # Copiar Archivos
        for idx, src in enumerate(archivos_origen, 1):
            if self.cancel_event.is_set(): break
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

        # Limpieza Estricta en Modo Espejo (Archivos y Carpetas sobrantes)
        if modo == "espejo" and destino.exists() and not self.cancel_event.is_set():
            # Usamos topdown=False para procesar las subcarpetas antes que sus padres
            for root, dirs, files in os.walk(destino, topdown=False):
                if self.cancel_event.is_set(): break
                
                # 1. Eliminar archivos que ya no existen en origen
                for f in files:
                    r_dst = Path(root) / f
                    rel = r_dst.relative_to(destino)
                    if not (origen / rel).exists():
                        try:
                            r_dst.unlink()
                            eliminados += 1
                            if callback_log: callback_log(f"🗑️ Eliminado en destino (Espejo): {rel}")
                        except Exception:
                            errores += 1

                # 2. Eliminar directorios vacíos o que no existen en origen
                for d in dirs:
                    r_dir_dst = Path(root) / d
                    rel_dir = r_dir_dst.relative_to(destino)
                    src_dir_corr = origen / rel_dir

                    if not src_dir_corr.exists():
                        try:
                            # Intentar eliminar la carpeta (solo funcionará si está vacía)
                            r_dir_dst.rmdir()
                            eliminados += 1
                            if callback_log: callback_log(f"🗑️ Carpeta eliminada en destino (Espejo): {rel_dir}")
                        except OSError:
                            # Si no está vacía o hay error de permisos
                            pass

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
                         callback_log: Optional[Callable] = None,
                         callback_progreso: Optional[Callable] = None) -> Optional[Path]:
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

        if callback_log: 
            callback_log(f"📦 Analizando archivos para comprimir...")

        try:
            # 1. Escanear y contar archivos previamente
            archivos_a_procesar = []
            for root, dirs, files in os.walk(carpeta_origen):
                if self.cancel_event.is_set(): return None
                dirs[:] = [d for d in dirs if not self._excluir_archivo(Path(root) / d)]
                for f in files:
                    p = Path(root) / f
                    if not self._excluir_archivo(p): 
                        archivos_a_procesar.append(p)

            total_archivos = len(archivos_a_procesar)
            if callback_log: 
                callback_log(f"📦 Empaquetando {total_archivos} archivos en: {ruta_zip.name}")

            manifest = {"version": VERSION, "fecha": datetime.now().isoformat(), "origen": str(carpeta_origen), "archivos": {}}
            
            # Calcular Hashes si está activo
            hashes = SecurityUtils.calcular_hashes_paralelo(archivos_a_procesar) if self.config.get_opcion("verificar_hash", True) else {}

            # 2. Comprimir y reportar avance archivo por archivo
            with zipfile.ZipFile(ruta_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=compression_level) as zf:
                for idx, r in enumerate(archivos_a_procesar, 1):
                    if self.cancel_event.is_set():
                        if callback_log: callback_log("🛑 Compresión .ZIP cancelada por el usuario.")
                        break

                    while not self.pause_event.is_set():
                        if self.cancel_event.is_set(): break
                        time.sleep(0.1)

                    arcname = str(r.relative_to(carpeta_origen))
                    zf.write(r, arcname)
                    manifest["archivos"][arcname] = {"size": r.stat().st_size, "hash": hashes.get(r, "")}

                    # Enviar avance en tiempo real a la interfaz
                    if callback_progreso:
                        callback_progreso(idx, total_archivos, idx, 0, 0, f"Comprimiendo: {arcname}")

                if not self.cancel_event.is_set():
                    zf.writestr("manifest_backup.json", json.dumps(manifest, indent=4))

            if self.cancel_event.is_set():
                if ruta_zip.exists():
                    try: ruta_zip.unlink()
                    except Exception: pass
                return None

            # 3. Cifrado opcional con reporte visual
            if password and CRYPTO_AVAILABLE:
                if callback_log: callback_log("🔒 Aplicando cifrado AES-256 al paquete...")
                ruta_cifrada = ruta_zip.with_suffix(".zip.enc")
                if SecurityUtils.cifrar_archivo(ruta_zip, ruta_cifrada, password, self.cancel_event):
                    ruta_zip.unlink()
                    ruta_zip = ruta_cifrada
                    if callback_log: callback_log("🔒 Paquete cifrado exitosamente con AES-256")
                else: 
                    raise RuntimeError("Error cifrando el archivo ZIP.")

            if callback_log: callback_log(f"✅ Backup .ZIP completado: {ruta_zip.name}")
            return ruta_zip

        except Exception as e:
            logger.error(f"Error creando backup zip: {e}")
            if callback_log: callback_log(f"❌ Error en compresión ZIP: {e}")
            if ruta_zip.exists():
                try: ruta_zip.unlink()
                except Exception: pass
            return None

    def restaurar_desde_backup(self, ruta_zip: Path, destino: Path, password: Optional[str] = None,
                               callback_log: Optional[Callable] = None,
                               callback_progreso: Optional[Callable] = None) -> bool:
        if callback_log: callback_log(f"📥 Iniciando restauración desde: {ruta_zip.name}")
        temp_zip_path = None
        target_zip = ruta_zip

        # 1. Gestionar Descifrado AES si aplica
        if ruta_zip.suffix == ".enc":
            if not CRYPTO_AVAILABLE:
                if callback_log: callback_log("❌ Error: Se requiere PyCryptodome para descifrar.")
                return False
            try:
                if callback_log: callback_log("🔑 Descifrando archivo .ZIP.ENC temporalmente...")
                temp_file = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
                temp_zip_path = Path(temp_file.name)
                temp_file.close()

                if not SecurityUtils.descifrar_archivo(ruta_zip, temp_zip_path, password):
                    raise ValueError("Contraseña incorrecta o archivo corrupto.")
                target_zip = temp_zip_path
            except Exception as e:
                if callback_log: callback_log(f"❌ Error al descifrar: {e}")
                if temp_zip_path and temp_zip_path.exists(): temp_zip_path.unlink()
                return False

        # 2. Extraer Archivos con Progreso
        try:
            destino_dir = destino.resolve()
            destino_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(target_zip, 'r') as zf:
                miembros = [m for m in zf.infolist() if m.filename != "manifest_backup.json" and not m.is_dir()]
                total = len(miembros)

                for idx, member in enumerate(miembros, 1):
                    if self.cancel_event.is_set():
                        if callback_log: callback_log("🛑 Restauración cancelada.")
                        return False

                    # Prevenir ataques Zip Slip (rutas absolutas maliciosas)
                    target_path = (destino_dir / member.filename).resolve()
                    if not str(target_path).startswith(str(destino_dir)):
                        continue

                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)

                    if callback_progreso:
                        callback_progreso(idx, total, idx, 0, 0, f"Restaurando: {member.filename}")

            if callback_log: callback_log("✅ Restauración desde .ZIP completada con éxito.")
            return True
        except Exception as e:
            logger.error(f"Error en restauración ZIP: {e}")
            if callback_log: callback_log(f"❌ Error al restaurar: {e}")
            return False
        finally:
            if temp_zip_path and temp_zip_path.exists():
                try: temp_zip_path.unlink()
                except Exception: pass

# --- DISEÑO DIVIDIDO (SPLIT-SCREEN INTERFACE) ---
BaseTk = tk.Tk if GUI_AVAILABLE else object

class Copy4MeGUI(BaseTk):
    def __init__(self):
        if not GUI_AVAILABLE:
            raise ImportError("Tkinter no está presente en el sistema.")
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

        self.font_title = ("Segoe UI", 12, "bold")
        self.font_sub = ("Segoe UI", 10, "italic")
        self.font_bold = ("Segoe UI", 11, "bold")
        self.font_norm = ("Segoe UI", 11)
        self.font_big_btn = ("Segoe UI", 11, "bold")

        self.style.configure('TLabelframe', background="#ffffff", relief="solid", borderwidth=1, bordercolor="#cbd5e1")
        self.style.configure('TLabelframe.Label', font=self.font_title, foreground="#0f172a", background="#ffffff")
        self.style.configure('TFrame', background="#f8fafc")
        self.style.configure('TLabel', background="#ffffff", foreground="#334155", font=self.font_norm)
        self.style.configure('TRadiobutton', background="#ffffff", font=self.font_norm)
        self.style.configure('TCheckbutton', background="#ffffff", font=self.font_norm)
        
        self.style.configure('TButton', font=self.font_norm, padding=6)
        self.style.configure('TCombobox', font=self.font_norm, padding=4)
        self.style.configure('TEntry', font=self.font_norm, padding=4)

    def _crear_interfaz_dividida(self):
        top_bar = ttk.Frame(self, padding=(15, 8))
        top_bar.pack(fill=tk.X)
        
        ttk.Label(top_bar, text=f"📂 {APP_NAME} Enterprise", font=self.font_title, foreground="#0f172a").pack(side=tk.LEFT)
        
        btn_tools = ttk.Frame(top_bar)
        btn_tools.pack(side=tk.RIGHT)
        
        ttk.Button(btn_tools, text="🔍 Historial Backups", command=self._abrir_ventana_historial).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_tools, text="⚙️ Ajustes", command=self._abrir_ventana_ajustes).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_tools, text="🔌 Refrescar USB", command=self._refresh_all).pack(side=tk.LEFT, padx=3)

        main_split = ttk.Frame(self, padding=(10, 0, 10, 5))
        main_split.pack(fill=tk.BOTH, expand=False)
        main_split.columnconfigure(0, weight=4) 
        main_split.columnconfigure(1, weight=2) 
        main_split.columnconfigure(2, weight=4) 

        # ---------------- ORIGEN ----------------
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

        # ---------------- ACCIÓN ----------------
        card_centro = ttk.Frame(main_split, padding=5)
        card_centro.grid(row=0, column=1, sticky="nsew")

        # Fila para Título de Modo + Botón Info
        row_modo_header = ttk.Frame(card_centro)
        row_modo_header.pack(pady=(5, 2))

        ttk.Label(row_modo_header, text="Modo de Operación:", font=self.font_bold).pack(side=tk.LEFT)
        btn_info_modos = ttk.Button(row_modo_header, text="ℹ️", width=3, command=self._mostrar_info_modos)
        btn_info_modos.pack(side=tk.LEFT, padx=(5, 0))

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

        # ---------------- DESTINO ----------------
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

        # ---------------- PANEL LOGS/CONTROLES ----------------
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
                self.engine.crear_backup_zip(
                    origen, nombre, password, compression_level, 
                    callback_log=cb_log_custom, 
                    callback_progreso=cb_progreso
                )

            header = f"Respaldo finalizado en '{nombre}'\nArchivos copiados: {copiados} | Eliminados: {eliminados} | Errores: {errores}"
            detalle = "\n".join(cambios_detallados) if cambios_detallados else "Archivos sincronizados sin cambios pendientes."
            self.ui_queue.put(("mostrar_reporte_detallado", (header, detalle)))

        except Exception as e:
            self.ui_queue.put(("msgbox_error", ("Error Crítico", f"Error durante el respaldo:\n{e}")))
        finally:
            self.ui_queue.put(("stop_progress", None))
            self.ui_queue.put(("refresh", None))

    def _ejecutar_restauracion_izquierda(self):
        destino_pc = self.entry_ruta_origen.get().strip()
        origen_usb = self.entry_ruta_destino.get().strip()

        if not destino_pc:
            messagebox.showerror("Error", "Seleccione la carpeta de la PC (panel izquierdo) donde desea restaurar los datos.")
            return

        # Ventana de elección de método de restauración
        win_opc = tk.Toplevel(self)
        win_opc.title("Seleccionar Método de Restauración")
        win_opc.geometry("480x240")
        win_opc.grab_set()

        ttk.Label(win_opc, text="¿Cómo desea realizar la restauración?", font=self.font_bold).pack(pady=12)

        def elegir_zip():
            win_opc.destroy()
            self._restaurar_desde_zip_gui(Path(destino_pc))

        def elegir_directo():
            win_opc.destroy()
            if not origen_usb or not Path(origen_usb).exists():
                messagebox.showerror("Error", "Seleccione una carpeta válida en el panel derecho (USB/Destino).")
                return
            self._restaurar_directo_gui(Path(origen_usb), Path(destino_pc))

        btn_zip = ttk.Button(win_opc, text="📦 Desde Archivo .ZIP / .ZIP.ENC\n(Elegir una copia comprimida específica)", command=elegir_zip)
        btn_zip.pack(fill=tk.X, padx=20, pady=8)

        btn_dir = ttk.Button(win_opc, text="📁 Copia Directa entre Carpetas\n(Copiar archivos tal cual desde el panel derecho)", command=elegir_directo)
        btn_dir.pack(fill=tk.X, padx=20, pady=8)

    def _restaurar_desde_zip_gui(self, destino_pc: Path):
        # 1. Seleccionar archivo .zip
        archivo_zip_str = filedialog.askopenfilename(
            parent=self,
            title="Selecciona el archivo de Backup (.zip o .zip.enc)",
            filetypes=[("Archivos de Backup", "*.zip *.zip.enc"), ("Todos los archivos", "*.*")]
        )
        if not archivo_zip_str: return

        ruta_zip = Path(archivo_zip_str)
        password = None

        # 2. Pedir contraseña si es cifrado
        if ruta_zip.suffix == ".enc":
            if not CRYPTO_AVAILABLE:
                messagebox.showerror("Error", "Librería PyCryptodome no instalada.")
                return
            password = simpledialog.askstring("Archivo Cifrado", "Introduce la contraseña AES para descifrar:", show='*')
            if not password: return

        # 3. Confirmar acción
        if not messagebox.askyesno("Confirmar Restauración", f"¿Restaurar el contenido del paquete:\n{ruta_zip.name}\n\nHacia la carpeta:\n{destino_pc}?"):
            return

        self.btn_pausa.config(state="normal")
        self.btn_cancelar.config(state="normal")
        threading.Thread(target=self._worker_restaurar_zip, args=(ruta_zip, destino_pc, password), daemon=True).start()

    def _worker_restaurar_zip(self, ruta_zip: Path, destino_pc: Path, password: Optional[str]):
        def cb_progreso(idx, total, cop, del_, err, arch):
            self.ui_queue.put(("set_determinate", (total,)))
            self.ui_queue.put(("progress", (idx, total, cop, del_, err, arch)))

        try:
            exito = self.engine.restaurar_desde_backup(
                ruta_zip, destino_pc, password, 
                callback_log=self.log_gui, 
                callback_progreso=cb_progreso
            )
            if exito:
                self.ui_queue.put(("msgbox", ("Restauración Exitosa", f"Los archivos se restauraron correctamente en:\n{destino_pc}")))
        except Exception as e:
            self.ui_queue.put(("msgbox_error", ("Error de Restauración", f"Fallo al restaurar:\n{e}")))
        finally:
            self.ui_queue.put(("stop_progress", None))
            self.ui_queue.put(("refresh", None))

    def _restaurar_directo_gui(self, origen_usb: Path, destino_pc: Path):
        if messagebox.askyesno("Confirmar Restauración Directa", f"¿Copiar archivos directamente:\nDesde: {origen_usb}\nHacia: {destino_pc}?"):
            self.btn_pausa.config(state="normal")
            self.btn_cancelar.config(state="normal")
            threading.Thread(target=self._worker_restaurar_directo, args=(origen_usb, destino_pc), daemon=True).start()

    def _worker_restaurar_directo(self, origen_usb: Path, destino_pc: Path):
        def cb_progreso(idx, total, cop, del_, err, arch):
            self.ui_queue.put(("set_determinate", (total,)))
            self.ui_queue.put(("progress", (idx, total, cop, del_, err, arch)))

        try:
            copiados, eliminados, errores = self.engine.sincronizar(
                origen_usb, destino_pc, modo="incremental", 
                callback_progreso=cb_progreso, 
                callback_log=self.log_gui
            )
            self.ui_queue.put(("msgbox", ("Restauración Completada", f"Sincronización directa terminada.\nArchivos restaurados: {copiados}\nErrores: {errores}")))
        except Exception as e:
            self.ui_queue.put(("msgbox_error", ("Error", f"Fallo en la sincronización directa: {e}")))
        finally:
            self.ui_queue.put(("stop_progress", None))
            self.ui_queue.put(("refresh", None))

    def _abrir_ventana_historial(self):
        v = tk.Toplevel(self)
        v.title("Historial de Copias Comprimidas (.ZIP)")
        v.geometry("750x450")

        # Contenedor para la tabla
        frame_tabla = ttk.Frame(v)
        frame_tabla.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))

        tree = ttk.Treeview(frame_tabla, columns=("Fecha", "Tamaño", "Formato"), show="tree headings")
        tree.heading("#0", text="Proyecto / Archivo Backup")
        tree.heading("Fecha", text="Fecha de Creación")
        tree.heading("Tamaño", text="Tamaño")
        tree.heading("Formato", text="Estado Cifrado")

        # Ajustar anchos de columnas
        tree.column("#0", width=300)
        tree.column("Fecha", width=140)
        tree.column("Tamaño", width=100)
        tree.column("Formato", width=120)

        scrollbar = ttk.Scrollbar(frame_tabla, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Diccionario para mapear nodos del árbol con rutas reales del disco
        rutas_archivos = {}

        def cargar_backups():
            tree.delete(*tree.get_children())
            rutas_archivos.clear()

            if DIR_BACKUPS.exists():
                for p in sorted(DIR_BACKUPS.iterdir()):
                    if p.is_dir():
                        node = tree.insert("", tk.END, text=f"📂 {p.name}", open=True)
                        for b in sorted(p.glob("backup_*"), key=lambda x: x.stat().st_mtime, reverse=True):
                            if b.is_file():
                                fecha = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                                tam = formatear_tamano(b.stat().st_size)
                                est = "🔒 Cifrado AES" if b.suffix == ".enc" else "📦 ZIP Estándar"
                                item_id = tree.insert(node, tk.END, text=b.name, values=(fecha, tam, est))
                                rutas_archivos[item_id] = b

        cargar_backups()

        # Botón de eliminación
        frame_acciones = ttk.Frame(v, padding=5)
        frame_acciones.pack(fill=tk.X, padx=10, pady=(0, 10))

        def borrar_backup_seleccionado():
            seleccion = tree.selection()
            if not seleccion:
                messagebox.showwarning("Atención", "Seleccione un archivo de backup para eliminar.", parent=v)
                return

            item_id = seleccion[0]
            ruta_file = rutas_archivos.get(item_id)

            if not ruta_file or not ruta_file.exists():
                messagebox.showerror("Error", "El elemento seleccionado es una carpeta o el archivo ya no existe.", parent=v)
                return

            if messagebox.askyesno("Confirmar Borrado", f"¿Desea eliminar permanentemente el archivo?\n\n{ruta_file.name}", parent=v):
                try:
                    ruta_file.unlink()
                    self.log_gui(f"🗑️ Backup eliminado manualmente: {ruta_file.name}")
                    cargar_backups()
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo eliminar el archivo:\n{e}", parent=v)

        btn_borrar = ttk.Button(frame_acciones, text="🗑️ Eliminar Backup Seleccionado", command=borrar_backup_seleccionado)
        btn_borrar.pack(side=tk.RIGHT)

    def _abrir_ventana_ajustes(self):
        v = tk.Toplevel(self)
        v.title("Ajustes Generales del Sistema")
        v.geometry("500x420")
        v.grab_set()  # Mantiene la ventana al frente hasta que se cierre

        f = ttk.Frame(v, padding=15)
        f.pack(fill=tk.BOTH, expand=True)

        # 1. Verificación SHA-256
        var_hash = tk.BooleanVar(value=self.config.get_opcion("verificar_hash", True))
        ttk.Checkbutton(f, text="Verificación estricta de integridad (SHA-256)", variable=var_hash).pack(anchor=tk.W, pady=5)

        # 2. Rotación de Backups (Máximo de copias)
        ttk.Label(f, text="Máximo de backups .ZIP a conservar por proyecto:").pack(anchor=tk.W, pady=(10, 2))
        spin_max = tk.Spinbox(f, from_=1, to=50, width=8)
        spin_max.delete(0, tk.END)
        spin_max.insert(0, str(self.config.get_opcion("max_backups", MAX_BACKUPS)))
        spin_max.pack(anchor=tk.W)

        # 3. Nivel de Compresión
        ttk.Label(f, text="Nivel Compresión ZIP (0 = sin compresión, 9 = máxima):").pack(anchor=tk.W, pady=(10, 2))
        spin_comp = tk.Spinbox(f, from_=0, to=9, width=8)
        spin_comp.delete(0, tk.END)
        spin_comp.insert(0, str(self.config.get_opcion("compresion", DEFAULT_COMPRESSION_LEVEL)))
        spin_comp.pack(anchor=tk.W)

        # 4. Extensiones Excluidas
        ttk.Label(f, text="Extensiones Excluidas (separadas por comas, ej: .tmp, .log):").pack(anchor=tk.W, pady=(10, 2))
        ent_excl = ttk.Entry(f)
        ent_excl.insert(0, ", ".join(self.config.get_opcion("excluir_patrones", [])))
        ent_excl.pack(fill=tk.X, pady=(0, 15))

        # --- FUNCIÓN PARA GUARDAR TODO JUNTO ---
        def guardar_todos_los_ajustes():
            try:
                # Validar y guardar Max Backups
                max_b = int(spin_max.get())
                if max_b < 1: max_b = 1
                self.config.set_opcion("max_backups", max_b)
                self.engine.max_backups = max_b  # Actualizar motor en caliente

                # Validar y guardar Compresión
                comp = int(spin_comp.get())
                if not (0 <= comp <= 9): comp = DEFAULT_COMPRESSION_LEVEL
                self.config.set_opcion("compresion", comp)

                # Guardar Checkbox y Patrones
                self.config.set_opcion("verificar_hash", var_hash.get())
                
                patrones = [x.strip() for x in ent_excl.get().split(',') if x.strip()]
                self.config.set_opcion("excluir_patrones", patrones)

                self.log_gui("⚙️ Ajustes guardados correctamente en la configuración.")
                v.destroy()  # Cerrar ventana
            except ValueError:
                messagebox.showerror("Error de Validación", "Por favor ingresa números válidos en los campos numéricos.", parent=v)

        # Botón único de guardado
        btn_guardar = ttk.Button(f, text="💾 Guardar Cambios", command=guardar_todos_los_ajustes)
        btn_guardar.pack(anchor=tk.E, pady=10)

    def _refresh_all(self):
        perfiles = self.config._data.get("perfiles", {})
        self.combo_perfiles['values'] = sorted(perfiles.keys())

        proyectos_usb = set()
        usbs = USBDetector.listar_unidades_extraibles()
        
        for usb in usbs:
            proyectos_usb.add(str(usb)) 
            
            backup_dir = usb / "copy4me_backups"
            if backup_dir.exists():
                for d in backup_dir.iterdir():
                    if d.is_dir(): 
                        proyectos_usb.add(d.name)

        self.combo_proyectos_usb['values'] = sorted(list(proyectos_usb))
        
        if usbs and not self.entry_ruta_destino.get().strip():
            self.entry_ruta_destino.delete(0, tk.END)
            self.entry_ruta_destino.insert(0, str(usbs[0]))
            
        self.log_gui(f"🔄 Escaneo completado. Unidades/Rutas halladas: {len(usbs)}")

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

        self.update_idletasks()

        nuevo_nombre = simpledialog.askstring(
            "Renombrar Perfil", 
            f"Introduce el nuevo nombre para '{nombre_actual}':"
        )
        
        if nuevo_nombre:
            nuevo_nombre_sano = limpiar_nombre_ruta(nuevo_nombre)
            if nuevo_nombre_sano == nombre_actual or not nuevo_nombre_sano:
                return
            
            perfil_data = self.config.get_perfil(nombre_actual)
            if perfil_data:
                self.config._data["perfiles"][nuevo_nombre_sano] = perfil_data
                self.config.delete_perfil(nombre_actual)
                
                self.update_idletasks()
                self._refresh_all()
                self.combo_perfiles.set(nuevo_nombre_sano)
                self.log_gui(f"✏️ Perfil '{nombre_actual}' renombrado a '{nuevo_nombre_sano}'")

    def _mostrar_info_modos(self):
        v = tk.Toplevel(self)
        v.title("Información sobre Modos de Sincronización")
        v.geometry("580x450")
        v.grab_set()

        f = ttk.Frame(v, padding=15)
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(f, text="📐 Explicación de los Modos de Copiado", font=self.font_bold).pack(anchor=tk.W, pady=(0, 10))

        txt = scrolledtext.ScrolledText(f, wrap=tk.WORD, font=("Segoe UI", 10), bg="#ffffff", fg="#0f172a")
        txt.pack(fill=tk.BOTH, expand=True)

        contenido = (
            "1. MODO INCREMENTAL (Añadir sin borrar)\n"
            "• ¿Qué hace?: Copia de Origen a Destino únicamente los archivos nuevos o que hayan sido modificados recientemente.\n"
            "• ¿Si borras en PC?: El archivo SE MANTIENE intacto en la USB/Destino.\n"
            "• Ideal para: Copias acumulativas de seguridad donde no quieres perder nada.\n\n"
            "--------------------------------------------------\n\n"
            "2. MODO ESPEJO (Clonación exacta)\n"
            "• ¿Qué hace?: Fuerza a que el Destino sea un duplicado idéntico del Origen.\n"
            "• ¿Si borras en PC?: Se BORRARÁ también en la USB/Destino al sincronizar para mantener ambas carpetas iguales.\n"
            "• Ideal para: Mantener una copia idéntica de trabajo día a día.\n\n"
            "--------------------------------------------------\n\n"
            "3. MODO BIDIRECCIONAL (Sincronización en 2 sentidos)\n"
            "• ¿Qué hace?: Ambas carpetas se actualizan mutuamente. Si creas o modificas un archivo en la USB (por ejemplo, trabajando en otra PC), el programa lo detecta y lo copia de vuelta a tu PC fija.\n"
            "• Ideal para: Trabajar con la USB en ordenadores portátiles/externos y sincronizar los avances al volver a tu PC."
        )

        txt.insert(tk.END, contenido)
        txt.config(state='disabled')

def modo_tui():
    config = ConfigManager()
    engine = SyncEngine(config)

    def log_tui(msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    while True:
        print("\n" + "="*55)
        print(f"   💻 {APP_NAME} Enterprise - Modo Terminal ({VERSION})")
        print("="*55)
        print(" 1. 🚀 Realizar Sincronización / Respaldo (PC -> USB/Disco)")
        print(" 2. 📂 Gestionar Perfiles Guardados")
        print(" 3. 🔌 Escanear y Probar Unidades Externas/USB")
        print(" 4. 🔄 Restaurar Datos (USB/Disco -> PC)")
        print(" 5. 📦 Crear Backup .ZIP Comprimido/Cifrado")
        print(" 6. ⚙️ Configuración / Opciones")
        print(" 0. ↩ Salir")
        print("-" * 55)

        opc = input("Selecciona una opción [0-6]: ").strip()

        if opc == "1":
            print("\n--- RESPALDO DE DATOS ---")
            perfiles = config._data.get("perfiles", {})
            nombre_perfil = ""
            origen_str = ""
            destino_str = ""

            if perfiles:
                print("\nPerfiles guardados detectados:")
                keys = list(perfiles.keys())
                for i, k in enumerate(keys, 1):
                    print(f"  [{i}] {k} ➔ {perfiles[k].get('ruta_local')}")
                print("  [0] Ingresar nueva ruta manualmente")
                
                sel_p = input("\nSeleccione perfil o 0 para nuevo: ").strip()
                if sel_p.isdigit() and 1 <= int(sel_p) <= len(keys):
                    nombre_perfil = keys[int(sel_p) - 1]
                    p_data = perfiles[nombre_perfil]
                    origen_str = p_data.get('ruta_local', '')
                    destino_str = p_data.get('ruta_destino', '')

            if not origen_str:
                origen_str = input("\nRuta Carpeta ORIGEN (PC): ").strip()
                if not origen_str:
                    print("❌ Operación cancelada: No se ingresó ruta de origen.")
                    continue
                nombre_perfil = Path(origen_str).name

            origen_path = Path(origen_str)
            if not origen_path.exists():
                print(f"❌ Error: La ruta de origen '{origen_str}' no existe.")
                continue

            if not destino_str:
                usbs = USBDetector.listar_unidades_extraibles()
                if usbs:
                    print("\nUnidades externas detectadas:")
                    for i, u in enumerate(usbs, 1):
                        print(f"  [{i}] 🔌 {u}")
                    print("  [0] Ingresar otra ruta de destino manualmente")
                    
                    sel_u = input("\nSeleccione unidad externa o 0: ").strip()
                    if sel_u.isdigit() and 1 <= int(sel_u) <= len(usbs):
                        destino_str = str(usbs[int(sel_u) - 1] / "copy4me_backups" / nombre_perfil)

                if not destino_str:
                    destino_str = input("Ruta Carpeta DESTINO: ").strip()

            if not destino_str:
                print("❌ Operación cancelada: No se definió una ruta de destino.")
                continue

            destino_path = Path(destino_str)

            print("\nModos de Sincronización:")
            print("  [1] incremental   | Copia solo archivos nuevos o modificados")
            print("  [2] espejo        | Borra en destino lo eliminado en origen")
            print("  [3] bidireccional | Sincroniza cambios en ambos sentidos")
            m_opc = input("Selecciona modo [1-3] (Por defecto 1): ").strip()
            
            modo = "espejo" if m_opc == "2" else ("bidireccional" if m_opc == "3" else "incremental")

            is_valid, msg = validar_espacio_disponible(origen_path, destino_path)
            if not is_valid:
                print(f"❌ Error: {msg}")
                continue

            print("\n🚀 Iniciando proceso...")
            engine.sincronizar(origen_path, destino_path, modo=modo, callback_log=log_tui)
            config.set_perfil(nombre_perfil, origen_path, destino_path)
            print("✔ Perfil actualizado y sincronización finalizada.")

        elif opc == "2":
            print("\n--- GESTIÓN DE PERFILES ---")
            perfiles = config._data.get("perfiles", {})
            if not perfiles:
                print("⚠️ No hay perfiles guardados.")
                continue

            keys = list(perfiles.keys())
            for i, k in enumerate(keys, 1):
                v = perfiles[k]
                print(f" [{i}] {k}")
                print(f"     📁 Origen : {v.get('ruta_local')}")
                print(f"     🎯 Destino: {v.get('ruta_destino')}")

            print("\nOpciones: [D] Eliminar Perfil | [Enter] Volver")
            sub_opc = input("Acción: ").strip().lower()

            if sub_opc == 'd':
                num = input("Número de perfil a eliminar: ").strip()
                if num.isdigit() and 1 <= int(num) <= len(keys):
                    target = keys[int(num) - 1]
                    config.delete_perfil(target)
                    print(f"✔ Perfil '{target}' eliminado con éxito.")
                else:
                    print("❌ Selección no válida.")

        elif opc == "3":
            print("\n--- DETECCIÓN DE UNIDADES EXTERNAS / USB ---")
            usbs = USBDetector.listar_unidades_extraibles()
            if not usbs:
                print("⚠️ No se encontraron unidades externas o USBs conectadas.")
            else:
                for u in usbs:
                    print(f"🔌 Unidad detectada: {u}")
                    backups = u / "copy4me_backups"
                    if backups.exists():
                        print(f"   └─ 📂 Proyectos dentro: {[d.name for d in backups.iterdir() if d.is_dir()]}")

        elif opc == "4":
            print("\n--- RESTAURACIÓN DE DATOS ---")
            origen_str = input("Ruta Carpeta ORIGEN (Respaldo en USB/Disco): ").strip()
            if not origen_str or not Path(origen_str).exists():
                print("❌ Ruta de origen no válida o inexistente.")
                continue

            destino_str = input("Ruta Carpeta DESTINO (En PC): ").strip()
            if not destino_str:
                print("❌ Debe especificar una ruta de destino.")
                continue

            is_valid, msg = validar_espacio_disponible(Path(origen_str), Path(destino_str))
            if not is_valid:
                print(f"❌ Error de espacio: {msg}")
                continue

            print("\n🚀 Restaurando archivos...")
            engine.sincronizar(Path(origen_str), Path(destino_str), modo="incremental", callback_log=log_tui)
            print("✔ Restauración finalizada.")

        elif opc == "5":
            print("\n--- RESPALDO COMPRIMIDO (.ZIP) ---")
            origen_str = input("Ruta Carpeta a Comprimir: ").strip()
            if not origen_str or not Path(origen_str).exists():
                print("❌ Ruta no válida.")
                continue

            nombre = input("Nombre del Proyecto: ").strip() or Path(origen_str).name
            cifrar = input("¿Desea cifrar con AES-256? (s/n): ").strip().lower() == 's'
            password = None

            if cifrar:
                if not CRYPTO_AVAILABLE:
                    print("❌ PyCryptodome no está instalada en el sistema.")
                    continue
                password = input("Introduce Contraseña: ").strip()
                if not password:
                    print("❌ La contraseña no puede estar vacía.")
                    continue

            comp = config.get_opcion("compresion", 6)
            engine.crear_backup_zip(Path(origen_str), nombre, password=password, compression_level=comp, callback_log=log_tui)

        elif opc == "6":
            print("\n--- CONFIGURACIÓN DEL SISTEMA ---")
            hash_val = config.get_opcion("verificar_hash", True)
            comp_level = config.get_opcion("compresion", 6)

            print(f" 1. Verificación SHA-256 estricta : [{'ACTIVADO' if hash_val else 'DESACTIVADO'}]")
            print(f" 2. Nivel de compresión ZIP      : [{comp_level}]")

            sub = input("\n¿Desea cambiar la verificación SHA-256? (s/n): ").strip().lower()
            if sub == 's':
                config.set_opcion("verificar_hash", not hash_val)
                print(f"✔ Verificación SHA-256 cambiada a: {not hash_val}")

        elif opc == "0":
            print("👋 Saliendo de Copy4Me Terminal Engine...")
            break
        else:
            print("❌ Opción no válida. Intente nuevamente.")

# --- PUNTO DE ENTRADA ---
if __name__ == "__main__":
    if platform.system() == "Linux":
        os.environ["TK_SILENT_ERROR"] = "1"

    tiene_display = (
        platform.system() == "Windows" or 
        bool(os.environ.get('DISPLAY', '')) or 
        bool(os.environ.get('WAYLAND_DISPLAY', ''))
    )

    if GUI_AVAILABLE and tiene_display:
        try:
            app = Copy4MeGUI()
            app.tk.call('tk', 'scaling', 1.2)
            app.mainloop()
        except Exception as e:
            print(f"⚠️ No se pudo iniciar la interfaz gráfica ({e}).")
            print("🔄 Cambiando automáticamente a modo Terminal (TUI)...\n")
            modo_tui()
    else:
        if not GUI_AVAILABLE:
            print("ℹ️ Librería gráfica (Tkinter) no detectada.")
        elif not tiene_display:
            print("ℹ️ Entorno sin pantalla gráfica detectado (SSH/Servidor).")
            
        print("🚀 Iniciando Copy4Me en Modo Terminal (TUI)...\n")
        modo_tui()
