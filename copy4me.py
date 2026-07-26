from __future__ import annotations
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



# Activar alta densidad de píxeles (High DPI) en Windows si está disponible
if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI awareness
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
VERSION = "4.5 testing"
APP_NAME = "Copy4Me"
MAX_BACKUPS = 10
EXCLUDE_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env',
    '.idea', '.vscode', 'System Volume Information', '$RECYCLE.BIN',
    '.Trash-1000', 'Thumbs.db', '.DS_Store'
}
EXCLUDE_EXTENSIONS = {'.tmp', '.log', '.bak'}
DEFAULT_COMPRESSION_LEVEL = 6
CHUNK_SIZE = 64 * 1024  # 64 KB para operaciones por bloques

# --- Colores ANSI para Consola (TUI) ---
class Color:
    CYAN = "\033[96m"
    VERDE = "\033[92m"
    MAGENTA = "\033[95m"
    BLANCO = "\033[97m"
    AMARILLO = "\033[93m"
    ROJO = "\033[91m"
    AZUL = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

BANNER = f"""{Color.CYAN} ██████╗ ██████╗ ██████╗ ██╗██╗  ██╗███╗   ███╗███████╗
██╔════╝██╔═══██╗██╔══██╗██║██║  ██║████╗ ████║██╔════╝
{Color.VERDE}██║     ██║   ██║██████╔╝██║███████║██╔████╔██║█████╗  
██║     ██║   ██║██╔═══╝ ╚═╝╚════██║██║╚██╔╝██║██╔══╝  
{Color.MAGENTA}╚██████╗╚██████╔╝██║        ██║  ██║██║ ╚═╝ ██║███████╗
 ╚═════╝ ╚═════╝ ╚═╝        ╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝{Color.RESET}
 
{Color.CYAN}               _________________________________________
    [ PC-1 ]       C  O  P  Y  ◄─── 4 ──►  M  E         [ PC-2 ]
      📂       ==  ==  ==  ==  ==  ==  ==  ==  ==  ==       📂
    Directo          S i n c r o n i z a d o r           Respaldado
               __________________________________________{Color.RESET}
                   Versión: {Color.BOLD}{VERSION}{Color.RESET} | Max Backups: {Color.BOLD}{MAX_BACKUPS}{Color.RESET}
                   Equipo Local: {Color.AZUL}{socket.gethostname()}{Color.RESET} ({platform.system()} {platform.release()})
{Color.AMARILLO}   ------------------------------------------------------------{Color.RESET}"""

def validar_espacio_disponible(origen: Path, destino: Path, tamano_total: Optional[int] = None) -> tuple[bool, str]:
    """Verifica si el tamaño requerido cabe en la unidad de destino."""
    try:
        if tamano_total is None:
            tamano_total = calcular_tamano_origen(origen)
            
        unidad_destino = destino.anchor if destino.exists() else destino.parent.anchor
        _, _, libre = shutil.disk_usage(unidad_destino)

        if libre < tamano_total:
            tam_mb = tamano_total / (1024 * 1024)
            lib_mb = libre / (1024 * 1024)
            return False, f"Espacio insuficiente. Requerido: {tam_mb:.1f} MB | Disponible en destino: {lib_mb:.1f} MB"

        return True, "OK"
    except Exception as e:
        return True, f"No se pudo verificar el espacio: {e}"

def get_base_dir() -> Path:
    """Obtiene la ruta raíz del ejecutable o del script de origen."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

BASE_DIR = get_base_dir()
DIR_BACKUPS = BASE_DIR / "copy4me_backups"
CONFIG_FILE = BASE_DIR / "config.json"

def setup_logging():
    """Configura el sistema de registro (Logging) con rotación automática estándar por tamaño."""
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

        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(formatter)
        root_logger.addHandler(console)
    except Exception as e:
        print(f"⚠️ No se pudo configurar el registro de depuración: {e}")

setup_logging()
logger = logging.getLogger("Copy4Me")

def formatear_tamano(tamano_bytes: int) -> str:
    """Convierte bytes a un formato legible (KB, MB, GB)."""
    size = float(tamano_bytes)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

def calcular_tamano_origen(origen: Path) -> int:
    """Calcula el tamaño total en bytes de un archivo o directorio de forma eficiente."""
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
    """Administra la lectura y escritura del archivo de configuración JSON con seguridad atómica."""
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.path = config_path
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        """Carga la configuración desde el disco o inicializa valores por defecto."""
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
        """Guarda la configuración usando una escritura temporal atómica."""
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

# --- Detector de Dispositivos Extraíbles ---
class USBDetector:
    """Escanea el sistema en busca de unidades de almacenamiento USB / extraíbles."""
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

# --- Utilidades de Cifrado y Hash Flujo Continuo ---
class SecurityUtils:
    """Proporciona funciones de cálculo de firmas SHA-256 y cifrado por flujo AES-256 CBC."""
    @staticmethod
    def calcular_hash(archivo: Path, algoritmo="sha256") -> str:
        hash_func = hashlib.new(algoritmo)
        try:
            with open(archivo, 'rb') as f:
                while chunk := f.read(CHUNK_SIZE):
                    hash_func.update(chunk)
            return hash_func.hexdigest()
        except Exception as e:
            logger.warning(f"No se pudo calcular firma hash de {archivo}: {e}")
            return ""

    @staticmethod
    def cifrar_archivo(origen: Path, destino: Path, password: str) -> bool:
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("La librería PyCryptodome no está instalada.")
        try:
            salt = os.urandom(16)
            key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000, dklen=32)
            iv = os.urandom(16)
            cipher = AES.new(key, AES.MODE_CBC, iv)

            with open(origen, 'rb') as f_in, open(destino, 'wb') as f_out:
                f_out.write(salt)
                f_out.write(iv)
                
                while True:
                    chunk = f_in.read(CHUNK_SIZE)
                    if len(chunk) == CHUNK_SIZE:
                        f_out.write(cipher.encrypt(chunk))
                    else:
                        # Último bloque (incluso si mide 0 bytes): aplicamos padding
                        f_out.write(cipher.encrypt(pad(chunk, AES.block_size)))
                        break
            return True
        except Exception as e:
            logger.error(f"Error al cifrar archivo {origen}: {e}")
            if destino.exists():
                try:
                    destino.unlink()
                except Exception:
                    pass
            return False

    @staticmethod
    def descifrar_archivo(origen: Path, destino: Path, password: str) -> bool:
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("La librería PyCryptodome no está instalada.")
        try:
            file_size = origen.stat().st_size
            if file_size < 32:
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
                            f_out.write(unpad(cipher.decrypt(prev_chunk), AES.block_size))
                        break
                    if prev_chunk:
                        f_out.write(cipher.decrypt(prev_chunk))
                    prev_chunk = chunk
            return True
        except Exception as e:
            logger.error(f"Error al descifrar archivo {origen}: {e}")
            if destino.exists():
                try:
                    destino.unlink()
                except Exception:
                    pass
            return False

            # --- Motor de Sincronización y Respaldo ---
class SyncEngine:
    """Motor central de operaciones de copia, verificación de integridad y compresión."""
    def __init__(self, config: ConfigManager):
        self.config = config
        self.max_backups = config.get_opcion("max_backups", MAX_BACKUPS)
        self.exclude_dirs = EXCLUDE_DIRS.copy()
        self.exclude_exts = EXCLUDE_EXTENSIONS.copy()
        for pat in config.get_opcion("excluir_patrones", []):
            self.exclude_exts.add(pat if pat.startswith('.') else f".{pat}")

    def _excluir_archivo(self, ruta: Path) -> bool:
        if ruta.name.lower() in {d.lower() for d in self.exclude_dirs}:
            return True
        if ruta.name in self.exclude_dirs:
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
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    # Comprobamos si son idénticos para saber si se omite o se actualiza
                    if src.stat().st_size == dst.stat().st_size and abs(src.stat().st_mtime - dst.stat().st_mtime) <= 2.0:
                        return True, "omitido" # El archivo no cambió
                
                shutil.copy2(src, dst)
                if self.config.get_opcion("verificar_hash", True):
                    h_src = SecurityUtils.calcular_hash(src)
                    h_dst = SecurityUtils.calcular_hash(dst)
                    if h_src and h_dst and h_src != h_dst:
                        raise ValueError("Incoincidencia de Hash SHA-256")
                return True, "copiado"
            except Exception as e:
                logger.warning(f"Intento {attempt+1}/{max_attempts} fallido para {src.name}: {e}")
                if dst.exists():
                    try:
                        dst.unlink()  # <-- Elimina el archivo corrupto en destino antes de reintentar
                    except Exception:
                        pass
                time.sleep(0.3 * (attempt + 1))
        return False, "error"

    def sincronizar(self, origen: Path, destino: Path, modo: str = "espejo",
                    callback_progreso: Optional[Callable] = None,
                    callback_log: Optional[Callable] = None) -> Tuple[int, int, int]:
        if callback_log:
            callback_log(f"🚀 Iniciando sincronización ({modo.upper()})")
            callback_log(f"📂 Origen:  {origen}")
            callback_log(f"🎯 Destino: {destino}")

        origen = Path(origen).resolve()
        destino = Path(destino).resolve()
        if destino == origen or origen in destino.parents:
            if callback_log:
                callback_log("❌ Error: La carpeta destino no puede estar dentro de la carpeta origen.")
            return 0, 0, 1
       
        archivos_origen = []
        directorios_origen = []
        
        for root, dirs, files in os.walk(origen):
            dirs[:] = [d for d in dirs if not self._excluir_archivo(Path(root) / d)]
            
            # Recopilar carpetas para asegurar que se repliquen aunque estén vacías
            for d in dirs:
                r_dir = Path(root) / d
                if not self._excluir_archivo(r_dir):
                    directorios_origen.append(r_dir)

            for f in files:
                r = Path(root) / f
                if not self._excluir_archivo(r):
                    archivos_origen.append(r)

        carpetas_creadas = 0

        # Crear las carpetas en el destino antes de procesar los archivos e informarlo
        for dir_src in directorios_origen:
            rel_dir = dir_src.relative_to(origen)
            dir_dst = destino / rel_dir
            try:
                # Verificamos si la carpeta NO existe para registrar su creación
                if not dir_dst.exists():
                    dir_dst.mkdir(parents=True, exist_ok=True)
                    carpetas_creadas += 1
                    if callback_log:
                        callback_log(f"📁 Carpeta creada/movida: {rel_dir}")
                else:
                    dir_dst.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"No se pudo crear la carpeta {rel_dir}: {e}")

        total = len(archivos_origen)
        copiados, eliminados, errores = 0, 0, 0

        for idx, src in enumerate(archivos_origen, 1):
            rel = src.relative_to(origen)
            dst = destino / rel
            if callback_progreso:
                callback_progreso(idx, total, copiados, eliminados, errores, str(rel))
            
            # Recibimos el estado de la acción
            exito, estado = self._copiar_con_reintentos(src, dst, callback_log=callback_log)
            
            if exito:
                if estado == "copiado":
                    copiados += 1
                    if callback_log:
                        callback_log(f"➕ Copiado/Actualizado: {rel}")
            else:
                errores += 1
                if callback_log:
                    callback_log(f"❌ Error al copiar: {rel}")

        if modo in ("espejo", "bidireccional") and destino.exists():
            for root, _, files in os.walk(destino):
                for f in files:
                    r_dst = Path(root) / f
                    rel = r_dst.relative_to(destino)
                    src_correspondiente = origen / rel

                    if not src_correspondiente.exists():
                        if modo == "espejo":
                            try:
                                r_dst.unlink()
                                eliminados += 1
                                if callback_log:
                                    callback_log(f"🗑️ Eliminado de destino (Modo Espejo): {rel}")
                            except Exception as e:
                                logger.warning(f"No se pudo eliminar {r_dst}: {e}")
                                errores += 1
                        elif modo == "bidireccional":
                            exito, estado = self._copiar_con_reintentos(r_dst, src_correspondiente)
                            if exito:
                                if estado == "copiado":
                                    copiados += 1
                                    if callback_log:
                                        callback_log(f"🔄 Recuperado a Origen (Bidireccional): {rel}")
                            else:
                                errores += 1

            # --- Limpieza de directorios vacíos en modo espejo con REGISTRO DE LOGS ---
            if modo == "espejo":
                for root, dirs, files in os.walk(destino, topdown=False):
                    for d in dirs:
                        dir_dst = Path(root) / d
                        rel_dir = dir_dst.relative_to(destino)
                        src_dir = origen / rel_dir
                        # Si la carpeta ya no existe en el origen o está vacía
                        if not src_dir.exists():
                            try:
                                if not any(dir_dst.iterdir()):
                                    dir_dst.rmdir()
                                    eliminados += 1
                                    if callback_log:
                                        callback_log(f"🗑️ Carpeta eliminada/obsoleta en destino: {rel_dir}")
                            except Exception as e:
                                logger.warning(f"No se pudo eliminar la carpeta vacía {dir_dst}: {e}")

        if callback_log:
            callback_log(f"✅ Sincronización finalizada. Archivos copiados: {copiados}, Carpetas creadas: {carpetas_creadas}, Eliminados: {eliminados}, Errores: {errores}")
        return copiados, eliminados, errores
    def _rotar_backups(self, carpeta: Path, callback_log: Optional[Callable] = None) -> bool:
        """
        Rota los backups eliminando los más antiguos cuando se supera el límite max_backups.
        """
        backups = sorted(
            [f for f in carpeta.rglob("backup_*.zip*") if f.is_file()],
            key=lambda x: x.stat().st_mtime
        )
        
        while len(backups) >= self.max_backups:
            antiguo = backups.pop(0)
            try:
                antiguo.unlink()
                if callback_log: 
                    callback_log(f"♻️ Rotación: Eliminado backup antiguo {antiguo.name}")
            except Exception as e:
                logger.warning(f"No se pudo eliminar backup antiguo {antiguo}: {e}")
                
        return True

    def crear_backup_zip(self, carpeta_origen: Path, nombre_proyecto: str,
                         password: Optional[str] = None,
                         compression_level: int = DEFAULT_COMPRESSION_LEVEL,
                         callback_log: Optional[Callable] = None) -> Optional[Path]:
        if not carpeta_origen.exists():
            if callback_log: callback_log("❌ Error: La carpeta origen no existe.")
            return None

        carpeta_backups = DIR_BACKUPS / nombre_proyecto
        carpeta_backups.mkdir(parents=True, exist_ok=True)
        
        # Rotar archivos antiguos
        self._rotar_backups(carpeta_backups, callback_log)

        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_zip = f"backup_{nombre_proyecto}_{socket.gethostname()}_{fecha}.zip"
        ruta_zip = carpeta_backups / nombre_zip

        if callback_log:
            callback_log(f"📦 Generando paquete de respaldo completo: {ruta_zip.name}")

        try:
            with zipfile.ZipFile(ruta_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=compression_level) as zf:
                manifest = {
                    "version": VERSION,
                    "fecha_creacion": datetime.now().isoformat(),
                    "equipo": socket.gethostname(),
                    "origen": str(carpeta_origen),
                    "tipo": "completo",
                    "archivos": {}
                }
                
                for root, dirs, files in os.walk(carpeta_origen):
                    dirs[:] = [d for d in dirs if not self._excluir_archivo(Path(root) / d)]
                    for f in files:
                        ruta_archivo = Path(root) / f
                        if self._excluir_archivo(ruta_archivo):
                            continue
                            
                        arcname = str(ruta_archivo.relative_to(carpeta_origen))
                        stat = ruta_archivo.stat()
                        
                        zf.write(ruta_archivo, arcname)
                        manifest["archivos"][arcname] = {
                            "ruta": arcname,
                            "st_size": stat.st_size,
                            "st_mtime": stat.st_mtime,
                            "ubicacion_zip": nombre_zip,
                            "hash": SecurityUtils.calcular_hash(ruta_archivo) if self.config.get_opcion("verificar_hash", True) else ""
                        }

                zf.writestr("manifest_backup.json", json.dumps(manifest, indent=4))

            if password and CRYPTO_AVAILABLE:
                ruta_cifrada = ruta_zip.with_suffix(".zip.enc")
                if SecurityUtils.cifrar_archivo(ruta_zip, ruta_cifrada, password):
                    ruta_zip.unlink()
                    ruta_zip = ruta_cifrada
                    if callback_log:
                        callback_log("🔒 Respaldo cifrado correctamente con AES-256")
                else:
                    raise RuntimeError("Falló el proceso de cifrado AES.")

            if callback_log:
                callback_log(f"✅ Backup completo creado exitosamente en: {ruta_zip.name}")
            return ruta_zip
        except Exception as e:
            logger.error(f"Error creando backup zip: {e}")
            if callback_log: callback_log(f"❌ Error al crear backup: {e}")
            return None

    def restaurar_desde_backup(self, ruta_zip: Path, destino: Path, password: Optional[str] = None,
                               callback_log: Optional[Callable] = None) -> bool:
        if callback_log:
            callback_log(f"📥 Restaurando paquete completo: {ruta_zip.name} -> {destino}")

        temp_zip_file = None
        target_zip = ruta_zip

        if ruta_zip.suffix == ".enc":
            if not CRYPTO_AVAILABLE:
                if callback_log: callback_log("❌ Error: PyCryptodome no está instalado.")
                return False
            try:
                temp_zip_file = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
                temp_zip_path = Path(temp_zip_file.name)
                temp_zip_file.close()

                if not SecurityUtils.descifrar_archivo(ruta_zip, temp_zip_path, password):
                    raise ValueError("Error de descifrado. Clave incorrecta o archivo dañado.")
                target_zip = temp_zip_path
            except Exception as e:
                if callback_log: callback_log(f"❌ Error de descifrado: {e}")
                if temp_zip_file and Path(temp_zip_file.name).exists():
                    Path(temp_zip_file.name).unlink()
                return False

        try:
            destino_dir = destino.resolve()
            destino_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(target_zip, 'r') as zf:
                for member in zf.infolist():
                    if member.filename == "manifest_backup.json":
                        continue
                    target_path = (destino_dir / member.filename).resolve()
                    if str(target_path).startswith(str(destino_dir.resolve())):
                        zf.extract(member, destino_dir)

            if callback_log:
                callback_log("✅ Restauración completada con éxito.")
            return True
        except Exception as e:
            logger.error(f"Error restaurando desde backup: {e}")
            if callback_log: callback_log(f"❌ Error en la restauración: {e}")
            return False
        finally:
            if temp_zip_file and Path(temp_zip_file.name).exists():
                try:
                    Path(temp_zip_file.name).unlink()
                except Exception:
                    pass

# --- Interfaz Gráfica Mejorada (Tkinter) ---
if GUI_AVAILABLE:
    class Copy4MeGUI(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"{APP_NAME} - Sincronizador y Respaldo ({VERSION})")
            self.geometry("1150x820")
            self.minsize(950, 700)

            self.config = ConfigManager()
            self.engine = SyncEngine(self.config)
            self.ui_queue = queue.Queue()

            self._configurar_estilos()
            self._crear_interfaz()
            self._refresh_all()
            self.after(100, self._procesar_cola)
            self._detectar_usb()

        def _configurar_estilos(self):
            self.style = ttk.Style()
            self.style.theme_use('clam')

            # --- PALETA DE COLORES MEJORADA ---
            COLOR_FONDO_VENTANA = "#f8fafc"   # Blanco/Gris muy claro (Slate 50)
            COLOR_TARJETAS       = "#ffffff"   # Blanco puro para recuadros
            COLOR_TEXTO_TITULO  = "#1e293b"   # Azul/Gris muy oscuro para legibilidad
            COLOR_BOTON_PRIMARIO = "#2563eb"   # Azul moderno (Royal Blue)
            COLOR_SELECCION      = "#0d9488"   # Teal/Verde azulado para pestaña activa

            self.configure(bg=COLOR_FONDO_VENTANA)

            self.font_title = ("Segoe UI", 12, "bold")
            self.font_sub = ("Segoe UI", 9, "italic")
            self.font_bold = ("Segoe UI", 9, "bold")
            self.font_normal = ("Segoe UI", 9)

            # Configuración del Notebook (Pestañas)
            self.style.configure('TNotebook', background=COLOR_FONDO_VENTANA)
            self.style.configure('TNotebook.Tab', padding=[14, 8], font=("Segoe UI", 10, "bold"))
            self.style.map('TNotebook.Tab',
                background=[('selected', COLOR_SELECCION), ('!selected', '#e2e8f0')],
                foreground=[('selected', '#ffffff'), ('!selected', '#475569')]
            )

            # Configuración de los Tarjeteros (LabelFrames)
            self.style.configure('TLabelframe', background=COLOR_TARJETAS, relief="solid", borderwidth=1, bordercolor="#cbd5e1")
            self.style.configure('TLabelframe.Label', font=("Segoe UI", 10, "bold"), foreground=COLOR_TEXTO_TITULO, background=COLOR_TARJETAS)

            # Frames generales
            self.style.configure('TFrame', background=COLOR_FONDO_VENTANA)

            # Estilos nativos para widgets TTK si decides migrar
            self.style.configure('TLabel', background=COLOR_TARJETAS, foreground="#334155")
            self.style.configure('TRadiobutton', background=COLOR_TARJETAS, font=("Segoe UI", 9))
            self.style.configure('TCheckbutton', background=COLOR_TARJETAS, font=("Segoe UI", 9))
        
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

        def _crear_interfaz(self):
            header = ttk.Frame(self, padding=(15, 10))
            header.pack(fill=tk.X)
            ttk.Label(header, text=f"{APP_NAME} Enterprise", font=self.font_title, foreground="#2c3e50").pack(side=tk.LEFT)
            ttk.Label(header, text=f"Equipo Local: {socket.gethostname()} ({platform.system()})", font=self.font_sub).pack(side=tk.RIGHT)

            self.notebook = ttk.Notebook(self)
            self.notebook.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

            self.tab_respaldo = ttk.Frame(self.notebook, padding=15)
            self.notebook.add(self.tab_respaldo, text=" 📤 Respaldo (PC → USB/Carpeta) ")
            self._build_tab_respaldo()

            self.tab_restaurar = ttk.Frame(self.notebook, padding=15)
            self.notebook.add(self.tab_restaurar, text=" 📥 Restaurar (USB → PC) ")
            self._build_tab_restaurar()

            self.tab_backups = ttk.Frame(self.notebook, padding=15)
            self.notebook.add(self.tab_backups, text=" 🔍 Administrar Historial ")
            self._build_tab_backups()

            self.tab_config = ttk.Frame(self.notebook, padding=15)
            self.notebook.add(self.tab_config, text=" ⚙️ Ajustes de Sistema ")
            self._build_tab_config()

            bottom_panel = ttk.Frame(self, padding=(15, 5))
            bottom_panel.pack(fill=tk.BOTH, expand=True)

            status_frame = ttk.LabelFrame(bottom_panel, text=" Progreso de Tarea ", padding=10)
            status_frame.pack(fill=tk.X, pady=(0, 5))

            self.progress_bar = ttk.Progressbar(status_frame, orient="horizontal", mode="determinate")
            self.progress_bar.pack(fill=tk.X, pady=2)

            self.lbl_progreso = ttk.Label(status_frame, text="Estado: En espera", font=self.font_bold)
            self.lbl_progreso.pack(anchor=tk.W, pady=2)

            self.lbl_archivo_actual = ttk.Label(status_frame, text="", font=self.font_sub)
            self.lbl_archivo_actual.pack(anchor=tk.W)

            log_frame = ttk.LabelFrame(bottom_panel, text=" Consola de Registro y Depuración ", padding=5)
            log_frame.pack(fill=tk.BOTH, expand=True)

            self.log_text = scrolledtext.ScrolledText(
                log_frame, height=7, state='disabled',
                bg='#0f172a', fg='#38bdf8', font=("Consolas", 9)  # Fondo azul noche oscuro con texto azul neón
            )
            self.log_text.pack(fill=tk.BOTH, expand=True)

        def _build_tab_respaldo(self):
            card_origen = ttk.LabelFrame(self.tab_respaldo, text=" 1. Proyecto / Carpeta a respaldar ", padding=10)
            card_origen.pack(fill=tk.X, pady=5)

            row1 = ttk.Frame(card_origen)
            row1.pack(fill=tk.X)
            self.combo_perfiles = ttk.Combobox(row1, state="readonly", font=self.font_normal)
            self.combo_perfiles.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
            self.combo_perfiles.bind("<<ComboboxSelected>>", self._on_perfil_selected)

            ttk.Button(row1, text="📁 Seleccionar Carpeta...", command=self._browse_origen).pack(side=tk.LEFT, padx=2)
            ttk.Button(row1, text="❌ Borrar Perfil", command=self._eliminar_perfil).pack(side=tk.LEFT, padx=2)
            ttk.Button(row1, text="🔁 Repetir Acción", command=self._iniciar_respaldo).pack(side=tk.LEFT, padx=2) 
               
            self.lbl_ruta_origen = ttk.Label(card_origen, text="Ruta seleccionada: (Ninguna)", font=self.font_sub)
            self.lbl_ruta_origen.pack(anchor=tk.W, pady=(5, 0))

            card_destino = ttk.LabelFrame(self.tab_respaldo, text=" 2. Carpeta de Destino (USB / Dispositivo / Carpeta Personalizada) ", padding=10)
            card_destino.pack(fill=tk.X, pady=5)

            row_dest = ttk.Frame(card_destino)
            row_dest.pack(fill=tk.X)
            self.entry_destino_respaldo = ttk.Entry(row_dest, font=self.font_normal)
            self.entry_destino_respaldo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
            ttk.Button(row_dest, text="📁 Seleccionar Destino...", command=self._browse_destino_respaldo).pack(side=tk.LEFT)

            ttk.Label(card_destino, text="   (Si se deja en blanco, se detectará automáticamente un USB o la carpeta por defecto del sistema)", font=self.font_sub).pack(anchor=tk.W, pady=(5, 0))

            card_modos = ttk.LabelFrame(self.tab_respaldo, text=" 3. Selecciona la Modalidad de Sincronización ", padding=10)
            card_modos.pack(fill=tk.X, pady=5)

            self.var_modo_respaldo = tk.StringVar(value="incremental")

            r1 = ttk.Radiobutton(card_modos, text="Modo Normal / Directo", value="incremental", variable=self.var_modo_respaldo)
            r1.pack(anchor=tk.W)
            ttk.Label(card_modos, text="   Copia los archivos sin comprimir al directorio destino.", font=self.font_sub).pack(anchor=tk.W, pady=(0, 5))

            r2 = ttk.Radiobutton(card_modos, text="Modo Espejo (Sincronización Exacta)", value="espejo", variable=self.var_modo_respaldo)
            r2.pack(anchor=tk.W)
            ttk.Label(card_modos, text="   Mantiene la carpeta USB idéntica al PC. ¡ATENCIÓN! Eliminará en el USB lo que hayas borrado en tu PC.", font=self.font_sub).pack(anchor=tk.W, pady=(0, 5))

            r3 = ttk.Radiobutton(card_modos, text="Modo Bidireccional (Dos Vías)", value="bidireccional", variable=self.var_modo_respaldo)
            r3.pack(anchor=tk.W)
            ttk.Label(card_modos, text="   Combina los cambios de ambos lados. Si creaste un archivo en la USB, se copiará de vuelta al PC.", font=self.font_sub).pack(anchor=tk.W, pady=(0, 5))

            # --- NUEVA SECCIÓN DE ACCIONES A EJECUTAR ---
            card_acciones = ttk.LabelFrame(self.tab_respaldo, text=" 4. Tareas a Realizar ", padding=10)
            card_acciones.pack(fill=tk.X, pady=5)

            self.var_hacer_directo = tk.BooleanVar(value=True)
            self.var_hacer_zip = tk.BooleanVar(value=False)

            cb_directo = ttk.Checkbutton(card_acciones, text="📂 Sincronización Directa de Archivos (Descomprimida)", variable=self.var_hacer_directo)
            cb_directo.pack(anchor=tk.W)
            
            cb_zip = ttk.Checkbutton(card_acciones, text="📦 Crear Copia de Seguridad Comprimida (.ZIP)", variable=self.var_hacer_zip)
            cb_zip.pack(anchor=tk.W)

            card_seg = ttk.LabelFrame(self.tab_respaldo, text=" 5. Seguridad y Protección ", padding=10)
            card_seg.pack(fill=tk.X, pady=5)

            self.var_cifrar = tk.BooleanVar(value=False)
            cb_cifrar = ttk.Checkbutton(
                card_seg, text="🔒 Cifrar el paquete comprimido (AES-256)", variable=self.var_cifrar
            )
            cb_cifrar.pack(anchor=tk.W)
            ttk.Label(card_seg, text="   Solicitará una contraseña secreta. Requiere tener activada la opción de Crear Copia (.ZIP).", font=self.font_sub).pack(anchor=tk.W)

            btn_exec = ttk.Button(self.tab_respaldo, text="🚀 INICIAR RESPALDO AHORA", command=self._iniciar_respaldo)
            btn_exec.pack(anchor=tk.E, pady=10)

        def _build_tab_restaurar(self):
            # 1. Selección del proyecto en USB
            card_origen = ttk.LabelFrame(self.tab_restaurar, text=" 1. Proyecto a Recuperar desde USB ", padding=10)
            card_origen.pack(fill=tk.X, pady=5)

            row = ttk.Frame(card_origen)
            row.pack(fill=tk.X)

            # Quitamos state="readonly" para permitir ingresar una ruta manualmente
            self.combo_proyectos_restaurar = ttk.Combobox(row, font=self.font_normal) 
            self.combo_proyectos_restaurar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
            self.combo_proyectos_restaurar.bind("<<ComboboxSelected>>", self._on_proyecto_restaurar_selected)
            
            # Añadimos el botón de búsqueda manual
            ttk.Button(row, text="📁 Buscar Carpeta USB...", command=self._browse_origen_restaurar).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="🔍 Escanear Unidades USB", command=self._actualizar_lista_proyectos_usb).pack(side=tk.LEFT)

            # 2. Selección de la carpeta de destino local (PC)
            card_destino = ttk.LabelFrame(self.tab_restaurar, text=" 2. Carpeta Destino en el Ordenador (PC) ", padding=10)
            card_destino.pack(fill=tk.X, pady=10)

            row2 = ttk.Frame(card_destino)
            row2.pack(fill=tk.X)
            self.entry_destino_restaurar = ttk.Entry(row2, font=self.font_normal)
            self.entry_destino_restaurar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
            ttk.Button(row2, text="📁 Buscar...", command=self._browse_destino_restaurar).pack(side=tk.LEFT)

            # 3. Origen de restauración (Datos directos vs Copia ZIP)
            card_origen_datos = ttk.LabelFrame(self.tab_restaurar, text=" 3. ¿De dónde quieres restaurar? ", padding=10)
            card_origen_datos.pack(fill=tk.X, pady=5)

            self.var_modo_origen_restauracion = tk.StringVar(value="directo")

            r_directo = ttk.Radiobutton(
                card_origen_datos, 
                text="📂 Restaurar datos del USB (archivos directos sincronizados)", 
                value="directo", 
                variable=self.var_modo_origen_restauracion,
                command=self._toggle_origen_restauracion
            )
            r_directo.pack(anchor=tk.W)
            ttk.Label(card_origen_datos, text="   Copia la estructura de carpetas y archivos visibles directamente desde el USB al PC.", font=self.font_sub).pack(anchor=tk.W, pady=(0, 5))

            r_zip = ttk.Radiobutton(
                card_origen_datos, 
                text="📦 Restaurar desde una copia guardada (.zip / .zip.enc)", 
                value="zip", 
                variable=self.var_modo_origen_restauracion,
                command=self._toggle_origen_restauracion
            )
            r_zip.pack(anchor=tk.W)
            ttk.Label(card_origen_datos, text="   Descomprime un paquete de copia de seguridad histórico guardado en el USB.", font=self.font_sub).pack(anchor=tk.W, pady=(0, 5))

            # Selector de archivo ZIP (se activa solo si selecciona la opción ZIP)
            self.frame_selector_zip = ttk.Frame(card_origen_datos, padding=(20, 5, 0, 0))
            ttk.Label(self.frame_selector_zip, text="Seleccionar archivo de backup:").pack(side=tk.LEFT, padx=(0, 5))
            self.combo_zips_disponibles = ttk.Combobox(self.frame_selector_zip, state="readonly", width=45)
            self.combo_zips_disponibles.pack(side=tk.LEFT, fill=tk.X, expand=True)

            ttk.Button(self.tab_restaurar, text="📥 INICIAR RESTAURACIÓN EN PC", command=self._iniciar_restauracion).pack(anchor=tk.E, pady=15)

        def _toggle_origen_restauracion(self):
            """Muestra u oculta la selección de archivos ZIP según la opción elegida."""
            if self.var_modo_origen_restauracion.get() == "zip":
                self.frame_selector_zip.pack(fill=tk.X)
            else:
                self.frame_selector_zip.pack_forget()

        def _on_proyecto_restaurar_selected(self, event):
            nombre = self.combo_proyectos_restaurar.get()
            if not nombre:
                return

            carpeta_usb = USBDetector.buscar_proyecto_en_usb(nombre)
            if carpeta_usb:
                # CORRECCIÓN: Buscar los zips dentro de la carpeta del proyecto (carpeta_usb), no en parent
                zips = sorted([f for f in carpeta_usb.rglob("backup_*.zip*") if f.is_file()], key=lambda x: x.stat().st_mtime, reverse=True)
                self.combo_zips_disponibles['values'] = [f.name for f in zips]
                if zips:
                    self.combo_zips_disponibles.current(0)
                else:
                    self.combo_zips_disponibles['values'] = ["No hay copias .zip encontradas"]

        def _iniciar_restauracion(self):
            nombre_o_ruta = self.combo_proyectos_restaurar.get()
            if not nombre_o_ruta:
                messagebox.showerror("Error", "Seleccione o busque un proyecto para restaurar.")
                return

            destino_str = self.entry_destino_restaurar.get().strip()
            if not destino_str:
                messagebox.showerror("Error", "Especifique una carpeta de destino en su PC.")
                return

            destino = Path(destino_str)
            
            # Validar si el texto introducido es una ruta absoluta válida seleccionada manualmente
            if Path(nombre_o_ruta).is_absolute() and Path(nombre_o_ruta).exists():
                origen_usb = Path(nombre_o_ruta)
            else:
                origen_usb = USBDetector.buscar_proyecto_en_usb(nombre_o_ruta)

            if self.var_modo_origen_restauracion.get() == "directo":
                if not origen_usb or not origen_usb.exists():
                    messagebox.showerror("Error", "No se encontró la carpeta del proyecto en el USB.")
                    return

                if messagebox.askyesno("Confirmar", f"Restaurar directamente desde:\n{origen_usb}\nHacia:\n{destino}"):
                    threading.Thread(
                        target=self._worker_restauracion_directa, 
                        args=(origen_usb, destino), 
                        daemon=True
                    ).start()

            else: # Modo ZIP
                zip_nombre = self.combo_zips_disponibles.get()
                if not zip_nombre or "No hay copias" in zip_nombre:
                    messagebox.showerror("Error", "Seleccione un archivo comprimido válido.")
                    return

                # CORRECCIÓN: Apuntar directamente a origen_usb / zip_nombre
                ruta_zip = origen_usb / zip_nombre if origen_usb else None
                if not ruta_zip or not ruta_zip.exists():
                    messagebox.showerror("Error", f"No se encontró el archivo {zip_nombre} en la unidad USB.")
                    return

                password = None
                if ruta_zip.suffix == ".enc":
                    password = simpledialog.askstring("Clave Requerida", "Ingrese la clave para descifrar el backup:", show='*')
                    if password is None:
                        return

                if messagebox.askyesno("Confirmar", f"Descomprimir paquete:\n{zip_nombre}\nHacia:\n{destino}"):
                    threading.Thread(
                        target=self._worker_restauracion_backup, 
                        args=(ruta_zip, destino, password), 
                        daemon=True
                    ).start()

        def _worker_restauracion_directa(self, origen, destino):
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(10)
            try:
                # Notificar visualmente en la barra de estados
                self.ui_queue.put(("status_text", ("Calculando tamaño de archivos para restaurar...",)))
                self.log_gui("📊 Verificando tamaño de origen y espacio disponible en la partición del PC...")
                
                # --- LLAMADA A LA VALIDACIÓN ---
                es_valido, mensaje = validar_espacio_disponible(origen, destino)
                if not es_valido:
                    self.ui_queue.put(("msgbox_error", ("Espacio Insuficiente en PC", f"No se puede restaurar:\n\n{mensaje}")))
                    return

                copiados, eliminados, errores = self.engine.sincronizar(
                    origen, destino, modo="incremental", callback_log=self.log_gui
                )
                self.ui_queue.put(("msgbox", ("Restauración Completada", f"Se han copiado los datos directamente desde el USB al PC.\nArchivos copiados: {copiados}\nErrores: {errores}")))
            except Exception as e:
                self.ui_queue.put(("msgbox_error", ("Error", f"Fallo al restaurar: {e}")))
            finally:
                self.ui_queue.put(("stop_progress", None))
                self.ui_queue.put(("refresh", None))

        def _build_tab_backups(self):
            ttk.Label(self.tab_backups, text="Puntos de Restauración Comprimidos Almacenados:", font=self.font_bold).pack(anchor=tk.W, pady=5)

            self.tree_backups = ttk.Treeview(self.tab_backups, columns=("Fecha", "Tamaño", "Formato"), show="tree headings")
            self.tree_backups.heading("#0", text="Proyecto / Archivo Backup")
            self.tree_backups.heading("Fecha", text="Fecha de Creación")
            self.tree_backups.heading("Tamaño", text="Tamaño del Archivo")
            self.tree_backups.heading("Formato", text="Estado de Cifrado")

            self.tree_backups.column("#0", width=350)
            self.tree_backups.column("Fecha", width=180)
            self.tree_backups.column("Tamaño", width=120)
            self.tree_backups.column("Formato", width=120)

            self.tree_backups.pack(fill=tk.BOTH, expand=True, pady=5)

            btn_bar = ttk.Frame(self.tab_backups)
            btn_bar.pack(fill=tk.X, pady=5)

            ttk.Button(btn_bar, text="🗑️ Eliminar Backup Seleccionado", command=self._eliminar_backup).pack(side=tk.LEFT)
            ttk.Button(btn_bar, text="🔄 Actualizar Lista", command=self._refresh_all).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_bar, text="📂 Abrir Carpeta de Respaldos", command=self._abrir_carpeta_backups).pack(side=tk.RIGHT)

        def _build_tab_config(self):
            card_conf = ttk.LabelFrame(self.tab_config, text=" Parámetros de Rendimiento e Integridad ", padding=10)
            card_conf.pack(fill=tk.X, pady=5)

            row = ttk.Frame(card_conf)
            row.pack(anchor=tk.W, pady=5)
            ttk.Label(row, text="Nivel de Compresión ZIP (0 = Ninguno, 9 = Máximo): ").pack(side=tk.LEFT)
            self.combo_compresion = ttk.Combobox(row, values=list(range(10)), state="readonly", width=5)
            self.combo_compresion.set(str(self.config.get_opcion("compresion", DEFAULT_COMPRESSION_LEVEL)))
            self.combo_compresion.pack(side=tk.LEFT, padx=5)
            self.combo_compresion.bind("<<ComboboxSelected>>", lambda e: self.config.set_opcion("compresion", int(self.combo_compresion.get())))

            self.var_verificar_hash = tk.BooleanVar(value=self.config.get_opcion("verificar_hash", True))
            ttk.Checkbutton(
                card_conf, text="✅ Verificación estricta de integridad de datos (SHA-256)", variable=self.var_verificar_hash,
                command=lambda: self.config.set_opcion("verificar_hash", self.var_verificar_hash.get())
            ).pack(anchor=tk.W, pady=5)

            card_excl = ttk.LabelFrame(self.tab_config, text=" Exclusiones Globales ", padding=10)
            card_excl.pack(fill=tk.X, pady=10)

            ttk.Label(card_excl, text="Extensiones a omitir durante la copia (separadas por coma):").pack(anchor=tk.W)
            self.entry_excluir = ttk.Entry(card_excl, font=self.font_normal)
            self.entry_excluir.insert(0, ", ".join(self.config.get_opcion("excluir_patrones", [])))
            self.entry_excluir.pack(fill=tk.X, pady=5)
            ttk.Button(card_excl, text="Guardar Exclusiones", command=self._guardar_patrones).pack(anchor=tk.W)

            card_limits = ttk.LabelFrame(self.tab_config, text=" Retención de Historial ", padding=10)
            card_limits.pack(fill=tk.X, pady=5)

            ttk.Label(card_limits, text="Número máximo de backups automáticos conservados por proyecto:").pack(anchor=tk.W)
            self.spin_max_backups = tk.Spinbox(card_limits, from_=1, to=100, width=8, font=self.font_normal)
            self.spin_max_backups.delete(0, tk.END)
            self.spin_max_backups.insert(0, str(self.config.get_opcion("max_backups", MAX_BACKUPS)))
            self.spin_max_backups.bind("<FocusOut>", lambda e: self.config.set_opcion("max_backups", int(self.spin_max_backups.get())))
            self.spin_max_backups.pack(anchor=tk.W, pady=5)

            ttk.Button(self.tab_config, text="📋 Inspeccionar Archivo config.json", command=self._ver_config_json).pack(anchor=tk.W, pady=10)

        def _actualizar_sugerencia_destino(self, nombre_proyecto: str):
            destino_usb = USBDetector.buscar_proyecto_en_usb(nombre_proyecto)
            ruta_sugerida = str(destino_usb) if destino_usb else str(DIR_BACKUPS / nombre_proyecto / "MASTER")
            self.entry_destino_respaldo.delete(0, tk.END)
            self.entry_destino_respaldo.insert(0, ruta_sugerida)

        def _on_perfil_selected(self, event):
            nombre = self.combo_perfiles.get()
            perfil = self.config.get_perfil(nombre)
            if perfil:
                self.lbl_ruta_origen.config(text=f"Ruta seleccionada: {perfil.get('ruta_local', '')}")
                ruta_destino_guardada = perfil.get("ruta_destino", "")
                
                self.entry_destino_respaldo.delete(0, tk.END)
                if ruta_destino_guardada:
                    self.entry_destino_respaldo.insert(0, ruta_destino_guardada)
                else:
                    self._actualizar_sugerencia_destino(nombre)
                    
                ultimo_modo = perfil.get("ultimo_modo")
                if ultimo_modo in ["incremental", "espejo", "bidireccional"]:
                    self.var_modo_respaldo.set(ultimo_modo)
               

        def _browse_origen(self):
            folder = filedialog.askdirectory(title="Selecciona la carpeta raíz a respaldar")
            if folder:
                folder_path = Path(folder)
                
                # --- CAMBIO: Sugerir nombre compuesto y pedir confirmación ---
                nombre_sugerido = f"{folder_path.name} ({folder_path.parent.name})"
                nombre = simpledialog.askstring(
                    "Nombre del Perfil", 
                    "Ingrese un nombre único para este perfil:", 
                    initialvalue=nombre_sugerido
                )
                
                if not nombre:
                    return # Si el usuario cancela, detenemos el proceso
                # --- FIN DEL CAMBIO ---
                
                self.config.set_perfil(nombre, folder_path)
                self._refresh_all()
                self.combo_perfiles.set(nombre)
                self.lbl_ruta_origen.config(text=f"Ruta seleccionada: {folder}")
                self._actualizar_sugerencia_destino(nombre)

        def _browse_destino_respaldo(self):
            folder = filedialog.askdirectory(title="Selecciona la carpeta de destino para el respaldo")
            if folder:
                self.entry_destino_respaldo.delete(0, tk.END)
                self.entry_destino_respaldo.insert(0, folder)

        def _eliminar_perfil(self):
            nombre = self.combo_perfiles.get()
            if nombre and messagebox.askyesno("Confirmar eliminación", f"¿Desea eliminar el perfil '{nombre}' del sistema?"):
                self.config.delete_perfil(nombre)
                self.entry_destino_respaldo.delete(0, tk.END)
                self._refresh_all()

        def _iniciar_respaldo(self):
            nombre = self.combo_perfiles.get()
            perfil = self.config.get_perfil(nombre)
            if not perfil:
                messagebox.showerror("Atención", "Por favor, seleccione o agregue una carpeta de origen.")
                return
            origen = Path(perfil["ruta_local"])
            if not origen.exists():
                messagebox.showerror("Error de Ruta", f"La carpeta local no existe:\n{origen}")
                return

            destino_str = self.entry_destino_respaldo.get().strip()
            if destino_str:
                destino = Path(destino_str)
            else:
                destino_usb = USBDetector.buscar_proyecto_en_usb(nombre)
                if destino_usb:
                    destino = destino_usb
                else:
                    destino = DIR_BACKUPS / nombre / "MASTER"

            modo = self.var_modo_respaldo.get()
            hacer_directo = self.var_hacer_directo.get()
            hacer_zip = self.var_hacer_zip.get()
            cifrar = self.var_cifrar.get()

            if not hacer_directo and not hacer_zip:
                messagebox.showwarning("Atención", "Debe seleccionar al menos una tarea a realizar (Sincronización Directa o Copia .ZIP).")
                return

            compression_level = int(self.combo_compresion.get())
            password = None

            if cifrar:
                if not hacer_zip:
                    messagebox.showwarning("Atención", "El cifrado requiere activar la casilla 'Crear Copia de Seguridad Comprimida (.ZIP)'.")
                    return
                if not CRYPTO_AVAILABLE:
                    messagebox.showerror("Componente Faltante", "Instale 'pycryptodome' para usar la función de cifrado.")
                    return
                password = simpledialog.askstring("Protección por Contraseña", "Introduzca la clave secreta para el cifrado AES:", show='*')
                if not password:
                    return

            try:
                tamano_bytes = sum(f.stat().st_size for f in origen.rglob('*') if f.is_file()) if origen.is_dir() else origen.stat().st_size
                tam_legible = formatear_tamano(tamano_bytes)
            except Exception:
                tam_legible = "Calculando..."

            tareas = []
            if hacer_directo: tareas.append(f"• Sincronización directa ({modo.upper()})")
            if hacer_zip: tareas.append("• Creación de archivo ZIP" + (" cifrado" if cifrar else ""))

            tareas_str = "\n".join(tareas)

            msg = f"¿Desea iniciar las siguientes operaciones?\n\n{tareas_str}\n\n• Proyecto: {nombre}\n• Tamaño estimado: {tam_legible}\n• Origen: {origen}\n• Destino: {destino}"
            if not messagebox.askyesno("Confirmación de Operación", msg):
                return
                
            self.progress_bar.stop()
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(10)
            self.lbl_progreso.config(text="Estado: Analizando directorios y preparando respaldo...")
            self.lbl_archivo_actual.config(text="Por favor espere...")

            threading.Thread(
                target=self._worker_respaldo,
                args=(nombre, origen, destino, modo, password, compression_level, hacer_directo, hacer_zip),
                daemon=True
            ).start()
            
        def _worker_respaldo(self, nombre, origen, destino, modo, password, compression_level, hacer_directo, hacer_zip):
            # Lista para almacenar el historial de cambios detallados
            cambios_detallados = []

            def cb_progreso(idx, total, cop, del_, err, arch):
                self.ui_queue.put(("set_determinate", (total,)))
                self.ui_queue.put(("progress", (idx, total, cop, del_, err, arch)))

            def cb_log_custom(msg):
                self.log_gui(msg)
                self.ui_queue.put(("status_text", (msg,)))
                # Guardamos los eventos relevantes para el reporte emergente
                if any(icon in msg for icon in ["➕", "📁", "🗑️", "🔄", "❌"]):
                    cambios_detallados.append(msg)

            try:
                copiados, eliminados, errores = 0, 0, 0

                # 1. EJECUCIÓN DE COPIA / SINCRONIZACIÓN DIRECTA
                if hacer_directo:
                    cb_log_custom("📊 Verificando espacio disponible para copia directa...")
                    es_valido, mensaje = validar_espacio_disponible(origen, destino)
                    if not es_valido:
                        self.ui_queue.put(("msgbox_error", ("Espacio Insuficiente", f"No se puede realizar la copia directa:\n\n{mensaje}")))
                        return

                    copiados, eliminados, errores = self.engine.sincronizar(
                        origen, destino, modo,
                        callback_progreso=cb_progreso,
                        callback_log=cb_log_custom
                    )

                    self.config.set_perfil(
                        nombre, origen, ruta_destino=destino, 
                        metadatos={"ultima_sincronizacion": datetime.now().isoformat(), "ultimo_modo": modo}
                    )

                # 2. EJECUCIÓN DE RESPALDO EN ZIP
                if hacer_zip:
                    cb_log_custom("📦 Generando paquete comprimido ZIP de respaldo...")
                    
                    if not hacer_directo:
                        es_valido, mensaje = validar_espacio_disponible(origen, DIR_BACKUPS)
                        if not es_valido:
                            self.ui_queue.put(("msgbox_error", ("Espacio Insuficiente", f"No se puede crear el archivo ZIP:\n\n{mensaje}")))
                            return

                    archivo_zip = self.engine.crear_backup_zip(origen, nombre, password, compression_level, cb_log_custom)
                    if not archivo_zip:
                        errores += 1

                # 3. NOTIFICACIÓN CON VENTANA EMERGENTE DETALLADA
                resumen_header = f"Operación completada en '{nombre}'\n"
                if hacer_directo:
                    resumen_header += f"• Archivos procesados: {copiados} | Eliminados: {eliminados} | Errores: {errores}\n"
                if hacer_zip:
                    resumen_header += "• Paquete comprimido (.ZIP) creado con éxito.\n"

                if cambios_detallados:
                    detalle_texto = "\n".join(cambios_detallados)
                else:
                    detalle_texto = "No hubo cambios requeridos (los archivos y carpetas ya estaban al día)."

                # Enviar orden de abrir la ventana de reporte detallado
                self.ui_queue.put(("mostrar_reporte_detallado", (resumen_header, detalle_texto)))

            except Exception as e:
                self.ui_queue.put(("msgbox_error", ("Error Crítico", f"Ocurrió un error durante el proceso:\n{e}")))
            finally:
                self.ui_queue.put(("stop_progress", None))
                self.ui_queue.put(("refresh", None))
                
        def _actualizar_lista_proyectos_usb(self):
            proyectos = set()
            for usb in USBDetector.listar_unidades_extraibles():
                backup_dir = usb / "copy4me_backups"
                if backup_dir.exists():
                    for d in backup_dir.iterdir():
                        if d.is_dir():
                            proyectos.add(d.name)
            self.combo_proyectos_restaurar['values'] = sorted(proyectos)
            if proyectos:
                self.log_gui(f"🔍 Proyectos encontrados en USB: {', '.join(proyectos)}")

        def _browse_destino_restaurar(self):
            folder = filedialog.askdirectory(title="Seleccione la carpeta donde restaurar los datos")
            if folder:
                self.entry_destino_restaurar.delete(0, tk.END)
                self.entry_destino_restaurar.insert(0, folder)
        
        def _browse_origen_restaurar(self):
            folder = filedialog.askdirectory(title="Seleccione la carpeta en el USB que contiene los datos sincronizados")
            if folder:
                self.combo_proyectos_restaurar.set(folder)
                
        def _worker_restauracion_backup(self, ruta_zip, destino, password):
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(10)
            try:
                self.ui_queue.put(("status_text", ("Calculando tamaño de archivos a descomprimir...",)))
                self.log_gui("📊 Verificando tamaño descomprimido y espacio disponible en el PC...")
                
                # 1. Calculamos el tamaño real sin comprimir
                tam_real = calcular_tamano_descomprimido(ruta_zip)
                
                # 2. Validamos el espacio pasándole el tamaño exacto
                es_valido, mensaje = validar_espacio_disponible(ruta_zip, destino, tamano_total=tam_real)
                if not es_valido:
                    self.ui_queue.put(("msgbox_error", ("Espacio Insuficiente en PC", f"No se puede descomprimir el respaldo:\n\n{mensaje}")))
                    return

                exito = self.engine.restaurar_desde_backup(ruta_zip, destino, password, self.log_gui)
                if exito:
                    self.ui_queue.put(("msgbox", ("Éxito", "Restauración desde paquete ZIP completada.")))
            except Exception as e:
                self.ui_queue.put(("msgbox_error", ("Error", f"Fallo en la restauración: {e}")))
            finally:
                self.ui_queue.put(("stop_progress", None))
                self.ui_queue.put(("refresh", None))

        def _eliminar_backup(self):
            sel = self.tree_backups.selection()
            if not sel:
                return
            item = sel[0]
            parent = self.tree_backups.parent(item)
            if parent:
                proyecto = self.tree_backups.item(parent, 'text')
                nombre_archivo = self.tree_backups.item(item, 'text')
                ruta_archivo = DIR_BACKUPS / proyecto / nombre_archivo
                if ruta_archivo.exists() and messagebox.askyesno("Confirmar borrado", f"¿Desea eliminar permanentemente {nombre_archivo}?"):
                    ruta_archivo.unlink()
                    self._refresh_all()

        def _abrir_carpeta_backups(self):
            if DIR_BACKUPS.exists():
                if platform.system() == "Windows":
                    os.startfile(str(DIR_BACKUPS))
                elif platform.system() == "Darwin":
                    os.system(f'open "{DIR_BACKUPS}"')
                else:
                    os.system(f'xdg-open "{DIR_BACKUPS}"')

        def _guardar_patrones(self):
            patrones = [p.strip().lstrip('.') for p in self.entry_excluir.get().split(',') if p.strip()]
            self.config.set_opcion("excluir_patrones", patrones)
            messagebox.showinfo("Guardado", "Patrones de exclusión actualizados.")

        def _ver_config_json(self):
            ventana = tk.Toplevel(self)
            ventana.title("Visor de Archivo config.json")
            ventana.geometry("600x400")
            text = scrolledtext.ScrolledText(ventana, wrap=tk.WORD, font=("Consolas", 10))
            text.pack(fill=tk.BOTH, expand=True)
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    text.insert(tk.END, f.read())
            except Exception as e:
                text.insert(tk.END, f"Error al leer la configuración: {e}")
            text.config(state='disabled')

        def _refresh_all(self):
            perfiles = self.config._data.get("perfiles", {})
            self.combo_perfiles['values'] = sorted(perfiles.keys())
            self._actualizar_lista_proyectos_usb()

            for item in self.tree_backups.get_children():
                self.tree_backups.delete(item)

            if DIR_BACKUPS.exists():
                for p in sorted(DIR_BACKUPS.iterdir()):
                    if p.is_dir():
                        node = self.tree_backups.insert("", tk.END, text=p.name, open=True)
                        for b in sorted(p.glob("backup_*"), key=lambda x: x.stat().st_mtime, reverse=True):
                            if b.is_file():
                                fecha = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                                tam = self._obtener_tamano_formateado(b)
                                estado = "🔒 Cifrado AES" if b.suffix == ".enc" else "📦 ZIP Estándar"
                                self.tree_backups.insert(node, tk.END, text=b.name, values=(fecha, tam, estado))

        def _obtener_tamano_formateado(self, ruta: Path) -> str:
            try:
                size = float(ruta.stat().st_size)
                for unit in ['B', 'KB', 'MB', 'GB']:
                    if size < 1024.0:
                        return f"{size:.2f} {unit}"
                    size /= 1024.0
                return f"{size:.2f} TB"
            except Exception:
                return "0 B"

        def _detectar_usb(self):
            usb = USBDetector.listar_unidades_extraibles()
            if usb:
                self.log_gui(f"🔌 Unidades detectadas: {', '.join(str(u) for u in usb)}")
            else:
                self.log_gui("ℹ️ No se detectaron unidades externas al iniciar.")

        def _mostrar_ventana_reporte(self, encabezado: str, detalle: str):
            """Crea una ventana emergente personalizada con barra de desplazamiento para ver todos los cambios."""
            ventana = tk.Toplevel(self)
            ventana.title("Resumen Detallado de Operaciones")
            ventana.geometry("850x600")
            ventana.transient(self)
            ventana.grab_set()

            # Encabezado
            lbl_info = ttk.Label(ventana, text=encabezado, font=self.font_bold, justify=tk.LEFT)
            lbl_info.pack(anchor=tk.W, padx=15, pady=(15, 5))

            ttk.Label(ventana, text="Detalle de cambios realizados:", font=self.font_normal).pack(anchor=tk.W, padx=15, pady=(5, 5))

            # Cuadro de texto con desplazamiento para mostrar todos los cambios
            frame_txt = ttk.Frame(ventana, padding=(15, 0, 15, 10))
            frame_txt.pack(fill=tk.BOTH, expand=True)

            txt_reporte = scrolledtext.ScrolledText(
                frame_txt, wrap=tk.WORD, font=("Consolas", 9),
                bg="#0f172a", fg="#34d399"  # Fondo azul noche con texto verde esmeralda neón
            )
            txt_reporte.insert(tk.END, detalle)
            txt_reporte.config(state='disabled')
            txt_reporte.pack(fill=tk.BOTH, expand=True)

            # Botón de cierre
            btn_cerrar = ttk.Button(ventana, text="Entendido / Cerrar", command=ventana.destroy)
            btn_cerrar.pack(pady=10)        

# --- Modo Consola / TUI (Fallback) ---
def modo_tui():
    config = ConfigManager()
    engine = SyncEngine(config)

    def log_tui(msg):
        print(msg)

    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        print(BANNER)
        print(f"\n{Color.AMARILLO}⚠️ Modo Consola / TUI Activado{Color.RESET}\n")
        print(" Menú de Opciones:")
        print(" 1. 📤 Respaldo (PC → USB/Carpeta)")
        print(" 2. 📥 Restaurar (USB → PC)")
        print(" 3. 🔍 Administrar Historial de Backups")
        print(" 4. ⚙️ Ver Configuración")
        print(" 5. 🔌 Detectar Dispositivos USB")
        print(" 6. ❌ Salir")

        opcion = input("\nSeleccione una opción [1-6]: ").strip()
        if opcion == "1":
            perfiles = config._data.get("perfiles", {})
            if perfiles:
                print("\nPerfiles guardados:")
                for idx, (k, v) in enumerate(perfiles.items(), 1):
                    print(f"  {idx}. {k} -> {v.get('ruta_local', '')}")
                print("  0. Crear un nuevo perfil")
                sel = input("Seleccione: ").strip()
                if sel == "0":
                    ruta = input("Ruta origen local: ").strip()
                    if not Path(ruta).exists():
                        print("❌ Ruta inválida.")
                        input("Presione ENTER...")
                        continue
                    nombre = Path(ruta).name
                    config.set_perfil(nombre, Path(ruta))
                else:
                    try:
                        idx = int(sel) - 1
                        nombre = list(perfiles.keys())[idx]
                        ruta = perfiles[nombre].get("ruta_local")
                    except Exception:
                        print("❌ Selección no válida.")
                        input("Presione ENTER...")
                        continue
            else:
                ruta = input("Ruta origen local: ").strip()
                if not Path(ruta).exists():
                    print("❌ Ruta inválida.")
                    input("Presione ENTER...")
                    continue
                nombre = Path(ruta).name
                config.set_perfil(nombre, Path(ruta))

            origen = Path(ruta)
            destino_usb = USBDetector.buscar_proyecto_en_usb(nombre)
            destino_default = destino_usb if destino_usb else (DIR_BACKUPS / nombre / "MASTER")
            
            print(f"\nDestino sugerido / por defecto: {destino_default}")
            dest_input = input("Ruta de destino personalizada (ENTER para usar la sugerida): ").strip()
            
            if dest_input:
                destino = Path(dest_input)
            else:
                destino = destino_default

            print("\nModos de Sincronización: [1] Incremental | [2] Espejo | [3] Bidireccional")
            m_input = input("Seleccione Modo (1-3) [Defecto 1]: ").strip()
            modo = "incremental"
            if m_input == "2":
                modo = "espejo"
            elif m_input == "3":
                modo = "bidireccional"

            cifrar = input("¿Cifrar respaldo con clave AES-256? (s/N): ").strip().lower() == 's'
            password = None
            if cifrar:
                if not CRYPTO_AVAILABLE:
                    print("❌ PyCryptodome no está disponible.")
                    input("Presione ENTER...")
                    continue
                password = getpass.getpass("Ingrese contraseña de cifrado: ")

            if input(f"\n¿Confirmar respaldo del proyecto '{nombre}' en '{destino}'? (s/N): ").strip().lower() == 's':
                tamano_bytes = calcular_tamano_origen(origen)
                tam_legible = formatear_tamano(tamano_bytes)
                print(f"📊 Tamaño total a procesar: {tam_legible}\n")

                hacer_directo = input("¿Desea realizar la copia/sincronización de carpetas directa? (S/n): ").strip().lower() != 'n'
                hacer_zip = input("¿Desea crear una copia comprimida en ZIP? (s/N): ").strip().lower() == 's'

                if hacer_directo:
                    engine.sincronizar(origen, destino, modo, callback_log=log_tui)
                    config.set_perfil(nombre, origen, ruta_destino=destino)

                if hacer_zip:
                    engine.crear_backup_zip(origen, nombre, password, config.get_opcion("compresion", 6), log_tui)

                input("\nPresione ENTER para continuar...")

        elif opcion == "2":
            proyectos = []
            for usb in USBDetector.listar_unidades_extraibles():
                backup_dir = usb / "copy4me_backups"
                if backup_dir.exists():
                    proyectos.extend([d.name for d in backup_dir.iterdir() if d.is_dir()])

            if not proyectos:
                print("❌ No se encontraron proyectos en unidades USB.")
                input("Presione ENTER...")
                continue

            print("\nProyectos disponibles:")
            for idx, p in enumerate(proyectos, 1):
                print(f"  {idx}. {p}")
            sel = input("Seleccione proyecto: ").strip()
            try:
                nombre = proyectos[int(sel) - 1]
            except Exception:
                print("❌ Opción inválida.")
                input("Presione ENTER...")
                continue

            destino_str = input("Ruta de destino en el PC: ").strip()
            if not destino_str:
                print("❌ Ruta no válida.")
                input("Presione ENTER...")
                continue

            destino = Path(destino_str)
            origen_usb = USBDetector.buscar_proyecto_en_usb(nombre) or (DIR_BACKUPS / nombre / "MASTER")

            usar_backup = input("¿Restaurar desde paquete ZIP? (s/N): ").strip().lower() == 's'
            if usar_backup:
                backups = sorted(origen_usb.parent.glob("backup_*.zip*"), key=lambda x: x.stat().st_mtime, reverse=True)
                if not backups:
                    print("❌ No hay archivos ZIP de backup.")
                    input("Presione ENTER...")
                    continue
                zip_sel = backups[0]
                password = None
                if zip_sel.suffix == ".enc":
                    password = getpass.getpass("Ingrese contraseña para descifrar: ")
                engine.restaurar_desde_backup(zip_sel, destino, password, log_tui)
            else:
                modo = "espejo" if input("¿Utilizar Modo Espejo en la restauración? (s/N): ").strip().lower() == 's' else "incremental"
                engine.sincronizar(origen_usb, destino, modo, callback_log=log_tui)

            input("\nPresione ENTER para continuar...")

        elif opcion == "3":
            if DIR_BACKUPS.exists():
                for p in DIR_BACKUPS.iterdir():
                    if p.is_dir():
                        print(f"\n📂 Proyecto: {p.name}")
                        for b in sorted(p.glob("backup_*"), key=lambda x: x.stat().st_mtime, reverse=True):
                            fecha = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                            tam = b.stat().st_size // 1024
                            print(f"   └── {b.name} | {fecha} | {tam} KB")
            else:
                print("No hay historial de backups registrados.")
            input("\nPresione ENTER para continuar...")

        elif opcion == "4":
            print("\nConfiguración actual:")
            print(json.dumps(config._data, indent=4, ensure_ascii=False))
            input("\nPresione ENTER para continuar...")

        elif opcion == "5":
            usb = USBDetector.listar_unidades_extraibles()
            if usb:
                print("Unidades detectadas:")
                for u in usb:
                    print(f"  └── {u}")
            else:
                print("No se encontraron unidades externas conectadas.")
            input("\nPresione ENTER para continuar...")

        elif opcion == "6":
            print("👋 Saliendo de Copy4Me. ¡Hasta pronto!")
            sys.exit(0)

# --- Punto de Entrada del Ejecutable ---
if __name__ == "__main__":
    if GUI_AVAILABLE:
        try:
            app = Copy4MeGUI()
            app.mainloop()
        except Exception as e:
            logger.error(f"Error fatal iniciando interfaz gráfica, pasando a modo consola: {e}")
            modo_tui()
    else:
        modo_tui()