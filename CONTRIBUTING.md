# 🤝 Guía de Contribución

¡Gracias por tu interés en contribuir a **SATanas**! 

## 📋 Reglas Generales

1. Mantén los cambios enfocados: un Pull Request debe resolver un problema o agregar una funcionalidad específica.
2. Respeta las directrices de seguridad: **nunca** subas credenciales, tokens, ni datos fiscales a los tests o issues.
3. Asegura que todas las pruebas pasen antes de enviar un Pull Request.

---

## 🛠️ Flujo de Trabajo para el Desarrollo

1. **Haz un Fork** del repositorio en GitHub.
2. **Clona tu fork** localmente:
   ```bash
   git clone https://github.com/TU_USUARIO/SATanas.git
   cd SATanas
   ```
3. **Crea un entorno virtual e instala las dependencias**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. **Crea una nueva rama** para tu funcionalidad o corrección:
   ```bash
   git checkout -b feat/mi-nueva-funcionalidad
   # o
   git checkout -b fix/correccion-de-bug
   ```
5. **Escribe pruebas unitarias** para cubrir tus cambios dentro del directorio `tests/`.
6. **Ejecuta los tests** para confirmar que todo funciona:
   ```bash
   pytest tests -v
   ```
7. **Haz commit de tus cambios** siguiendo la convención de [Conventional Commits](https://www.conventionalcommits.org/):
   ```bash
   git commit -m "feat(modulo): descripcion concisa del cambio"
   ```
8. **Empuja a tu fork** y abre un Pull Request contra la rama `main`.

---

## 🐛 Reportar Bugs y Sugerir Mejoras

- Para reportar errores, utiliza la plantilla de **Bug Report** en los Issues de GitHub.
- Para proponer nuevas funciones, utiliza la plantilla de **Feature Request**.
- Si identificas una vulnerabilidad de seguridad, consulta [SECURITY.md](SECURITY.md) para divulgarla de forma privada y responsable.
