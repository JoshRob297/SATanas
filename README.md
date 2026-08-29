# 🏛️ SATanas

<p align="center">
  <img src="https://img.shields.io/badge/Version-1.0.0-blue.svg" alt="Version 1.0.0" />
  <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue?logo=python" alt="Python Versions" />
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License MIT" />
  <img src="https://img.shields.io/badge/Architecture-On--Demand-orange.svg" alt="On-Demand" />
  <img src="https://img.shields.io/badge/SAT-CFDI%204.0%20%26%20Nómina-purple.svg" alt="SAT CFDI" />
</p>

**SATanas** es un bot de Telegram profesional, seguro y *On-Demand* diseñado para descargar, organizar y gestionar comprobantes fiscales (CFDI / Recibos de Nómina) en formato PDF y XML directamente desde el portal oficial del SAT (Servicio de Administración Tributaria de México).

Almacena los comprobantes de forma local y privada en una base de datos SQLite optimizada, permitiendo explorar, filtrar y reenviar recibos instantáneamente desde Telegram sin depender de servicios en la nube de terceros.

---

## ✨ Características Principales

- 🔐 **100% On-Demand y Privado:** Sin procesos en segundo plano ni rastreadores externos. El bot únicamente interactúa con el SAT cuando tú se lo solicitas.
- ⚡ **Descarga Directa de PDF y XML:** Descarga física nativa y extracción mediante PageMethods oficiales con resolución de popups en Playwright.
- 📂 **Explorador Interactivo (Dashboard Telegram):** Navegación mensual estilo calendario (4x3) con selector de año y vista tipo pestañas.
- 💼 **Filtro Rápido de Nómina:** Alterna al instante entre todos los comprobantes del mes o exclusivamente recibos de nómina.
- 📦 **Entregas en Formato Múltiple:** Envío directo de archivo PDF, XML o paquete comprimido ZIP con nombres normalizados y únicos (`Tipo_Emisor_Fecha_UUID8.ext`).
- 🤖 **Gestión Interactiva de CAPTCHA:** El bot te envía la imagen del CAPTCHA a Telegram, introduces el texto y continúa la descarga automáticamente con auto-limpieza del chat.
- 🗄️ **Base de Datos SQLite de Alto Rendimiento:** Configurada con modo WAL (`PRAGMA journal_mode=WAL;`), índices optimizados y concurrencia thread-safe.
- 🛡️ **Seguridad y OPSEC Rigurosos:** Esquema de autorización estricta (*Fail-Closed*), permisos de archivo reforzados (`0600`/`0700`) y sanitización exhaustiva contra Path Traversal.

---

## 🛠️ Requisitos Previos

- **Sistema Operativo:** Linux (Ubuntu/Debian recomendado), macOS o Windows.
- **Python:** 3.10 o superior.
- **Chromium / Playwright Dependencies:** Instaladas vía CLI.
- **Credenciales SAT:** RFC y Contraseña (CIEC).
- **Bot de Telegram:** Token creado mediante [@BotFather](https://t.me/BotFather) y tu Telegram User ID (obtenible vía `@userinfobot`).

---

## 🚀 Instalación y Despliegue

### 1. Clonar el repositorio y crear entorno virtual

```bash
git clone https://github.com/JoshRob297/SATanas.git
cd SATanas

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
playwright install-deps
```

### 2. Configurar variables de entorno

Copia la plantilla de ejemplo y edita tus valores:

```bash
cp .env.example .env
nano .env
```

Configura tu archivo `.env`:

```ini
# Token de tu bot en Telegram (de @BotFather)
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# IDs de Telegram autorizados (separados por coma). Acceso exclusivo.
ALLOWED_USER_IDS=123456789

# Credenciales del SAT (CIEC)
SAT_RFC=XAXX010101000
SAT_PASSWORD=TuPasswordCIEC

# Ventana histórica de sincronización en meses (Opcional, default: 12)
SYNC_MONTHS_BACK=12
```

Asegura los permisos de tu archivo de entorno:
```bash
chmod 600 .env
```

---

## 💻 Uso del Bot

Inicia el bot con:

```bash
./venv/bin/python -m satanas.main
```

### Comandos disponibles en Telegram:
- `/start` - Abre el panel de control principal interactivo.
- `/recibos` - Explorador de comprobantes locales agrupados por año y mes.
- `/sync` - Inicia la sincronización contra el portal del SAT (solicitará CAPTCHA).
- `/cancelar` - Aborta inmediatamente cualquier operación activa.
- `/help` - Muestra la guía de uso y funcionalidades.
- `/id` - Muestra tu ID numérico de Telegram para configuración.

---

## ⚙️ Servicio Automático (Systemd en Linux)

Para mantener el bot ejecutándose permanentemente como servicio de sistema:

1. Crea el archivo de servicio `/etc/systemd/system/satanas.service`:

```ini
[Unit]
Description=SATanas Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/proyectos/SATanas
ExecStart=/root/proyectos/SATanas/venv/bin/python -m satanas.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

2. Activa e inicia el servicio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable satanas.service
sudo systemctl start satanas.service
```

---

## 🧪 Pruebas Unitarias

El proyecto cuenta con una suite completa de pruebas unitarias con `pytest`:

```bash
pytest tests -v
```

---

## 📄 Licencia

Distribuido bajo la Licencia MIT. Consulta el archivo `LICENSE` para más información.
