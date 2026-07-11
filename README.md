# Inmobiliaria Oriente Juárez

App web para gestión de inventario inmobiliario con crédito Infonavit.

## Deploy en Render

1. Sube este proyecto a GitHub
2. Entra a render.com → New Web Service → conecta el repo
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app`
5. En "Environment" agrega: `PYTHON_VERSION = 3.11.0`
