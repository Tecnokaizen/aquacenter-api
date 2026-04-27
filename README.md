# aquacenter-api

API FastAPI lista para desplegar en EasyPanel con Docker, volumen persistente y consumo desde n8n por HTTP.

## Endpoints

- `GET /health`
- `POST /extract` (`multipart/form-data`)
  - `file`: `.pdf`, `.xlsx`, `.xls`, `.csv`, `.eml`
  - `module`: `confirmacion_pedidos` | `revision_facturas` | `actualizacion_tarifas`
  - `use_ai`: `true/false`
- `GET /outputs/{file_name}` descarga Excel de salida

## Seguridad

`POST /extract` exige cabecera:

`Authorization: Bearer <API_KEY>`

## Variables de entorno

```env
APP_ENV=production
API_KEY=clave-larga-segura
MAX_UPLOAD_MB=50
DATA_DIR=/app/data
UPLOADS_DIR=/app/data/uploads
OUTPUTS_DIR=/app/data/outputs
LOGS_DIR=/app/data/logs

OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
```

## Desarrollo local

```bash
cd aquacenter-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Pruebas rápidas:

- [http://localhost:8000/health](http://localhost:8000/health)
- [http://localhost:8000/docs](http://localhost:8000/docs)

## Deploy en EasyPanel

1. Crear proyecto `aquacenter-mvp`.
2. Crear servicio `api-aquacenter`.
3. Conectar repositorio GitHub.
4. Build con `Dockerfile` (esta carpeta).
5. Puerto interno `8000` (HTTP).
6. Añadir volumen persistente en `/app/data`.
7. Configurar variables de entorno.
8. Añadir dominio (ej. `api-aquacenter.tudominio.com`).
9. Deploy.
10. Validar:
   - `GET /health`
   - `POST /extract` desde `/docs`
   - `GET /outputs/{file_name}`

## Integración n8n (HTTP)

Nodo HTTP Request:

- Method: `POST`
- URL: `https://api-aquacenter.tudominio.com/extract`
- Body: `multipart/form-data`
- Headers:
  - `Authorization: Bearer <API_KEY>`
- Campos:
  - `file` (binario del documento)
  - `module` (ej. `confirmacion_pedidos`)
  - `use_ai` (`false` para MVP)

Respuesta JSON incluye `output_excel` con ruta descargable (`/outputs/...`).
