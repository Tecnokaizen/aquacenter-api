# aquacenter-api

API FastAPI lista para desplegar en EasyPanel con Docker, volumen persistente y consumo desde n8n por HTTP.

## Endpoints

- `GET /health`
- `GET /healthz`
- `GET /version`
- `POST /extract` (`multipart/form-data`)
  - `file`: `.pdf`, `.xlsx`, `.xls`, `.csv`, `.eml`
  - `module`: `confirmacion_pedidos` | `revision_facturas` | `actualizacion_tarifas`
  - `use_ai`: `true/false`
- `POST /compare` (`multipart/form-data`)
  - `origin_file`: PDF pedido original
  - `target_file`: PDF confirmación proveedor
  - `module`: `confirmacion_pedidos`
  - `use_ai`: `true/false` (MVP recomendado `false`)
- `POST /compare-batch` (`multipart/form-data`)
  - `files[]`: múltiples PDFs (pedidos y confirmaciones mezclados)
  - `module`: `confirmacion_pedidos`
  - `use_ai`: `true/false`
- `GET /ui/compare` interfaz web mínima para comparación manual
- `GET /outputs/{file_name}` descarga Excel de salida

## Seguridad

`POST /extract`, `POST /compare` y `POST /compare-batch` exigen cabecera:

`Authorization: Bearer <API_KEY>`

En Swagger (`/docs`), usa el botón `Authorize` para introducir el token Bearer.

Nota de compatibilidad n8n:
- Para PDFs, la API acepta `application/pdf` y también `application/octet-stream` si el nombre de archivo termina en `.pdf`.
- La UI `/ui/compare` está pensada para entorno interno.

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
También devuelve:
- `extraction_method`
- `confidence`
- `output_excel_url` (URL absoluta)

Para `/compare` la respuesta incluye:
- `success`, `job_id`
- `overall_status` (`ok` | `with_incidents` | `failed`)
- `origin_document_type`, `target_document_type`
- `supplier`
- `lines_origin`, `lines_target`, `lines_ok`
- `incidents_total`, `incidents`
- `output_excel`
- `output_excel_url` (URL absoluta)

Para `/compare-batch` la respuesta incluye:
- `batch_id`
- `documents_total`
- `pairs_detected`
- `comparisons_ok`
- `comparisons_with_incidents`
- `unmatched_documents`
- `incidents_total`
- `batch_excel`
- `batch_excel_url`
