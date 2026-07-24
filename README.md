
# 📂 copy4me (ALL4ME)

**Copy4Me** es una solución ligera, segura e intuitiva escrita en Python para la sincronización de archivos y gestión de copias de seguridad de proyectos locales hacia dispositivos de almacenamiento externo (USB, discos duros) o carpetas en red. Pensada para tener acualizados tus documentos entre multiples workstation de forma controlada y segura. 

**Estamos en fase de pruebas**, esperamos afianzar la aplicación antes de convertirla en ejecutable.

---

## ✨ Características Principales

* 🔄 **Modos de Sincronización Flexible**:
  * **Incremental**: Copia únicamente archivos nuevos o modificados sin borrar nada.
  * **Espejo**: Mantiene el destino $100\%$ idéntico al origen (elimina en destino lo que se haya borrado en el origen).
  * **Bidireccional**: Sincroniza cambios en ambas direcciones.
* 📦 **Historial de Respaldos ZIP y Rotación**:
  * Generación de paquetes comprimidos `.zip` con rotación automática configurable.
  * Límite predeterminado de **10 backups** para optimizar el uso de espacio.
* 🔒 **Cifrado AES-256 de Alta Seguridad**:
  * Protección con contraseña mediante derivación de clave `PBKDF2` y cifrado por flujo por bloques `AES-CBC`.
* 🛡️ **Verificación de Integridad SHA-256**:
  * Comprobación estricta de hash de cada archivo tras la copia para evitar corrupción de datos.
* 🔌 **Detección Automática de Unidades USB**:
  * Identificación dinámica de unidades de almacenamiento extraíbles en Windows, Linux y macOS.
* 🖥️ **Interfaz Gráfica (GUI) y Consola (TUI)**:
  * Interfaz rica construida en Tkinter con soporte para pantallas de alta densidad DPI.
  * Caída automática a modo Consola (TUI) en entornos de terminal o servidores sin servidor X.

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
    

## 📁 Estructura del Proyecto

Plaintext

```
copy4me/
├── copy4me.py               # Script principal del programa
├── config.json              # Configuración y perfiles (se genera automáticamente)
├── copy4me_backups/         # Carpeta por defecto para respaldos locales e historiales
├── README.md                # Documentación del proyecto
└── .gitignore               # Archivos excluidos del control de versiones
```

## 🔒 Seguridad y Privacidad

- Las copias de seguridad cifradas generan archivos `.zip.enc`.
    
- Los archivos cifrados **solo pueden recuperarse** con la contraseña proporcionada durante su creación.
    
- Se incluye protección contra vulnerabilidades de extracción de rutas relativas (**Zip Slip**).
    

## 📄 Licencia

Este proyecto está bajo la Licencia **MIT**. Puedes usarlo, modificarlo y distribuirlo libremente.

