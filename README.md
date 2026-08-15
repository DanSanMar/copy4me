
# 📂 copy4me (ALL4ME)

**Copy4Me** es una solución ligera, segura e intuitiva escrita en Python para la sincronización de archivos y gestión de copias de seguridad de proyectos locales hacia dispositivos de almacenamiento externo (USB, discos duros) o carpetas en red. Pensada para tener acualizados tus documentos entre multiples workstation de forma controlada y segura. 

**Estamos en fase de pruebas**, esperamos afianzar la aplicación antes de convertirla en ejecutable.

---

## ✨ Características Principales

* 🔄 **Modos de Sincronización Flexible**:
  * **Incremental / Normal**: Copia únicamente archivos nuevos o modificados sin borrar nada.
  * **Espejo**: Mantiene el destino $100\%$ idéntico al origen (elimina en destino archivos y directorios vacíos que se hayan borrado en el origen).
  * **Bidireccional**: Combina y sincroniza los cambios en ambas direcciones.
* 📦 **Tareas Configurables y Respaldos ZIP**:
  * Ejecución flexible: permite elegir sincronización directa de archivos, creación de paquete comprimido `.zip` o ambas opciones de manera simulatánea.
  * Rotación automática de historiales `.zip` basada en un límite máximo configurable (por defecto 10 backups por proyecto).
* 💾 **Validación de Espacio en Disco**:
  * Comprobación preventiva del espacio disponible en la unidad de destino (calculando incluso el tamaño real descompreso para operaciones de restauración).
* 🔒 **Cifrado AES-256 de Alta Seguridad**:
  * Protección opcional de paquetes `.zip` con contraseña mediante derivación de clave `PBKDF2` y cifrado por bloques `AES-256 CBC` (generando archivos `.zip.enc`).
* 🛡️ **Verificación de Integridad SHA-256**:
  * Comprobación de firma hash de cada archivo tras la copia y política de reintentos automáticos para evitar corrupción de datos.
* 📋 **Reportes y Diagnósticos Detallados**:
  * Consola de depuración en tiempo real y ventana emergente de resumen con el listado preciso de modificaciones realizadas al finalizar cada tarea.
* 🔌 **Detección Automática de Unidades USB**:
  * Identificación dinámica de dispositivos de almacenamiento extraíbles en Windows, Linux y macOS.
* 🖥️ **Interfaz Gráfica (GUI) y Consola (TUI)**:
  * Interfaz construida en Tkinter con adaptación de densidad de píxeles (High DPI) en Windows.
  * Caída automática al modo Consola interactivo (TUI) en entornos de terminal sin soporte gráfico.
---

## 🛠️ Requisitos e Instalación

### Requisitos de Python
* **Python 3.8** o superior.

### Instalación de Dependencias

El programa solo requiere la librería `pycryptodome` para activar las funciones avanzadas de cifrado.

```bash
pip install pycryptodome
````

_(Las librerías para la interfaz gráfica `tkinter` vienen incluidas por defecto en la mayoría de instalaciones de Python)._

## 🚀 Uso del Programa

### Ejecución Directa

Ejecuta el script principal desde la terminal o haciendo doble clic:

Bash

```
python copy4me.py
```

- Si dispones de un entorno de escritorio gráfico, se abrirá la **interfaz gráfica (GUI)**.
    
- Si ejecutas el script en una terminal sin soporte gráfico, se iniciará el **modo consola interactivo (TUI)** automáticamente.
    
⚙️ Configuración y Exclusiones

El sistema administra las configuraciones y perfiles de manera atómica a través del archivo config.json. Puedes personalizar parámetros como:

    Exclusiones por Extensión y Regex: Ignora carpetas del sistema (.git, node_modules, venv, etc.) o archivos temporales de forma global.

    Nivel de Compresión: Ajustable de 0 a 9.

    Retención de Historial: Límite personalizado de backups antiguos conservados por proyecto.    

## 📁 Estructura del Proyecto

Plaintext

```
copy4me/
├── copy4me.py               # Script principal del programa
├── config.json              # Configuración y perfiles (se genera automáticamente)
├── copy4me_backups/         # Carpeta por defecto para respaldos locales, logs e historiales
│   └── sync_history.log    # Registro de depuración en rotación
├── README.md                # Documentación del proyecto
└── .gitignore               # Archivos excluidos del control de versiones
```

## 🔒 Seguridad y Privacidad

- Las copias de seguridad cifradas generan archivos `.zip.enc`.
    
- Los archivos cifrados **solo pueden recuperarse** con la contraseña proporcionada durante su creación.
    
- Se incluye protección contra vulnerabilidades de extracción de rutas relativas (**Zip Slip**).
    

## 📄 Licencia

Este proyecto está bajo la Licencia **MIT**. Puedes usarlo, modificarlo y distribuirlo libremente.

