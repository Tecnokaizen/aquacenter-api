# Integración n8n con aquacenter-api (Batch)

Esta guía describe cómo conectar `n8n` con `aquacenter-api` para procesar documentos en lote usando `POST /compare-batch`.

## 1) URL base

`https://aquacenter-mvp-api-aquacenter.rb0pxk.easypanel.host`

## 2) Endpoint principal

`POST /compare-batch`

URL completa:

`https://aquacenter-mvp-api-aquacenter.rb0pxk.easypanel.host/compare-batch`

## 3) Header de autenticación

```http
Authorization: Bearer API_KEY
```

En n8n:
- Header Name: `Authorization`
- Header Value: `Bearer {{ $env.API_KEY }}`

## 4) Body multipart (request)

`multipart/form-data` con:
- `module=confirmacion_pedidos`
- `use_ai=false`
- `files=<múltiples PDFs>`

Notas:
- El campo debe llamarse `files` (mismo nombre repetido para cada PDF en multipart).
- No enviar documentos uno a uno en producción.

## 5) Flujo n8n recomendado

1. `Manual Trigger` (o `Cron` para producción).
2. `Google Drive - List Files` en carpeta de entrada.
3. `Google Drive - Download File` para cada documento.
4. `HTTP Request` a `/compare-batch` enviando todos los binarios en un único request multipart.
5. `IF` condición: `incidents_total > 0`.
6. `Google Drive - Upload File` del Excel global y/o `Move File` de documentos procesados.
7. `Email` o `Telegram` con resumen del lote.

Recomendación práctica en n8n:
- Agrupar binarios antes del `HTTP Request` para que salgan en una sola llamada batch.
- Guardar el `batch_id` y `batch_excel_url` para trazabilidad.

## 6) Ejemplo de respuesta JSON

```json
{
  "batch_id": "batch_a1b2c3d4e5",
  "documents_total": 12,
  "pairs_detected": 6,
  "comparisons_ok": 4,
  "comparisons_with_incidents": 2,
  "unmatched_documents": [
    {
      "filename": "pedido_suelto_001.pdf",
      "document_type": "pedido",
      "supplier": "POTERMIC, S.A.",
      "reason": "NO_MATCH_CONFIRMACION",
      "warnings": []
    }
  ],
  "incidents_total": 3,
  "batch_excel": "/outputs/batch_a1b2c3d4e5.xlsx",
  "batch_excel_url": "https://aquacenter-mvp-api-aquacenter.rb0pxk.easypanel.host/outputs/batch_a1b2c3d4e5.xlsx",
  "pairs": [
    {
      "pair_id": 1,
      "origin_file": "1011 FLUIDRA.pdf",
      "target_file": "D_604382.PDF",
      "supplier": "FLUIDRA COMERCIAL ESPAÑA, S.A.U",
      "lines_origin": 5,
      "lines_target": 5,
      "lines_ok": 5,
      "incidents_total": 0,
      "overall_status": "ok",
      "output_excel": "/outputs/batch_a1b2c3d4e5_001.xlsx",
      "output_excel_url": "https://aquacenter-mvp-api-aquacenter.rb0pxk.easypanel.host/outputs/batch_a1b2c3d4e5_001.xlsx"
    }
  ]
}
```

## 7) Reglas operativas

- No procesar documento por documento en producción.
- Procesar por lote usando `POST /compare-batch`.
- Mover documentos tras procesar para evitar reprocesos.
- Guardar el Excel global en carpeta `Reportes` de Google Drive.

