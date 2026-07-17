import os
import sys
import shutil
import subprocess
import logging
import zipfile
from datetime import datetime
from pathlib import Path

# --- SISTEMA DE ENTRADA NATIVA ---
try:
    import msvcrt
    def obtener_tecla():
        """Lee una tecla en Windows."""
        ch = msvcrt.getch()
        if ch in (b'\x00', b'\xe0'):  # Tecla especial (flechas)
            ch2 = msvcrt.getch()
            if ch2 == b'H': return "UP"
            if ch2 == b'P': return "DOWN"
        if ch == b'\r':
            return "ENTER"
        if ch == b'\x03':  # Ctrl+C
            raise KeyboardInterrupt
        return None
except ImportError:
    import tty
    import termios
    def obtener_tecla():
        """Lee una tecla en Linux / macOS."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
            if ch == '\x1b':  # Secuencia de escape (flechas)
                ch2 = sys.stdin.read(2)
                if ch2 == '[A': return "UP"
                if ch2 == '[B': return "DOWN"
            if ch == '\n' or ch == '\r':
                return "ENTER"
            if ch == '\x03':  # Ctrl+C
                raise KeyboardInterrupt
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return None

# --- CONFIGURACIÓN ---
VERSION = "v1.4"  # Versión corregida con renderizado de pantalla completa
DIR_USB_BACKUPS = Path(__file__).resolve().parent / "copy4me_backups"
MAX_BACKUPS = 10
EXCLUDE_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env', '.idea', '.vscode'}

# Paleta de colores ANSI
class Color:
    CYAN = "\033[96m"
    VERDE = "\033[92m"
    MAGENTA = "\033[95m"
    BLANCO = "\033[97m"
    GRIS = "\033[90m"
    AMARILLO = "\033[93m"
    ROJO = "\033[91m"
    AZUL = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

# Configurar Logging
try:
    DIR_USB_BACKUPS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DIR_USB_BACKUPS / "sync_history.log",
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        encoding='utf-8'
    )
except Exception as e:
    print(f"{Color.AMARILLO}⚠️ No se pudo inicializar el archivo de log: {e}{Color.RESET}")

def limpiar_pantalla():
    try:
        # En sistemas Windows modernos, Linux y macOS, reposiciona el cursor en el inicio (0,0)
        # y limpia la pantalla desde ahí hacia abajo de manera progresiva y fluida, evitando el parpadeo.
        sys.stdout.write("\033[H\033[J")
        sys.stdout.flush()
    except Exception:
        try:
            os.system('cls' if os.name == 'nt' else 'clear')
        except Exception:
            pass

def mostrar_logo():
    # --- LOGO ALL4ME ---
    print(Color.CYAN +    "      █████╗ ██╗      ██╗      ██╗  ██╗███╗   ███╗███████╗")
    print(Color.CYAN +    "     ██╔══██╗██║      ██║      ██║  ██║████╗ ████║██╔════╝")
    print(Color.VERDE +   "     ███████║██║      ██║      ███████║██╔████╔██║█████╗  ")
    print(Color.VERDE +   "     ██╔══██║██║      ██║      ╚════██║██║╚██╔╝██║██╔══╝  ")
    print(Color.MAGENTA + "     ██║  ██║███████╗███████╗      ██║██║ ╚═╝ ██║███████╗")
    print(Color.MAGENTA + "     ╚═╝  ╚═╝╚══════╝╚══════╝      ╚═╝╚═╝     ╚═╝╚══════╝" + Color.RESET)
    print("")
  
    # --- USB COPY4ME ---
    print(Color.CYAN +        "               _________________________________________")
    print(Color.CYAN +        "    [ PC-1 ]       C  O  P  Y  ◄─── 4 ───►  M  E         [ PC-2 ]")
    print(Color.CYAN +        "      📂       ==  ==  ==  ==  ==  ==  ==  ==  ==  ==       📂")
    print(Color.CYAN +        "    Directo          S i n c r o n i z a d o r           Respaldado")
    print(Color.CYAN +        "               __________________________________________" + Color.RESET)
    print(f"\n                   Versión: {Color.BOLD}{VERSION}{Color.RESET} | Max Backups: {Color.BOLD}{MAX_BACKUPS}{Color.RESET}")
    print(f"{Color.AMARILLO}   ------------------------------------------------------------{Color.RESET}\n")

# --- SELECTOR INTERACTIVO ANTIPARPADEO Y ANTIDUPLICADO ---
def seleccionar_opcion(titulo, opciones, bloque_cabecera=None):
    """
    Renderiza el menú completo limpiando pantalla para evitar duplicaciones.
    - 'bloque_cabecera' es una función opcional que pinta el estado del menú actual (ej. Ruta activa).
    """
    seleccionado = 0
    total = len(opciones)
    
    while True:
        limpiar_pantalla()
        mostrar_logo()
        
        # Si hay un bloque de información intermedia, lo renderizamos aquí
        if bloque_cabecera:
            bloque_cabecera()
            
        # Imprimir el título del selector
        print(f"{Color.BOLD}{titulo}{Color.RESET}\n")
        
        # Imprimir las opciones
        for i, opcion in enumerate(opciones):
            if i == seleccionado:
                print(f" {Color.VERDE}❯ {Color.BOLD}{opcion}{Color.RESET}")
            else:
                print(f"   {Color.GRIS}{opcion}{Color.RESET}")
        
        # Esperar la tecla
        tecla = obtener_tecla()
        
        if tecla == "UP":
            seleccionado = (seleccionado - 1) % total
        elif tecla == "DOWN":
            seleccionado = (seleccionado + 1) % total
        elif tecla == "ENTER":
            return opciones[seleccionado]

def obtener_tamano_formateado(ruta):
    try:
        if ruta.is_file():
            total_size = ruta.stat().st_size
        else:
            total_size = 0
            for dirpath, _, filenames in os.walk(ruta):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        try:
                            total_size += os.path.getsize(fp)
                        except (OSError, PermissionError):
                            continue
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if total_size < 1024.0:
                return f"{total_size:.2f} {unit}"
            total_size /= 1024.0
    except Exception:
        return "Tamaño desconocido"

def crear_backup_zip(origen, destino_zip):
    try:
        with zipfile.ZipFile(destino_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(origen):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for file in files:
                    ruta_completa = Path(root) / file
                    try:
                        zipf.write(ruta_completa, ruta_completa.relative_to(origen))
                    except (PermissionError, FileNotFoundError) as fe:
                        logging.warning(f"Omitido del ZIP por falta de acceso/bloqueo: {ruta_completa} ({fe})")
        return True
    except Exception as e:
        logging.error(f"Error crítico creando ZIP {destino_zip}: {e}")
        return False

def navegador_archivos(titulo_prompt="Selecciona una carpeta:"):
    try:
        ruta_actual = Path('C:\\') if os.name == 'nt' else Path('/')
        if not ruta_actual.exists():
            ruta_actual = Path.cwd().root
    except Exception:
        ruta_actual = Path.cwd()

    while True:
        try:
            subcarpetas = [d for d in ruta_actual.iterdir() if d.is_dir() and not d.name.startswith('.')]
            subcarpetas.sort(key=lambda x: x.name.lower())
        except PermissionError:
            limpiar_pantalla()
            mostrar_logo()
            print(f"{Color.ROJO}⚠️ Sin permisos para acceder a esta carpeta.{Color.RESET}")
            input("\nPresiona ENTER para volver atrás...")
            ruta_actual = ruta_actual.parent if ruta_actual.parent != ruta_actual else Path.cwd()
            continue
        except Exception as e:
            limpiar_pantalla()
            mostrar_logo()
            print(f"{Color.ROJO}⚠️ Error al leer directorio: {e}{Color.RESET}")
            input("\nPresiona ENTER para ir al directorio de trabajo actual...")
            ruta_actual = Path.cwd()
            continue

        opciones = [f"💾 [ SELECCIONAR ESTA CARPETA: {ruta_actual.name or ruta_actual} ]", "↩️ .. (Ir atrás)"]
        if os.name == 'nt':
            opciones.append("💽 [ Cambiar de Unidad de Disco ]")

        opciones.extend([f"📁 {d.name}" for d in subcarpetas])
        
        # Definimos la cabecera dinámica para el explorador
        def cabecera_explorador():
            print(f"┌────────────────────────────────────────────────────────────")
            print(f"│ {Color.AZUL}📍 RUTA ACTUAL:{Color.RESET} {Color.BOLD}{ruta_actual}{Color.RESET}")
            print(f"└────────────────────────────────────────────────────────────\n")

        eleccion = seleccionar_opcion(titulo_prompt, opciones, bloque_cabecera=cabecera_explorador)
        
        if eleccion.startswith("💾 [ SELECCIONAR ESTA CARPETA"):
            return ruta_actual
        elif eleccion == "↩️ .. (Ir atrás)":
            if ruta_actual.parent != ruta_actual:
                ruta_actual = ruta_actual.parent
        elif eleccion == "💽 [ Cambiar de Unidad de Disco ]":
            unidades = [f"{d}:\\" for d in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if os.path.exists(f"{d}:\\")]
            if unidades:
                unidad_elegida = seleccionar_opcion("Selecciona unidad de disco:", unidades)
                ruta_actual = Path(unidad_elegida)
            else:
                limpiar_pantalla()
                print(f"{Color.ROJO}No se detectaron otras unidades de disco.{Color.RESET}")
                input("\nPresiona ENTER para continuar...")
        else:
            nombre_carpeta = eleccion.replace("📁 ", "")
            ruta_actual = ruta_actual / nombre_carpeta

# --- GESTIÓN DE COPIAS ---
def gestionar_rotacion_backups(nombre_carpeta):
    carpeta_historico = DIR_USB_BACKUPS / nombre_carpeta
    if not carpeta_historico.exists():
        return

    backups_existentes = sorted(
        [d for d in carpeta_historico.iterdir() if d.name.startswith("backup_")],
        key=lambda x: x.stat().st_mtime
    )

    while len(backups_existentes) >= MAX_BACKUPS:
        antiguo = backups_existentes.pop(0)
        try:
            if antiguo.is_dir():
                shutil.rmtree(antiguo)
            else:
                antiguo.unlink()
            msg = f"Historial lleno: Se autodestruyó el backup antiguo ({antiguo.name})"
            print(f"{Color.ROJO}♻️ {msg}{Color.RESET}")
            logging.info(msg)
        except Exception as e:
            print(f"{Color.AMARILLO}⚠️ No se pudo borrar el backup antiguo {antiguo.name}: {e}{Color.RESET}")
            logging.error(f"Fallo al eliminar backup antiguo {antiguo.name}: {e}")

def ver_y_gestionar_copias():
    while True:
        # Cabecera para la selección de proyectos
        def cabecera_proyectos():
            print(f"{Color.AZUL}[🔍 GESTOR DE COPIAS] Explorando almacenamiento USB...{Color.RESET}\n")
        
        if not DIR_USB_BACKUPS.exists() or not any(DIR_USB_BACKUPS.iterdir()):
            limpiar_pantalla()
            mostrar_logo()
            print(f"{Color.ROJO}⚠️ No hay proyectos guardados en el USB todavía.{Color.RESET}")
            input("\nPresiona ENTER para volver al menú...")
            return

        proyectos = [d.name for d in DIR_USB_BACKUPS.iterdir() if d.is_dir()]
        opciones_proyectos = proyectos + ["🔙 <= Volver al menú principal"]
        
        proyecto_elegido = seleccionar_opcion(
            "Selecciona un proyecto para inspeccionar sus copias:", 
            opciones_proyectos, 
            bloque_cabecera=cabecera_proyectos
        )
        
        if proyecto_elegido == "🔙 <= Volver al menú principal":
            return
            
        carpeta_proyecto = DIR_USB_BACKUPS / proyecto_elegido
        copias = sorted([d for d in carpeta_proyecto.iterdir() if d.name.startswith("backup_")], key=lambda x: x.stat().st_mtime, reverse=True)
        
        while True:
            if not copias:
                limpiar_pantalla()
                mostrar_logo()
                print("No existen copias de seguridad archivadas para este proyecto.")
                input("\nPresiona ENTER para volver...")
                break

            opciones_copias = []
            for copia in copias:
                tamano = obtener_tamano_formateado(copia)
                try:
                    fecha = datetime.fromtimestamp(copia.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    fecha = "Fecha desconocida"
                icono = "📁" if copia.is_dir() else "📦"
                etiqueta = f"{icono} {copia.name} | 📅 {fecha} | 💾 {tamano}"
                opciones_copias.append(etiqueta)
                
            opciones_copias.append("🔙 <= Volver a la lista de proyectos")
            
            # Cabecera para el listado de copias del proyecto seleccionado
            def cabecera_copias():
                print(f"{Color.AZUL}📌 Proyecto activo:{Color.RESET} {Color.BOLD}{proyecto_elegido}{Color.RESET}\n")

            copia_elegida = seleccionar_opcion(
                "Selecciona una copia específica para gestionar:", 
                opciones_copias, 
                bloque_cabecera=cabecera_copias
            )
            
            if copia_elegida == "🔙 <= Volver a la lista de proyectos":
                break
                
            nombre_real_copia = copia_elegida.split(" | ")[0].replace("📁 ", "").replace("📦 ", "")
            ruta_copia_exacta = carpeta_proyecto / nombre_real_copia
            
            accion = seleccionar_opcion(f"¿Qué deseas hacer con '{nombre_real_copia}'?", [
                "🗑️ Eliminar esta copia permanentemente",
                "❌ <= Cancelar"
            ])
            
            if accion.startswith("🗑️ Eliminar"):
                confirmacion = seleccionar_opcion("❗ ¿Estás completamente seguro de eliminar esta copia?", ["⚠️ Sí, eliminar definitivamente", "❌ No, cancelar"])
                if "Sí, eliminar" in confirmacion:
                    try:
                        if ruta_copia_exacta.is_dir():
                            shutil.rmtree(ruta_copia_exacta)
                        else:
                            ruta_copia_exacta.unlink()
                        limpiar_pantalla()
                        mostrar_logo()
                        print(f"\n{Color.VERDE}✅ Copia eliminada del almacenamiento exitosamente.{Color.RESET}")
                        logging.info(f"Usuario eliminó manualmente la copia: {ruta_copia_exacta.name}")
                    except Exception as e:
                        limpiar_pantalla()
                        mostrar_logo()
                        print(f"\n{Color.ROJO}❌ No se pudo eliminar la copia: {e}{Color.RESET}")
                        logging.error(f"Error al eliminar copia manual {ruta_copia_exacta.name}: {e}")
                    input("\nPresiona ENTER para continuar...")
                    copias = sorted([d for d in carpeta_proyecto.iterdir() if d.name.startswith("backup_")], key=lambda x: x.stat().st_mtime, reverse=True)
                    if not copias:
                        break

# --- COPIA INTELIGENTE ---
# --- COPIA INTELIGENTE (VERBOSA) ---
def copiar_sincronizada(origen, destino, modo_espejo=False):
    origen = Path(origen)
    destino = Path(destino)
    archivos_copiados = 0
    archivos_eliminados = 0
    errores_encontrados = 0
    
    print(f"{Color.AMARILLO}🔍 Analizando la estructura de archivos y calculando tamaño...{Color.RESET}")
    
    try:
        uso_destino = shutil.disk_usage(destino.anchor if destino.anchor else destino.parent)
        if uso_destino.free < 50 * 1024 * 1024:
            print(f"{Color.ROJO}⚠️ ¡ALERTA CRÍTICA!: El espacio disponible en el disco de destino es extremadamente bajo.{Color.RESET}")
            input("Presiona ENTER si deseas continuar bajo tu propio riesgo...")
    except Exception:
        pass

    todos_los_elementos = []
    for root, dirs, files in os.walk(origen):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for file in files:
            todos_los_elementos.append(Path(root) / file)
            
    total_archivos = len(todos_los_elementos)
    
    if total_archivos == 0 and not modo_espejo:
        print(f"{Color.AMARILLO}ℹ️ No se encontraron archivos válidos para transferir.{Color.RESET}")
        return 0, 0

    if modo_espejo and destino.exists():
        for root, dirs, files in os.walk(destino):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in files:
                ruta_dest = Path(root) / file
                relativa = ruta_dest.relative_to(destino)
                ruta_orig = origen / relativa
                if not ruta_orig.exists():
                    try:
                        # MODIFICACIÓN VERBOSA: Print de eliminación
                        print(f"{Color.ROJO}🗑️ Eliminando obsoleto:{Color.RESET} {relativa}")
                        ruta_dest.unlink()
                        archivos_eliminados += 1
                    except Exception as e:
                        logging.warning(f"No se pudo eliminar el archivo obsoleto {ruta_dest}: {e}")

    porcentaje_anterior = -1
    try:
        for indice, item in enumerate(todos_los_elementos, 1):
            relativa = item.relative_to(origen)
            target = destino / relativa
            
            try:
                if not target.exists() or item.stat().st_mtime > target.stat().st_mtime:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # MODIFICACIÓN VERBOSA: Print de copia en tiempo real
                    print(f"{Color.CYAN}📄 Copiando:{Color.RESET} {relativa}")
                    shutil.copy2(item, target)
                    archivos_copiados += 1
            except (PermissionError, FileNotFoundError, OSError) as ferr:
                errores_encontrados += 1
                logging.error(f"Error al copiar {item} -> {target}: {ferr}")
                
            porcentaje = int((indice / total_archivos) * 100) if total_archivos > 0 else 100
            if porcentaje != porcentaje_anterior or indice == total_archivos:
                porcentaje_anterior = porcentaje
                bloques = int(porcentaje / 5)
                barra = "█" * bloques + "░" * (20 - bloques)
                sys.stdout.write(f"\r⚡ {Color.AZUL}Sincronizando:{Color.RESET} [{barra}] {porcentaje}% ({indice}/{total_archivos}) | {Color.VERDE}Copiados: {archivos_copiados}{Color.RESET} | {Color.ROJO}Borrados: {archivos_eliminados}{Color.RESET}")
                sys.stdout.flush()
            
        print("\n")
        if errores_encontrados > 0:
            print(f"\n{Color.AMARILLO}⚠️ Sincronización finalizada con {errores_encontrados} advertencias (archivos ocupados, omitidos o bloqueados).{Color.RESET}")
            print(f"📘 Consulta los detalles específicos en '{Color.BOLD}sync_history.log{Color.RESET}'.")
        
        logging.info(f"Sincronización completada. Origen: {origen.name} | Copiados: {archivos_copiados} | Borrados: {archivos_eliminados} | Errores: {errores_encontrados}")
        return archivos_copiados, archivos_eliminados

    except KeyboardInterrupt:
        print(f"\n\n{Color.ROJO}🛑 Proceso cancelado abruptamente por el usuario (Ctrl+C).{Color.RESET}")
        return archivos_copiados, archivos_eliminados
    except Exception as e:
        print(f"\n\n{Color.ROJO}❌ Error crítico inesperado durante la transferencia: {e}{Color.RESET}")
        logging.critical(f"Excepción grave en copiar_sincronizada: {e}", exc_info=True)
        return archivos_copiados, archivos_eliminados

# --- ACCIÓN 1: PC -> USB ---
def subir_al_usb():
    limpiar_pantalla()
    mostrar_logo()
    print(f"{Color.AZUL}[PC -> USB] Iniciando subida de datos hacia la unidad externa...{Color.RESET}\n")
    
    dir_origen = navegador_archivos("Selecciona la carpeta origen en tu PC:")
    nombre_carpeta = dir_origen.name
    
    # El script define de forma automática e invisible la ruta blindada en el USB
    dir_master_usb = DIR_USB_BACKUPS / nombre_carpeta / "MASTER"
    
    modo = seleccionar_opcion("Selecciona la estrategia de sincronización en USB:", [
        "🔄 Conservar todo (Suma archivos nuevos, mantiene antiguos intactos en USB)",
        "🧹 Modo Espejo (Clona de forma exacta; borra archivos del USB que ya no existan en PC)"
    ])
    modo_espejo = "Modo Espejo" in modo

    if dir_master_usb.exists() and any(dir_master_usb.iterdir()):
        gestionar_rotacion_backups(nombre_carpeta)
        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_historico = DIR_USB_BACKUPS / nombre_carpeta / f"backup_{nombre_carpeta}_desdePC1_{fecha}.zip"
        
        print(f"\n{Color.AMARILLO}📦 Comprimiendo estado anterior en un punto de restauración (.ZIP)...{Color.RESET}")
        if crear_backup_zip(dir_master_usb, zip_historico):
            logging.info(f"Backup histórico ZIP creado: {zip_historico.name}")
        else:
            print(f"{Color.AMARILLO}⚠️ No se pudo procesar la compresión. Continuando sin backup previo...{Color.RESET}")

    # Creamos la ruta blindada de forma automática
    dir_master_usb.mkdir(parents=True, exist_ok=True)
    copiados, borrados = copiar_sincronizada(dir_origen, dir_master_usb, modo_espejo)
    
    print(f"\n{Color.VERDE}┌────────────────────────────────────────────────────────────")
    print(f"│ 🎉 ¡PROCESO DE SINCRONIZACIÓN EXITOSO!                     ")
    print(f"├────────────────────────────────────────────────────────────")
    print(f"│ 📂 Origen local: {dir_origen}")
    print(f"│ 📥 Archivos agregados/modificados en USB: {copiados}")
    if modo_espejo:
        print(f"│ 🗑️ Archivos obsoletos purgados del USB: {borrados}")
    print(f"└────────────────────────────────────────────────────────────{Color.RESET}")
    input("\nPresiona ENTER para regresar al menú...")

# --- ACCIÓN 2: USB -> PC ---
def descargar_del_usb():
    limpiar_pantalla()
    mostrar_logo()
    print(f"{Color.AZUL}[USB -> PC] Preparando volcado de datos hacia este ordenador...{Color.RESET}\n")
    
    if not DIR_USB_BACKUPS.exists():
        print(f"{Color.ROJO}⚠️ El almacenamiento USB no contiene carpetas de backups.{Color.RESET}")
        input("\nPresiona ENTER para volver...")
        return

    proyectos = [d.name for d in DIR_USB_BACKUPS.iterdir() if d.is_dir()]
    if not proyectos:
        print(f"{Color.ROJO}⚠️ No se encontraron estructuras de proyectos válidas en el USB.{Color.RESET}")
        input("\nPresiona ENTER para volver...")
        return

    opciones_proyectos = proyectos + ["🔙 <= Volver al menú"]
    proyecto_elegido = seleccionar_opcion("Selecciona el proyecto que deseas restaurar en esta máquina:", opciones_proyectos)
    
    if proyecto_elegido == "🔙 <= Volver al menú":
        return

    dir_master_usb = DIR_USB_BACKUPS / proyecto_elegido / "MASTER"
    
    limpiar_pantalla()
    mostrar_logo()
    print(f"Determina la ruta de destino exacta para colocar '{proyecto_elegido}':\n")
    dir_destino = navegador_archivos(f"Selecciona la carpeta destino en tu PC para '{proyecto_elegido}':")

    # --- LA MAGIA DEL ENCAPSULAMIENTO ESTÁ AQUÍ ---
    # Asegura que la carpeta final lleve el nombre del proyecto, sin importar lo que elija el usuario
    if dir_destino.name != proyecto_elegido:
        dir_destino = dir_destino / proyecto_elegido
    # ----------------------------------------------

    modo = seleccionar_opcion("Selecciona la estrategia de sincronización en tu PC:", [
        "🔄 Conservar todo (Suma archivos nuevos, no toca lo propio de tu ordenador)",
        "🧹 Modo Espejo (Clona el USB; elimina lo local que no exista en el pendrive)"
    ])
    modo_espejo = "Modo Espejo" in modo

    destino_tiene_archivos = False
    try:
        if dir_destino.exists():
            destino_tiene_archivos = any(dir_destino.iterdir())
    except Exception:
        pass

    if destino_tiene_archivos:
        print(f"\n{Color.AMARILLO}⚠️ Destino detectado con contenido. Salvaguardando PC en .ZIP histórico...{Color.RESET}")
        gestionar_rotacion_backups(proyecto_elegido)
        fecha_pc = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        zip_respaldo_pc = DIR_USB_BACKUPS / proyecto_elegido / f"backup_{proyecto_elegido}_desdePC2_{fecha_pc}.zip"
        if crear_backup_zip(dir_destino, zip_respaldo_pc):
            print(f"{Color.VERDE}✅ Resguardo local archivado en USB correctamente: {zip_respaldo_pc.name}{Color.RESET}")
        else:
            print(f"{Color.AMARILLO}⚠️ Error al empaquetar salvaguarda local, procediendo bajo supervisión...{Color.RESET}")

    copiados, borrados = copiar_sincronizada(dir_master_usb, dir_destino, modo_espejo)

    print(f"\n{Color.VERDE}┌────────────────────────────────────────────────────────────")
    print(f"│ 🖥️  ¡SISTEMA LOCAL REFRESCADO CORRECTAMENTE!               ")
    print(f"├────────────────────────────────────────────────────────────")
    print(f"│ 📂 Destino local: {dir_destino}")
    print(f"│ 📤 Archivos transferidos exitosamente al PC: {copiados}")
    if modo_espejo:
        print(f"│ 🗑️ Archivos locales obsoletos eliminados: {borrados}")
    print(f"└────────────────────────────────────────────────────────────{Color.RESET}")
    input("\nPresiona ENTER para regresar al menú...")

# --- BUCLE PRINCIPAL ---
def main():
    while True:
        try:
            menu_principal = [
                "📥 1. Guardar cambios en el USB (PC -> USB)",
                "📤 2. Descargar/Actualizar este PC (USB -> PC)",
                "🔍 3. Ver y gestionar copias guardadas",
                "❌ 4. Salir de la aplicación"
            ]
            
            seleccion = seleccionar_opcion("¿Qué acción deseas ejecutar hoy?", menu_principal)
            
            if "1." in seleccion:
                subir_al_usb()
            elif "2." in seleccion:
                descargar_del_usb()
            elif "3." in seleccion:
                ver_y_gestionar_copias()
            elif "4." in seleccion:
                limpiar_pantalla()
                print(f"\n{Color.VERDE}👍 Sincronización finalizada ordenadamente. Ya puedes expulsar tu USB de forma segura. ¡Hasta luego!{Color.RESET}")
                logging.info("Sesión finalizada por el usuario.")
                sys.exit(0)
        except KeyboardInterrupt:
            limpiar_pantalla()
            print(f"\n\n{Color.VERDE}👋 Proceso finalizado mediante comando de cancelación (Ctrl+C). ¡Buen día!{Color.RESET}")
            sys.exit(0)
        except Exception as e:
            logging.critical(f"Error general inesperado en el bucle principal: {e}", exc_info=True)
            limpiar_pantalla()
            print(f"{Color.ROJO}⚠️ Se ha producido una anomalía inesperada en el hilo principal: {e}{Color.RESET}")
            input("\nPresiona ENTER para reiniciar el entorno del menú...")

if __name__ == "__main__":
    main()