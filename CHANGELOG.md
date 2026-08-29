# 📜 Historial de Cambios (Changelog)

Todas las modificaciones notables de este proyecto serán documentadas en este archivo.
El formato se basa en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/) y este proyecto se adhiere a [Semantic Versioning](https://semver.org/lang/es/).

---

## [1.0.0] - 2026-08-28

### ✨ Añadido
- **Arquitectura On-Demand:** Eliminación de procesos en segundo plano para control manual exclusivo.
- **Explorador Interactivo:** Dashboard mensual tipo cuadrícula (4x3) con selector de año.
- **Descargas Físicas:** Soporte completo para captura de XML y PDF vía PageMethods nativos del SAT.
- **Filtro de Nómina:** Alternancia instantánea entre comprobantes ordinarios y recibos de sueldo/nómina.
- **Formato ZIP:** Generación dinámica de paquetes comprimidos con PDF + XML por comprobante.
- **Ventana Dinámica:** Variable de configuración `SYNC_MONTHS_BACK` para definir meses históricos a sincronizar.
- **Integración Continua:** Workflow de GitHub Actions para pruebas automatizadas en Python 3.10, 3.11 y 3.12.

### 🛡️ Seguridad
- Esquema de autorización estricta (*Fail-Closed*) cuando `ALLOWED_USER_IDS` no está definido.
- Permisos restrictivos (`0700`/`0600`) aplicados por defecto a directorios y base de datos SQLite.
- Sanitización de identificadores UUID contra ataques de *Path Traversal*.
- Almacenamiento local aislado en `~/.satanas/` sin telemetría externa.

### ⚡ Rendimiento
- Activación de SQLite WAL mode (`PRAGMA journal_mode=WAL;`) para lecturas y escrituras simultáneas sin bloqueos.
- Creación de índices en `(anio, mes)`, `fecha_emision` y campos de estado de descarga.
