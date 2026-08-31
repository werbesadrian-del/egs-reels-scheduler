# egs-reels-scheduler

Publicador cloud de **Instagram Trial Reels** (100% GitHub Actions, PC apagada, coste $0).

- `schedule.json` — cola de publicaciones (hora, clave del video en R2, caption, job_id).
- `published.json` — registro idempotente (job_id → media_id).
- `publish.py` — crea contenedor TRIAL (`trial_params`) → poll → `media_publish`.
- `.github/workflows/publish.yml` — cron cada 5 min + botón manual; concurrency para no duplicar.

Los videos viven en **Cloudflare R2 (bucket privado)** y se sirven con **presigned URLs**.
Los secretos (token IG, App ID/Secret, llaves R2) viven en **GitHub Secrets**, nunca en el repo.

Facebook se maneja aparte (server-side, ya automatizado).
