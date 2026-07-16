import os
import sys
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# Intentar importar o instalar 'inquirer' automáticamente para asegurar la interactividad limpia
try:
    import inquirer
except ImportError:
    print("Instalando librería de interfaz interactiva...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "inquirer"])
    import inquirer

# --- CONFIGURACIÓN ---
DIR_USB_BACKUPS = Path(__file__).resolve().parent / "copy4me_backups"
MAX_BACKUPS = 10  # Límite de backups históricos a mantener

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
    print(f"    [ SINCRONIZADOR INTERACTIVO MULTIPLATAFORMA | MAX: {MAX_BACKUPS} ]")
    print("\033[93m------------------------------------------------------------\033[0m")

def seleccionar_opcion(titulo, opciones):
    """Usa inquirer para una selección limpia con flechas."""
    preguntas = [
        inquirer.List('opcion',
                      message=titulo,
                      choices=opciones,
                      carousel=True)
    ]
    respuestas = inquirer.prompt(preguntas)
    if not respuestas:  # Por si el usuario presiona Ctrl+C
        sys.exit(0)
    return respuestas['opcion']

def navegador_archivos(titulo_prompt="Selecciona una carpeta:"):
    """Navegador visual de directorios usando inquirer."""
    # Inicialización de ruta según el OS
    ruta_actual = Path('C:\\') if os.name == 'nt' else Path('/')
    if not ruta_actual.exists():
        ruta_actual = Path.cwd().root

    while True:
        mostrar_logo()
        print(f"\033[94mRuta actual: {ruta_actual}\033[0m\n")
        
        try:
            # Filtrar solo carpetas accesibles y visibles
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

# --- GESTIÓN Y LIMPIEZA DE BACKUPS ---
def gestionar_rotacion_backups(nombre_carpeta):
    carpeta_historico = DIR_USB_BACKUPS / nombre_carpeta
    if not carpeta_historico.exists():
        return

    backups_existentes = sorted(
        [d for d in carpeta_historico.iterdir() if d.is_dir() and d.name.startswith("backup_")],
        key=lambda x: x.stat().st_mtime
    )

    while len(backups_existentes) >= MAX_BACKUPS:
        antiguo = backups_existentes.pop(0)
        try:
            shutil.rmtree(antiguo)
            print(f"\033[91m♻️ Historial lleno: Se autodestruyó el backup más antiguo ({antiguo.name})\033[0m")
        except Exception as e:
            print(f"⚠️ No se pudo borrar el backup antiguo {antiguo.name}: {e}")

# --- COPIA INTELIGENTE (Con Control de Cancelación y Barra de Progreso) ---
def copiar_sincronizada(origen, destino):
    origen = Path(origen)
    destino = Path(destino)
    archivos_copiados = 0
    
    print("\033[93m🔍 Escaneando archivos y calculando tamaño del proyecto...\033[0m")
    todos_los_elementos = [item for item in origen.rglob('*') if item.is_file()]
    total_archivos = len(todos_los_elementos)
    
    if total_archivos == 0:
        print("ℹ️ No se encontraron archivos para procesar.")
        return 0

    print(f"📦 Total de archivos a verificar: {total_archivos}")
    print("\033[90m(Puedes presionar Ctrl+C en cualquier momento para cancelar de forma segura)\033[0m\n")
    
    try:
        for indice, item in enumerate(todos_los_elementos, 1):
            relativa = item.relative_to(origen)
            target = destino / relativa
            
            # Verificar si el archivo necesita actualizarse
            if not target.exists() or item.stat().st_mtime > target.stat().st_mtime:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                archivos_copiados += 1
                
            # Calcular porcentaje y barra [████░░░░░░]
            porcentaje = int((indice / total_archivos) * 100)
            bloques = int(porcentaje / 5)
            barra = "█" * bloques + "░" * (20 - bloques)
            
            # Mostrar progreso
            sys.stdout.write(f"\r⚡ Sincronizando: [{barra}] {porcentaje}% ({indice}/{total_archivos}) | Copiados: {archivos_copiados}")
            sys.stdout.flush()
            
        print("\n")
        return archivos_copiados

    except KeyboardInterrupt:
        # Esto se ejecuta si el usuario presiona Ctrl+C durante la copia
        print("\n\n\033[91m🛑 Proceso cancelado por el usuario (Ctrl+C).\033[0m")
        print(f"⚠️ La sincronización se detuvo. Se alcanzaron a copiar {archivos_copiados} archivos.")
        input("\nPresiona ENTER para regresar al menú principal...")
        return archivos_copiados

# --- ACCIÓN 1: PC -> USB ---
def subir_al_usb():
    mostrar_logo()
    print("\033[94m[PC -> USB] Sincronizando hacia el USB...\033[0m")
    print("Navega hasta la carpeta de trabajo en este PC:\n")
    
    dir_origen = navegador_archivos("Selecciona la carpeta origen en tu PC:")
    nombre_carpeta = dir_origen.name
    dir_master_usb = DIR_USB_BACKUPS / nombre_carpeta / "MASTER"
    
    if dir_master_usb.exists() and any(dir_master_usb.iterdir()):
        gestionar_rotacion_backups(nombre_carpeta)
        fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_historico = DIR_USB_BACKUPS / nombre_carpeta / f"backup_{nombre_carpeta}_desdePC1_{fecha}"
        print("\033[93mCreando punto de restauración histórico en el USB...\033[0m")
        shutil.copytree(dir_master_usb, dir_historico)

    dir_master_usb.mkdir(parents=True, exist_ok=True)
    print("\033[93mAnalizando y copiando archivos modificados al USB...\033[0m")
    copiados = copiar_sincronizada(dir_origen, dir_master_usb)
    
    print(f"\n\033[92m--- ¡SINCRONIZACIÓN EXITOSA! ---\033[0m")
    print(f"Archivos nuevos/actualizados transferidos al USB: {copiados}")
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

    if any(dir_destino.iterdir()):
        print("\033[93mLa carpeta contiene archivos. Guardando copia del PC en el USB antes de actualizar...\033[0m")
        gestionar_rotacion_backups(proyecto_elegido)
        fecha_pc = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        respaldo_pc_en_usb = DIR_USB_BACKUPS / proyecto_elegido / f"backup_{proyecto_elegido}_desdePC2_{fecha_pc}"
        shutil.copytree(dir_destino, respaldo_pc_en_usb)
        print(f"\n\033[92mSalvaguarda guardada en el USB: {respaldo_pc_en_usb.name}\033[0m")

    print("\033[93mTrayendo archivos actualizados desde el USB al PC...\033[0m")
    copiados = copiar_sincronizada(dir_master_usb, dir_destino)

    print(f"\n\033[92m--- ¡PC ACTUALIZADO! ---\033[0m")
    print(f"Archivos transferidos al PC: {copiados}")
    input("\nPresiona ENTER para volver...")

# --- BUCLE PRINCIPAL ---
def main():
    while True:
        mostrar_logo()
        menu_principal = [
            "📥 1. Guardar cambios en el USB (PC -> USB)",
            "📤 2. Descargar/Actualizar este PC (USB -> PC)",
            "❌ 3. Salir"
        ]
        
        seleccion = seleccionar_opcion("¿Qué acción deseas realizar?", menu_principal)
        
        if "1." in seleccion:
            subir_al_usb()
        elif "2." in seleccion:
            descargar_del_usb()
        elif "3." in seleccion:
            print("\033[92m¡Sincronización terminada! Ya puedes retirar tu USB. ¡Adiós!\033[0m")
            sys.exit(0)

if __name__ == "__main__":
    main()