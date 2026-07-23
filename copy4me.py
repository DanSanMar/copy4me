import os
import sys
import shutil
import logging
import zipfile
import json
import socket
import platform
import threading
import queue
import time
from datetime import datetime
from pathlib import Path

# --- CARGA DEFENSIVA DE GUI (FALLBACK A TUI) ---
GUI_DISPONIBLE = False
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    GUI_DISPONIBLE = True
except ImportError:
    GUI_DISPONIBLE = False

# --- CONFIGURACIÓN Y CONSTANTES ---
VERSION = "v2.6-Enterprise"

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

DIR_USB_BACKUPS = BASE_DIR / "copy4me_backups"
CONFIG_FILE = BASE_DIR / "config.json"
MAX_BACKUPS = 10
EXCLUDE_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env',
    '.idea', '.vscode', 'System Volume Information', '$RECYCLE.BIN', '.Trash-1000'
}

# Metadatos del sistema local
NOMBRE_EQUIPO = socket.gethostname()
SISTEMA_OPERATIVO = f"{platform.system()} {platform.release()}"

# --- PALETA DE COLORES ANSI Y BANNER CORREGIDO ---
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

BANNER_ASCII = f"""{Color.CYAN}      █████╗ ██╗      ██╗      ██╗  ██╗███╗   ███╗███████╗
     ██╔══██╗██║      ██║      ██║  ██║████╗ ████║██╔════╝
{Color.VERDE}     ███████║██║      ██║      ███████║██╔████╔██║█████╗  
     ██╔══██║██║      ██║      ╚════██║██║╚██╔╝██║██╔══╝  
{Color.MAGENTA}     ██║  ██║███████╗███████╗      ██║██║ ╚═╝ ██║███████╗
     ╚═╝  ╚═╝╚══════╝╚══════╝      ╚═╝╚═╝     ╚═╝╚══════╝{Color.RESET}

{Color.CYAN}               _________________________________________
    [ PC-1 ]       C  O  P  Y  ◄─── 4 ───►  M  E         [ PC-2 ]
      📂       ==  ==  ==  ==  ==  ==  ==  ==  ==  ==       📂
    Directo          S i n c r o n i z a d o r           Respaldado
               __________________________________________{Color.RESET}
                   Versión: {Color.BOLD}{VERSION}{Color.RESET} | Max Backups: {Color.BOLD}{MAX_BACKUPS}{Color.RESET}
                   Equipo Local: {Color.AZUL}{NOMBRE_EQUIPO}{Color.RESET} ({SISTEMA_OPERATIVO})
{Color.AMARILLO}   ------------------------------------------------------------{Color.RESET}"""

# --- LOGGING SEGURO ---
try:
    DIR_USB_BACKUPS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DIR_USB_BACKUPS / "sync_history.log",
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        encoding='utf-8'
    )
except Exception as e:
    print(f"⚠️ No se pudo inicializar el log: {e}")

# --- AYUDANTE DE RUTAS EN WINDOWS ---
def adaptar_ruta_larga(ruta: Path) -> Path:
    """Añade prefijo UNC para evitar límites de 260 caracteres en Windows."""
    str_ruta = str(ruta.resolve())
    if platform.system() == "Windows" and not str_ruta.startswith("\\\\?\\"):
        return Path("\\\\?\\" + str_ruta)
    return ruta

# --- GESTIÓN DE CONFIGURACIÓN ---
def cargar_configuracion():
    if not CONFIG_FILE.exists():
        return {"perfiles": {}}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "perfiles" not in data:
                data["perfiles"] = {}
            return data
    except Exception as e:
        logging.error(f"Error leyendo {CONFIG_FILE}: {e}")
        return {"perfiles": {}}

def guardar_configuracion(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Error guardando {CONFIG_FILE}: {e}")
        return False

def registrar_o_actualizar_perfil(nombre_perfil, ruta_local):
    config = cargar_configuracion()
    config["perfiles"][nombre_perfil] = {
        "ruta_local": str(Path(ruta_local).resolve()),
        "ultimo_equipo": NOMBRE_EQUIPO,
        "sistema_operativo": SISTEMA_OPERATIVO,
        "ultima_sincronizacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    guardar_configuracion(config)
    return config

# --- MOTOR DE COPIA Y RESPALDOS ---
def obtener_tamano_formateado(ruta):
    try:
        ruta = Path(ruta)
        if ruta.is_file():
            total_size = ruta.stat().st_size
        else:
            total_size = sum(
                f.stat().st_size for f in ruta.glob('**/*') if f.is_file()
            )

        if total_size == 0:
            return "0 B"

        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if total_size < 1024.0:
                return f"{total_size:.2f} {unit}"
            total_size /= 1024.0
        return f"{total_size:.2f} PB"
    except Exception:
        return "Tamaño desconocido"

def gestionar_rotacion_backups(nombre_carpeta, log_func=print):
    carpeta_historico = DIR_USB_BACKUPS / nombre_carpeta
    if not carpeta_historico.exists():
        return

    backups_existentes = sorted(
        [f for f in carpeta_historico.glob("backup_*.zip") if f.is_file()],
        key=lambda x: x.stat().st_mtime
    )

    while len(backups_existentes) >= MAX_BACKUPS:
        antiguo = backups_existentes.pop(0)
        try:
            antiguo.unlink()
            msg = f"♻️ Rotación: Eliminado backup antiguo ({antiguo.name})"
            log_func(msg)
            logging.info(msg)
        except Exception as e:
            logging.error(f"Fallo al eliminar backup antiguo {antiguo.name}: {e}")

def crear_backup_zip(origen, destino_zip, log_func=print):
    try:
        origen = Path(origen)
        destino_zip = Path(destino_zip)
        destino_zip.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(destino_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            manifest_info = {
                "fecha_creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "creado_en_equipo": NOMBRE_EQUIPO,
                "sistema_operativo": SISTEMA_OPERATIVO,
                "origen_datos": str(origen)
            }
            zipf.writestr("manifest_backup.json", json.dumps(manifest_info, indent=4))

            for root, dirs, files in os.walk(origen, followlinks=False):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for file in files:
                    ruta_completa = Path(root) / file
                    if ruta_completa.resolve() == destino_zip.resolve():
                        continue
                    try:
                        arcname = ruta_completa.relative_to(origen)
                        zipf.write(ruta_completa, arcname)
                    except (PermissionError, FileNotFoundError) as fe:
                        logging.warning(f"Omitido del ZIP por bloqueo: {ruta_completa} ({fe})")

        log_func(f"📦 Punto de restauración creado: {destino_zip.name}")
        return True
    except Exception as e:
        logging.error(f"Error crítico creando ZIP {destino_zip}: {e}")
        return False

def ejecutar_copia_sincronizada(origen, destino, modo_espejo=False, callback_progreso=None, log_func=print):
    origen = Path(origen).resolve()
    destino = Path(destino).resolve()
    archivos_copiados = 0
    archivos_eliminados = 0
    errores = 0

    log_func(f"\n🚀 EJECUTANDO SINCRONIZACIÓN:")
    log_func(f" 📤 ORIGEN  : {origen}")
    log_func(f" 📥 DESTINO : {destino}")

    if not origen.exists():
        log_func("❌ Error: La ruta origen no existe.")
        return 0, 0

    todos_los_elementos = []
    for root, dirs, files in os.walk(origen, followlinks=False):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for file in files:
            todos_los_elementos.append(Path(root) / file)

    total_archivos = len(todos_los_elementos)

    # Modo Espejo: Limpieza en destino
    if modo_espejo and destino.exists():
        log_func("🧹 Aplicando eliminación espejo de elementos huérfanos...")
        for root, dirs, files in os.walk(destino, followlinks=False):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in files:
                ruta_dest = Path(root) / file
                relativa = ruta_dest.relative_to(destino)
                ruta_orig = origen / relativa
                if not ruta_orig.exists():
                    try:
                        ruta_dest.unlink()
                        archivos_eliminados += 1
                        log_func(f" 🗑️ Eliminado de destino: {relativa}")
                    except Exception as e:
                        logging.warning(f"No se pudo eliminar {ruta_dest}: {e}")

    # Copia / Actualización
    for idx, item in enumerate(todos_los_elementos, 1):
        relativa = item.relative_to(origen)
        target = destino / relativa

        try:
            necesita_copia = False
            if not target.exists():
                necesita_copia = True
            else:
                stat_item = item.stat()
                stat_target = target.stat()
                if stat_item.st_size != stat_target.st_size or abs(stat_item.st_mtime - stat_target.st_mtime) > 2.0:
                    necesita_copia = True

            if necesita_copia:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(item, target)
                except (PermissionError, OSError):
                    shutil.copy(item, target)
                archivos_copiados += 1
        except Exception as e:
            errores += 1
            logging.error(f"Error copiando {item}: {e}")

        if callback_progreso:
            callback_progreso(idx, total_archivos, archivos_copiados, archivos_eliminados)

    msg_final = f"✅ Proceso finalizado. Copiados: {archivos_copiados} | Borrados Espejo: {archivos_eliminados} | Errores: {errores}"
    log_func(msg_final)
    logging.info(f"Sync ({origen} -> {destino}): {msg_final}")
    return archivos_copiados, archivos_eliminados

# --- INTERFAZ GRÁFICA (Tkinter THREAD-SAFE) ---
if GUI_DISPONIBLE:
    class Copy4MeGUI(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"COPY FOR ME - Sincronizador Portátil ({VERSION})")
            self.geometry("850x660")
            self.minsize(750, 520)
            self.config_data = cargar_configuracion()
            self.ui_queue = queue.Queue()
            
            self._crear_interfaz()
            self.after(100, self._procesar_cola_ui)

        def _procesar_cola_ui(self):
            """Procesa mensajes provenientes de hilos secundarios de manera thread-safe."""
            try:
                while True:
                    task, args = self.ui_queue.get_nowait()
                    if task == "log":
                        self._append_log(args[0])
                    elif task == "progress":
                        self._actualizar_progreso(args[0], args[1])
                    elif task == "msgbox_info":
                        messagebox.showinfo(args[0], args[1])
                    elif task == "refresh":
                        self._refresh_all()
                    self.ui_queue.task_done()
            except queue.Empty:
                pass
            finally:
                self.after(100, self._procesar_cola_ui)

        def log_gui(self, texto):
            self.ui_queue.put(("log", (texto,)))

        def _append_log(self, texto):
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, f"{texto}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')

        def _actualizar_progreso(self, idx, total):
            self.progress_bar['maximum'] = total if total > 0 else 1
            self.progress_bar['value'] = idx

        def _crear_interfaz(self):
            header_frame = ttk.Frame(self, padding=10)
            header_frame.pack(fill=tk.X)
            lbl_banner = ttk.Label(header_frame, text="COPY ◄── 4 ──► ME", font=("Courier", 16, "bold"), foreground="#007acc")
            lbl_banner.pack(side=tk.LEFT)
            lbl_ver = ttk.Label(header_frame, text=f"{VERSION} | PC: {NOMBRE_EQUIPO}", font=("Helvetica", 9, "bold"))
            lbl_ver.pack(side=tk.RIGHT)

            self.notebook = ttk.Notebook(self)
            self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

            # Pestañas
            self.tab_subir = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(self.tab_subir, text=" 📤 PC ➔ USB (Respaldo) ")
            self._build_tab_subir()

            self.tab_descargar = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(self.tab_descargar, text=" 📥 USB ➔ PC (Restaurar) ")
            self._build_tab_descargar()

            self.tab_gestionar = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(self.tab_gestionar, text=" 🔍 Administrar Historias ")
            self._build_tab_gestionar()

            self.tab_perfiles = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(self.tab_perfiles, text=" ⚙️ Perfiles y Configuración ")
            self._build_tab_perfiles()

            # Consola de operaciones
            console_frame = ttk.LabelFrame(self, text=" Registro de Operación en Vivo ", padding=10)
            console_frame.pack(fill=tk.X, padx=10, pady=10)

            self.progress_bar = ttk.Progressbar(console_frame, orient="horizontal", mode="determinate")
            self.progress_bar.pack(fill=tk.X, pady=(0, 5))

            self.log_text = tk.Text(console_frame, height=7, state='disabled', wrap='word', bg='#1e1e1e', fg='#00ffcc', font=("Consolas", 9))
            self.log_text.pack(fill=tk.BOTH, expand=True)

        def _build_tab_subir(self):
            ttk.Label(self.tab_subir, text="1. Selecciona un Perfil o explora la carpeta origen en tu PC:", font=('Helvetica', 9, 'bold')).pack(anchor=tk.W, pady=5)
            f_opt = ttk.Frame(self.tab_subir)
            f_opt.pack(fill=tk.X, pady=5)

            perfiles = list(self.config_data.get("perfiles", {}).keys())
            self.combo_perfiles = ttk.Combobox(f_opt, values=perfiles)
            self.combo_perfiles.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
            self.combo_perfiles.bind("<<ComboboxSelected>>", self._on_perfil_selected)

            btn_browse = ttk.Button(f_opt, text="Examinar PC...", command=self._browse_pc_folder)
            btn_browse.pack(side=tk.RIGHT)

            self.lbl_path = ttk.Label(self.tab_subir, text="Ruta local seleccionada: (Ninguna)", foreground="gray")
            self.lbl_path.pack(anchor=tk.W, pady=5)

            ttk.Separator(self.tab_subir, orient='horizontal').pack(fill='x', pady=10)

            ttk.Label(self.tab_subir, text="2. Opciones de Sincronización hacia la USB:", font=('Helvetica', 9, 'bold')).pack(anchor=tk.W, pady=5)
            self.var_espejo = tk.BooleanVar(value=False)
            chk_espejo = ttk.Checkbutton(
                self.tab_subir,
                text="🧹 Modo Clonación Espejo (Elimina en el USB lo que hayas borrado en la PC)",
                variable=self.var_espejo
            )
            chk_espejo.pack(anchor=tk.W, pady=5)

            btn_start = ttk.Button(self.tab_subir, text="🚀 Guardar y Respaldar al USB", command=self._start_backup_thread)
            btn_start.pack(anchor=tk.E, pady=15)

        def _browse_pc_folder(self):
            folder = filedialog.askdirectory(title="Selecciona la carpeta local a respaldar")
            if folder:
                p = Path(folder)
                self.combo_perfiles.set(p.name)
                self.lbl_path.config(text=f"Ruta local seleccionada: {p}")
                if messagebox.askyesno("Guardar Perfil", f"¿Registrar el perfil '{p.name}' en config.json?"):
                    self.config_data = registrar_o_actualizar_perfil(p.name, p)
                    self.combo_perfiles['values'] = list(self.config_data.get("perfiles", {}).keys())

        def _on_perfil_selected(self, event):
            name = self.combo_perfiles.get()
            pdata = self.config_data.get("perfiles", {}).get(name, {})
            ruta = pdata.get("ruta_local") if isinstance(pdata, dict) else pdata
            if ruta:
                self.lbl_path.config(text=f"Ruta local seleccionada: {ruta}")

        def _start_backup_thread(self):
            nombre = self.combo_perfiles.get().strip()
            pdata = self.config_data.get("perfiles", {}).get(nombre, {})
            path_str = pdata.get("ruta_local") if isinstance(pdata, dict) else pdata

            if not path_str:
                path_str = self.lbl_path.cget("text").replace("Ruta local seleccionada: ", "")

            if not nombre or not Path(path_str).exists():
                messagebox.showerror("Error de Ruta", "Debes seleccionar una carpeta de origen válida en tu PC.")
                return

            origen = Path(path_str)
            destino = DIR_USB_BACKUPS / nombre / "MASTER"

            msg = f"¿Iniciar respaldo de '{nombre}'?\n\n📤 Desde (PC): {origen}\n📥 Hacia (USB): {destino}"
            if self.var_espejo.get():
                msg += "\n\n⚠️ ATENCIÓN: El Modo Espejo eliminará en el USB los archivos borrados en la PC."

            if messagebox.askyesno("Confirmar Respaldo", msg):
                registrar_o_actualizar_perfil(nombre, origen)
                threading.Thread(target=self._worker_backup, args=(nombre, origen), daemon=True).start()

        def _worker_backup(self, nombre, origen):
            destino = DIR_USB_BACKUPS / nombre / "MASTER"

            if destino.exists() and any(destino.iterdir()):
                gestionar_rotacion_backups(nombre, log_func=self.log_gui)
                fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
                crear_backup_zip(
                    destino,
                    DIR_USB_BACKUPS / nombre / f"backup_{nombre}_{NOMBRE_EQUIPO}_{fecha}.zip",
                    log_func=self.log_gui
                )

            destino.mkdir(parents=True, exist_ok=True)

            def cb(idx, total, cop, borr):
                self.ui_queue.put(("progress", (idx, total)))

            ejecutar_copia_sincronizada(origen, destino, self.var_espejo.get(), callback_progreso=cb, log_func=self.log_gui)
            self.ui_queue.put(("msgbox_info", ("Éxito", f"Respaldo de '{nombre}' completado correctamente.")))
            self.ui_queue.put(("refresh", None))

        def _build_tab_descargar(self):
            ttk.Label(self.tab_descargar, text="1. Selecciona el proyecto guardado en el USB:", font=('Helvetica', 9, 'bold')).pack(anchor=tk.W, pady=5)
            self.combo_usb = ttk.Combobox(self.tab_descargar)
            self.combo_usb.pack(fill=tk.X, pady=5)

            ttk.Separator(self.tab_descargar, orient='horizontal').pack(fill='x', pady=10)

            ttk.Label(self.tab_descargar, text="2. Opciones de Sincronización hacia la PC local:", font=('Helvetica', 9, 'bold')).pack(anchor=tk.W, pady=5)
            self.var_espejo_down = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                self.tab_descargar,
                text="🧹 Modo Clonación Espejo en PC (Elimina en la PC elementos que ya no existen en el USB)",
                variable=self.var_espejo_down
            ).pack(anchor=tk.W, pady=5)

            btn_down = ttk.Button(self.tab_descargar, text="📥 Descargar / Restaurar en PC", command=self._start_download_thread)
            btn_down.pack(anchor=tk.E, pady=15)

        def _start_download_thread(self):
            proj = self.combo_usb.get()
            if not proj:
                messagebox.showerror("Error", "Selecciona un proyecto del USB.")
                return

            pdata = self.config_data.get("perfiles", {}).get(proj, {})
            saved_path = pdata.get("ruta_local") if isinstance(pdata, dict) else pdata

            if saved_path and Path(saved_path).exists():
                if messagebox.askyesno("Ruta Detectada", f"¿Restaurar en la ruta asignada a este equipo?\n{saved_path}"):
                    dest = Path(saved_path)
                else:
                    dest = Path(filedialog.askdirectory(title="Selecciona destino en PC"))
            else:
                dest = Path(filedialog.askdirectory(title="Selecciona destino en PC"))

            if not dest or str(dest) == ".":
                return

            origen = DIR_USB_BACKUPS / proj / "MASTER"
            msg = f"¿Iniciar restauración de '{proj}'?\n\n📤 Desde (USB): {origen}\n📥 Hacia (PC): {dest}"
            if messagebox.askyesno("Confirmar Restauración", msg):
                threading.Thread(target=self._worker_download, args=(proj, dest), daemon=True).start()

        def _worker_download(self, proj, dest):
            origen = DIR_USB_BACKUPS / proj / "MASTER"
            if not origen.exists():
                self.log_gui("❌ Error: No existe la carpeta MASTER en el USB.")
                return

            dest.mkdir(parents=True, exist_ok=True)

            def cb(idx, total, cop, borr):
                self.ui_queue.put(("progress", (idx, total)))

            ejecutar_copia_sincronizada(origen, dest, self.var_espejo_down.get(), callback_progreso=cb, log_func=self.log_gui)
            registrar_o_actualizar_perfil(proj, dest)
            self.ui_queue.put(("msgbox_info", ("Éxito", f"Proyecto '{proj}' restaurado en PC.")))
            self.ui_queue.put(("refresh", None))

        def _build_tab_gestionar(self):
            ttk.Label(self.tab_gestionar, text="Explorador de Puntos de Restauración (.ZIP) en el USB:").pack(anchor=tk.W, pady=5)
            self.tree = ttk.Treeview(self.tab_gestionar, columns=("Fecha", "Tamaño"), show="tree headings")
            self.tree.heading("#0", text="Proyecto / Archivos de Respaldo")
            self.tree.heading("Fecha", text="Última Modificación")
            self.tree.heading("Tamaño", text="Tamaño")
            self.tree.pack(fill=tk.BOTH, expand=True, pady=5)

            f_btns = ttk.Frame(self.tab_gestionar)
            f_btns.pack(fill=tk.X, pady=5)

            ttk.Button(f_btns, text="🗑️ Eliminar Backup Seleccionado", command=self._delete_selected_backup).pack(side=tk.LEFT)
            ttk.Button(f_btns, text="🔄 Recargar Árbol", command=self._refresh_all).pack(side=tk.RIGHT)

        def _delete_selected_backup(self):
            selected = self.tree.selection()
            if not selected:
                return
            item_text = self.tree.item(selected[0])['text']
            parent_text = self.tree.item(self.tree.parent(selected[0]))['text']

            if not parent_text:
                messagebox.showwarning("Atención", "Selecciona un archivo .ZIP específico dentro de un proyecto.")
                return

            target = DIR_USB_BACKUPS / parent_text / item_text
            if messagebox.askyesno("Confirmar Eliminación", f"¿Eliminar permanentemente el archivo {item_text}?"):
                try:
                    target.unlink()
                    self.log_gui(f"🗑️ Copia eliminada: {item_text}")
                    self._refresh_all()
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo eliminar: {e}")

        def _build_tab_perfiles(self):
            ttk.Label(self.tab_perfiles, text="Contenido Actualizado del Archivo de Perfiles (config.json):").pack(anchor=tk.W, pady=5)
            self.txt_config = tk.Text(self.tab_perfiles, height=9, bg='#252526', fg='#ffffff', font=("Consolas", 9))
            self.txt_config.pack(fill=tk.BOTH, expand=True, pady=5)

        def _refresh_all(self):
            if DIR_USB_BACKUPS.exists():
                projs = [d.name for d in DIR_USB_BACKUPS.iterdir() if d.is_dir()]
                self.combo_usb['values'] = projs

            for item in self.tree.get_children():
                self.tree.delete(item)

            if DIR_USB_BACKUPS.exists():
                for p in DIR_USB_BACKUPS.iterdir():
                    if p.is_dir():
                        node = self.tree.insert("", tk.END, text=p.name, open=True)
                        for b in sorted(p.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                            if b.name.startswith("backup_") or b.name == "MASTER":
                                f_str = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                                size_str = obtener_tamano_formateado(b)
                                self.tree.insert(node, tk.END, text=b.name, values=(f_str, size_str))

            self.config_data = cargar_configuracion()
            self.combo_perfiles['values'] = list(self.config_data.get("perfiles", {}).keys())
            self.txt_config.delete("1.0", tk.END)
            self.txt_config.insert(tk.END, json.dumps(self.config_data, indent=4, ensure_ascii=False))

# --- MOTOR CONSOLA TUI COMPLETO (SOPORTE 100% MODO SIN PYTHON/GUI) ---
def modo_tui_fallback():
    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        print(BANNER_ASCII)
        print(f"\n{Color.AMARILLO}⚠️ AVISO: Entorno de Consola/TUI activado (Sin entorno gráfico Tkinter).{Color.RESET}")
        print("\nMenú Principal:")
        print(" 1. 📤 Respaldo PC ➔ USB")
        print(" 2. 📥 Restaurar USB ➔ PC")
        print(" 3. 🔍 Gestionar Historias (.ZIP)")
        print(" 4. ⚙️ Ver Perfiles y Configuración")
        print(" 5. ❌ Salir")

        opt = input("\nSelecciona una opción [1-5]: ").strip()

        if opt == "1":
            config = cargar_configuracion()
            perfiles = config.get("perfiles", {})
            keys = list(perfiles.keys())
            print("\n--- SELECCIONAR PERFIL O RUTA ---")
            for idx, k in enumerate(keys, 1):
                pdata = perfiles[k]
                r_loc = pdata.get("ruta_local") if isinstance(pdata, dict) else pdata
                print(f"  [{idx}] {k} -> {r_loc}")
            print("  [0] Registrar nueva ruta...")

            sel = input("\nOpción: ").strip()
            if sel == "0" or not sel.isdigit() or int(sel) > len(keys):
                path_str = input("Ruta local absoluta en la PC: ").strip()
                p = Path(path_str)
                if not p.exists():
                    input("❌ Ruta no válida. Presiona ENTER para continuar..."); continue
                nombre = p.name
            else:
                nombre = keys[int(sel)-1]
                pdata = perfiles[nombre]
                path_str = pdata.get("ruta_local") if isinstance(pdata, dict) else pdata

            origen = Path(path_str)
            destino = DIR_USB_BACKUPS / nombre / "MASTER"
            modo_esp = input("¿Activar Modo Clonación Espejo? (s/N): ").strip().lower() == 's'

            if input("\n¿Ejecutar respaldo? (s/N): ").strip().lower() == 's':
                if destino.exists() and any(destino.iterdir()):
                    gestionar_rotacion_backups(nombre)
                    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
                    crear_backup_zip(destino, DIR_USB_BACKUPS / nombre / f"backup_{nombre}_{NOMBRE_EQUIPO}_{fecha}.zip")

                destino.mkdir(parents=True, exist_ok=True)
                ejecutar_copia_sincronizada(origen, destino, modo_esp)
                registrar_o_actualizar_perfil(nombre, origen)
            input("\nPresiona ENTER para continuar...")

        elif opt == "2":
            print("\n--- RESTAURAR USB ➔ PC ---")
            if not DIR_USB_BACKUPS.exists():
                input("❌ No existen respaldos en la USB. Presiona ENTER..."); continue

            proyectos = [d.name for d in DIR_USB_BACKUPS.iterdir() if d.is_dir()]
            if not proyectos:
                input("❌ No se encontraron proyectos respaldados. ENTER para continuar..."); continue

            for idx, proj in enumerate(proyectos, 1):
                print(f"  [{idx}] {proj}")

            sel = input("\nSelecciona proyecto a restaurar: ").strip()
            if not sel.isdigit() or int(sel) < 1 or int(sel) > len(proyectos):
                continue

            proj_nombre = proyectos[int(sel)-1]
            origen = DIR_USB_BACKUPS / proj_nombre / "MASTER"

            dest_str = input("Ruta destino absoluta en la PC (o ENTER para usar config.json): ").strip()
            if not dest_str:
                config = cargar_configuracion()
                pdata = config.get("perfiles", {}).get(proj_nombre, {})
                dest_str = pdata.get("ruta_local") if isinstance(pdata, dict) else pdata

            if not dest_str:
                input("❌ Ruta invalida. ENTER para continuar..."); continue

            destino = Path(dest_str)
            modo_esp = input("¿Activar Modo Espejo en PC? (s/N): ").strip().lower() == 's'

            if input(f"\n¿Restaurar {proj_nombre} en {destino}? (s/N): ").strip().lower() == 's':
                destino.mkdir(parents=True, exist_ok=True)
                ejecutar_copia_sincronizada(origen, destino, modo_esp)
                registrar_o_actualizar_perfil(proj_nombre, destino)
            input("\nPresiona ENTER para continuar...")

        elif opt == "3":
            print("\n--- ADMINISTRAR HISTORIAS Y PUNTOS DE RESTAURACIÓN ---")
            if DIR_USB_BACKUPS.exists():
                for p in DIR_USB_BACKUPS.iterdir():
                    if p.is_dir():
                        print(f"\n📂 Proyecto: {p.name}")
                        zips = sorted([b for b in p.iterdir() if b.name.startswith("backup_") or b.name == "MASTER"])
                        for idx, b in enumerate(zips, 1):
                            f_str = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                            print(f"   [{idx}] {b.name} | {f_str} | {obtener_tamano_formateado(b)}")
            input("\nPresiona ENTER para continuar...")

        elif opt == "4":
            print("\n--- CONFIGURACIÓN Y PERFILES REGISTRADOS ---")
            config = cargar_configuracion()
            print(json.dumps(config, indent=4, ensure_ascii=False))
            input("\nPresiona ENTER para continuar...")

        elif opt == "5":
            print("\n👋 ¡Saliendo de Copy4Me!")
            sys.exit(0)

# --- PUNTO DE ENTRADA GENERAL ---
if __name__ == "__main__":
    if GUI_DISPONIBLE:
        try:
            app = Copy4MeGUI()
            app._refresh_all()
            app.mainloop()
        except Exception as e:
            logging.error(f"Fallo en GUI, conmutando a TUI: {e}")
            modo_tui_fallback()
    else:
        modo_tui_fallback()
