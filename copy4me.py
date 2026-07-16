import os
import sys
import shutil
import subprocess
import logging
import zipfile
from datetime import datetime
from pathlib import Path

# Intentar importar o instalar 'inquirer' automáticamente
try:
    import inquirer
except ImportError:
    print("Instalando librería de interfaz interactiva...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "inquirer"])
    import inquirer

# --- CONFIGURACIÓN ---
DIR_USB_BACKUPS = Path(__file__).resolve().parent / "copy4me_backups"
MAX_BACKUPS = 10
EXCLUDE_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env', '.idea', '.vscode'}

# Configurar Logging
DIR_USB_BACKUPS.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=DIR_USB_BACKUPS / "sync_history.log",
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def mostrar_logo():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("\033[96m")
    print(r'''
        ____ ___  ____  _    _ _  _    _     _____ 
       / ___/ _ \|  _ \| | | | | | |/ |   | ____|
      | |  | | | | |_) | |_| | |_| |/ |___|  _|  
      | |__| |_| |  __/ \__, |___  _|| |___| |___ 
       \____\___/|_|    |___/    |_| |_|   |_____|
    ''')
    print(f"    [ SINCRONIZADOR OPTIMIZADO MULTIPLATAFORMA | MAX: {MAX_BACKUPS} ]")
    print("\033[93m------------------------------------------------------------\033[0m")

def seleccionar_opcion(titulo, opciones):
    preguntas = [
        inquirer.List('opcion', message=titulo, choices=opciones, carousel=True)
    ]
    respuestas = inquirer.prompt(preguntas)
    if not respuestas:
        sys.exit(0)
    return respuestas['opcion']

def obtener_tamano_formateado(ruta):
    """Calcula el tamaño de un directorio o archivo ZIP."""
    if ruta.is_file():
        total_size = ruta.stat().st_size
    else:
        total_size = 0
        for dirpath, _, filenames in os.walk(ruta):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
    
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if total_size < 1024.0:
            return f"{total_size:.2f} {unit}"
        total_size /= 1024.0

def crear_backup_zip(origen, destino_zip):
    """Crea un archivo ZIP comprimido para ahorrar espacio en los históricos."""
    try:
        with zipfile.ZipFile(destino_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(origen):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for file in files:
                    ruta_completa = Path(root) / file
                    zipf.write(ruta_completa, ruta_completa.relative_to(origen))
        return True
    except Exception as e:
        logging.error(f"Error creando ZIP {destino_zip}: {e}")
        return False

def navegador_archivos(titulo_prompt="Selecciona una carpeta:"):
    ruta_actual = Path('C:\\') if os.name == 'nt' else Path('/')
    if not ruta_actual.exists():
        ruta_actual = Path.cwd().root

    while True:
        mostrar_logo()
        print(f"\033[94mRuta actual: {ruta_actual}\033[0m\n")
        
        try:
            subcarpetas = [d for d in ruta_actual.iterdir() if d.is_dir() and not d.name.startswith('.')]
            subcarpetas.sort(key=lambda x: x.name.lower())
        except PermissionError:
            print("\033[91m⚠️ Sin permisos para acceder a esta carpeta.\033[0m")
            input("\nPresiona ENTER para volver atrás...")
            ruta_actual = ruta_actual.parent
            continue

        opciones = ["[ SELECCIONAR ESTA CARPETA ]", ".. (Ir atrás)"]
        if os.name == 'nt':
            opciones.append("[ Cambiar de Unidad de Disco ]")

        opciones.extend([f"📁 {d.name}" for d in subcarpetas])
        
        eleccion = seleccionar_opcion(titulo_prompt, opciones)
        
        if eleccion == "[ SELECCIONAR ESTA CARPETA ]":
            return ruta_actual
        elif eleccion == ".. (Ir atrás)":
            if ruta_actual.parent != ruta_actual:
                ruta_actual = ruta_actual.parent
        elif eleccion == "[ Cambiar de Unidad de Disco ]":
            unidades = [f"{d}:\\" for d in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if os.path.exists(f"{d}:\\")]
            unidad_elegida = seleccionar_opcion("Selecciona unidad de disco:", unidades)
            ruta_actual = Path(unidad_elegida)
        else:
            nombre_carpeta = eleccion.replace("📁 ", "")
            ruta_actual = ruta_actual / nombre_carpeta

# --- GESTIÓN DE COPIAS ---
def gestionar_rotacion_backups(nombre_carpeta):
    carpeta_historico = DIR_USB_BACKUPS / nombre_carpeta
    if not carpeta_historico.exists():
        return

    # Buscar tanto carpetas de backup antiguas como los nuevos archivos ZIP
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
            print(f"\033[91m♻️ {msg}\033[0m")
            logging.info(msg)
        except Exception as e:
            print(f"⚠️ No se pudo borrar el backup antiguo {antiguo.name}: {e}")

def ver_y_gestionar_copias():
    while True:
        mostrar_logo()
        print("\033[94m[🔍 GESTOR DE COPIAS] Explorando el USB...\033[0m\n")
        
        if not DIR_USB_BACKUPS.exists() or not any(DIR_USB_BACKUPS.iterdir()):
            print("\033[91m⚠️ No hay proyectos guardados en el USB todavía.\033[0m")
            input("\nPresiona ENTER para volver al menú...")
            return

        proyectos = [d.name for d in DIR_USB_BACKUPS.iterdir() if d.is_dir()]
        opciones_proyectos = proyectos + ["<= Volver al menú principal"]
        
        proyecto_elegido = seleccionar_opcion("Selecciona un proyecto para ver sus copias:", opciones_proyectos)
        
        if proyecto_elegido == "<= Volver al menú principal":
            return
            
        carpeta_proyecto = DIR_USB_BACKUPS / proyecto_elegido
        copias = sorted([d for d in carpeta_proyecto.iterdir() if d.name.startswith("backup_")], key=lambda x: x.stat().st_mtime, reverse=True)
        
        while True:
            mostrar_logo()
            print(f"\033[94mProyecto: {proyecto_elegido}\033[0m\n")
            
            if not copias:
                print("No hay copias de seguridad de este proyecto.")
                input("\nPresiona ENTER para volver...")
                break

            opciones_copias = []
            for copia in copias:
                tamano = obtener_tamano_formateado(copia)
                fecha = datetime.fromtimestamp(copia.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
                icono = "📁" if copia.is_dir() else "📦"
                etiqueta = f"{icono} {copia.name} | 📅 {fecha} | 💾 {tamano}"
                opciones_copias.append(etiqueta)
                
            opciones_copias.append("<= Volver a la lista de proyectos")
            
            copia_elegida = seleccionar_opcion("Selecciona una copia para gestionar:", opciones_copias)
            
            if copia_elegida == "<= Volver a la lista de proyectos":
                break
                
            nombre_real_copia = copia_elegida.split(" | ")[0].replace("📁 ", "").replace("📦 ", "")
            ruta_copia_exacta = carpeta_proyecto / nombre_real_copia
            
            accion = seleccionar_opcion(f"¿Qué deseas hacer con '{nombre_real_copia}'?", [
                "🗑️ Eliminar esta copia",
                "<= Cancelar"
            ])
            
            if accion == "🗑️ Eliminar esta copia":
                confirmacion = seleccionar_opcion("¿Estás seguro?", ["Sí, eliminar", "No, cancelar"])
                if confirmacion == "Sí, eliminar":
                    if ruta_copia_exacta.is_dir():
                        shutil.rmtree(ruta_copia_exacta)
                    else:
                        ruta_copia_exacta.unlink()
                    print(f"\033[92mCopia eliminada exitosamente.\033[0m")
                    logging.info(f"Usuario eliminó manualmente la copia: {ruta_copia_exacta.name}")
                    input("\nPresiona ENTER para continuar...")
                    copias = sorted([d for d in carpeta_proyecto.iterdir() if d.name.startswith("backup_")], key=lambda x: x.stat().st_mtime, reverse=True)
                    if not copias:
                        break

# --- COPIA INTELIGENTE ---
def copiar_sincronizada(origen, destino, modo_espejo=False):
    origen = Path(origen)
    destino = Path(destino)
    archivos_copiados = 0
    archivos_eliminados = 0
    
    print("\033[93m🔍 Escaneando archivos y calculando tamaño del proyecto...\033[0m")
    
    todos_los_elementos = []
    for root, dirs, files in os.walk(origen):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for file in files:
            todos_los_elementos.append(Path(root) / file)
            
    total_archivos = len(todos_los_elementos)
    
    if total_archivos == 0 and not modo_espejo:
        print("ℹ️ No se encontraron archivos válidos para procesar.")
        return 0, 0

    # Lógica de eliminación en modo espejo (Rsync behavior)
    if modo_espejo and destino.exists():
        for root, dirs, files in os.walk(destino):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in files:
                ruta_dest = Path(root) / file
                relativa = ruta_dest.relative_to(destino)
                ruta_orig = origen / relativa
                if not ruta_orig.exists():
                    ruta_dest.unlink()
                    archivos_eliminados += 1

    try:
        for indice, item in enumerate(todos_los_elementos, 1):
            relativa = item.relative_to(origen)
            target = destino / relativa
            
            if not target.exists() or item.stat().st_mtime > target.stat().st_mtime:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                archivos_copiados += 1
                
            porcentaje = int((indice / total_archivos) * 100) if total_archivos > 0 else 100
            bloques = int(porcentaje / 5)
            barra = "█" * bloques + "░" * (20 - bloques)
            
            sys.stdout.write(f"\r⚡ Sincronizando: [{barra}] {porcentaje}% ({indice}/{total_archivos}) | Copiados: {archivos_copiados} | Borrados: {archivos_eliminados}")
            sys.stdout.flush()
            
        print("\n")
        logging.info(f"Sincronización completada. Origen: {origen.name} | Copiados: {archivos_copiados} | Borrados: {archivos_eliminados}")
        return archivos_copiados, archivos_eliminados

    except KeyboardInterrupt:
        print("\n\n\033[91m🛑 Proceso cancelado por el usuario (Ctrl+C).\033[0m")
        return archivos_copiados, archivos_eliminados
    except Exception as e:
        print(f"\n\n\033[91m❌ Error inesperado durante la copia: {e}\033[0m")
        return archivos_copiados, archivos_eliminados

# --- ACCIÓN 1: PC -> USB ---
def subir_al_usb():
    mostrar_logo()
    print("\033[94m[PC -> USB] Sincronizando hacia el USB...\033[0m")
    
    dir_origen = navegador_archivos("Selecciona la carpeta origen en tu PC:")
    nombre_carpeta = dir_origen.name
    dir_master_usb = DIR_USB_BACKUPS / nombre_carpeta / "MASTER"
    
    # Preguntar tipo de sincronización
    modo = seleccionar_opcion("Selecciona el tipo de sincronización:", [
        "🔄 Conservar todo (Suma archivos nuevos, no borra nada del USB)",
        "🧹 Modo Espejo (Hace que el USB sea EXACTAMENTE igual al PC, borrando lo eliminado)"
    ])
    modo_espejo = "Modo Espejo" in modo

    if dir_master_usb.exists() and any(dir_master_usb.iterdir()):
        gestionar_rotacion_backups(nombre_carpeta)
        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_historico = DIR_USB_BACKUPS / nombre_carpeta / f"backup_{nombre_carpeta}_desdePC1_{fecha}.zip"
        
        print("\033[93mCreando punto de restauración comprimido (.ZIP) en el USB...\033[0m")
        if crear_backup_zip(dir_master_usb, zip_historico):
            logging.info(f"Backup histórico ZIP creado: {zip_historico.name}")
        else:
            print("⚠️ No se pudo comprimir, omitiendo backup histórico...")

    dir_master_usb.mkdir(parents=True, exist_ok=True)
    copiados, borrados = copiar_sincronizada(dir_origen, dir_master_usb, modo_espejo)
    
    print(f"\n\033[92m--- ¡SINCRONIZACIÓN EXITOSA! ---\033[0m")
    print(f"Archivos nuevos/actualizados transferidos al USB: {copiados}")
    if modo_espejo:
        print(f"Archivos obsoletos eliminados del USB: {borrados}")
    input("\nPresiona ENTER para volver...")

# --- ACCIÓN 2: USB -> PC ---
def descargar_del_usb():
    mostrar_logo()
    print("\033[94m[USB -> PC] Descargando última versión al PC...\033[0m")
    
    if not DIR_USB_BACKUPS.exists():
        print("\033[91m⚠️ El USB no contiene ninguna carpeta sincronizada todavía.\033[0m")
        input("\nPresiona ENTER para volver...")
        return

    proyectos = [d.name for d in DIR_USB_BACKUPS.iterdir() if d.is_dir()]
    if not proyectos:
        print("\033[91m⚠️ No se encontraron carpetas en el USB.\033[0m")
        input("\nPresiona ENTER para volver...")
        return

    opciones_proyectos = proyectos + ["<= Volver al menú"]
    proyecto_elegido = seleccionar_opcion("Selecciona la carpeta que deseas volcar en este PC:", opciones_proyectos)
    
    if proyecto_elegido == "<= Volver al menú":
        return

    dir_master_usb = DIR_USB_BACKUPS / proyecto_elegido / "MASTER"
    
    mostrar_logo()
    print(f"Selecciona el directorio del PC donde quieres descargar '{proyecto_elegido}':\n")
    dir_destino = navegador_archivos(f"Selecciona la carpeta de destino para '{proyecto_elegido}':")

    modo = seleccionar_opcion("Selecciona el tipo de sincronización para tu PC:", [
        "🔄 Conservar todo (Suma archivos nuevos, no borra nada de tu PC)",
        "🧹 Modo Espejo (Hace que el PC sea EXACTAMENTE igual al USB, borrando lo eliminado)"
    ])
    modo_espejo = "Modo Espejo" in modo

    if any(dir_destino.iterdir()):
        print("\033[93mLa carpeta del PC no está vacía. Creando resguardo en .ZIP en el USB...\033[0m")
        gestionar_rotacion_backups(proyecto_elegido)
        fecha_pc = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        zip_respaldo_pc = DIR_USB_BACKUPS / proyecto_elegido / f"backup_{proyecto_elegido}_desdePC2_{fecha_pc}.zip"
        if crear_backup_zip(dir_destino, zip_respaldo_pc):
            print(f"\n\033[92mSalvaguarda comprimida guardada en el USB: {zip_respaldo_pc.name}\033[0m")
        else:
            print("⚠️ No se pudo crear la salvaguarda, procediendo con precaución...")

    copiados, borrados = copiar_sincronizada(dir_master_usb, dir_destino, modo_espejo)

    print(f"\n\033[92m--- ¡PC ACTUALIZADO! ---\033[0m")
    print(f"Archivos transferidos al PC: {copiados}")
    if modo_espejo:
        print(f"Archivos obsoletos eliminados de tu PC: {borrados}")
    input("\nPresiona ENTER para volver...")

# --- BUCLE PRINCIPAL ---
def main():
    while True:
        mostrar_logo()
        menu_principal = [
            "📥 1. Guardar cambios en el USB (PC -> USB)",
            "📤 2. Descargar/Actualizar este PC (USB -> PC)",
            "🔍 3. Ver y gestionar copias guardadas",
            "❌ 4. Salir"
        ]
        
        seleccion = seleccionar_opcion("¿Qué acción deseas realizar?", menu_principal)
        
        if "1." in seleccion:
            subir_al_usb()
        elif "2." in seleccion:
            descargar_del_usb()
        elif "3." in seleccion:
            ver_y_gestionar_copias()
        elif "4." in seleccion:
            print("\033[92m¡Sincronización terminada! Ya puedes retirar tu USB. ¡Adiós!\033[0m")
            logging.info("Sesión finalizada por el usuario.")
            sys.exit(0)

if __name__ == "__main__":
    main()