# -*- coding: utf-8 -*-
"""
publish.py — Publicador cloud de Instagram TRIAL REELS (corre en GitHub Actions, PC apagada).
Lee la cola (schedule.json) + registro (published.json), y para cada reel cuya hora ya llegó y
no esté publicado: genera presigned URL desde R2, crea el contenedor TRIAL (trial_params),
hace polling del status y publica con media_publish. Idempotente por job_id.

Config 100% por ENV (GitHub Secrets):
  IG_ACCESS_TOKEN, IG_USER_ID, GRAPH_API_VERSION (opcional),
  APP_ID, APP_SECRET (para chequear expiración del token),
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
Secretos NUNCA se imprimen. El token se enmascara.
"""
import os, sys, json, time, datetime
from urllib import request, parse, error

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE = os.path.join(HERE, "schedule.json")
PUBLISHED = os.path.join(HERE, "published.json")
GRAPH = "https://graph.facebook.com"
VER = os.environ.get("GRAPH_API_VERSION", "v26.0")
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "5"))

def log(stage, msg): print("[%s] %s | %s" % (datetime.datetime.utcnow().strftime("%H:%M:%S"), stage, msg), flush=True)
def mask(t): return (t[:6] + "…" + t[-4:]) if t and len(t) > 12 else "****"

def need(k):
    v = os.environ.get(k)
    if not v: sys.exit("Falta el secret %s" % k)
    return v

def r2_presign(key, expires=7200):
    import boto3
    from botocore.config import Config
    acc = need("R2_ACCOUNT_ID")
    c = boto3.client("s3", endpoint_url="https://%s.r2.cloudflarestorage.com" % acc,
                     aws_access_key_id=need("R2_ACCESS_KEY_ID"),
                     aws_secret_access_key=need("R2_SECRET_ACCESS_KEY"),
                     region_name="auto", config=Config(signature_version="s3v4"))
    return c.generate_presigned_url("get_object", Params={"Bucket": need("R2_BUCKET"), "Key": key}, ExpiresIn=expires)

def api(method, path, params, token):
    data = dict(params); data["access_token"] = token
    if method == "GET":
        req = request.Request(GRAPH + "/" + path + "?" + parse.urlencode(data), method="GET")
    else:
        req = request.Request(GRAPH + "/" + path, data=parse.urlencode(data).encode(), method="POST")
    try:
        with request.urlopen(req) as r:
            return True, json.loads(r.read().decode()), None
    except error.HTTPError as e:
        raw = e.read().decode()
        try: ej = json.loads(raw).get("error", {})
        except Exception: ej = {"message": raw}
        return False, None, {"http": e.code, "code": ej.get("code"), "subcode": ej.get("error_subcode"),
                             "message": ej.get("message"), "fbtrace_id": ej.get("fbtrace_id")}

def check_token(token):
    app_id = os.environ.get("APP_ID"); app_secret = os.environ.get("APP_SECRET")
    if not (app_id and app_secret): return
    ok, d, err = api("GET", "debug_token", {"input_token": token}, app_id + "|" + app_secret)
    if not ok: log("TOKEN", "no pude verificar (%s)" % (err or {}).get("message")); return
    info = d.get("data", {})
    exp = info.get("data_access_expires_at") or info.get("expires_at") or 0
    valid = info.get("is_valid")
    if not valid:
        log("TOKEN", "⚠️ TOKEN INVÁLIDO — hay que regenerarlo"); return
    if exp:
        dias = (exp - time.time()) / 86400
        log("TOKEN", "válido; acceso a datos vence en ~%.0f días" % dias)
        if dias < 7:
            log("TOKEN", "⚠️ EL TOKEN VENCE PRONTO — regenerá y actualizá el secret IG_ACCESS_TOKEN")

def publish_one(item, token, uid):
    key = item["r2_key"]; caption = item.get("caption", ""); grad = item.get("graduation", "MANUAL")
    url = r2_presign(key)
    ok, cont, err = api("POST", "%s/%s/media" % (VER, uid),
                        {"media_type": "REELS", "video_url": url, "caption": caption,
                         "trial_params": json.dumps({"graduation_strategy": grad})}, token)
    if not ok: log("ERROR/CREAR", json.dumps(err)); return None
    cid = cont["id"]; log("CREAR", "contenedor %s (%s)" % (cid, item["job_id"]))
    estado = None
    for _ in range(10):
        ok, st, err = api("GET", "%s/%s" % (VER, cid), {"fields": "status_code,status"}, token)
        if not ok: log("ERROR/ESTADO", json.dumps(err)); return None
        estado = st.get("status_code"); log("ESTADO", estado)
        if estado == "FINISHED": break
        if estado in ("ERROR", "EXPIRED"): log("ERROR/PROCESADO", estado); return None
        time.sleep(30)
    if estado != "FINISHED": log("ERROR/TIMEOUT", cid); return None
    ok, pub, err = api("POST", "%s/%s/media_publish" % (VER, uid), {"creation_id": cid}, token)
    if not ok: log("ERROR/PUBLICAR", json.dumps(err)); return None
    log("OK", "TRIAL REEL publicado media_id=%s" % pub["id"]); return pub["id"]

def main():
    token = need("IG_ACCESS_TOKEN"); uid = need("IG_USER_ID")
    log("INICIO", "token=%s uid=%s ver=%s" % (mask(token), uid, VER))
    check_token(token)
    sched = json.load(open(SCHEDULE, encoding="utf-8")) if os.path.exists(SCHEDULE) else []
    reg = json.load(open(PUBLISHED, encoding="utf-8")) if os.path.exists(PUBLISHED) else {}
    now = datetime.datetime.utcnow()
    def due(it):
        try: return datetime.datetime.fromisoformat(it["when"].replace("Z", "")) <= now and it["job_id"] not in reg
        except Exception: return False
    pend = [it for it in sched if due(it)]
    if not pend:
        log("FIN", "nada pendiente (%d publicados de %d)" % (len(reg), len(sched))); return
    log("PENDIENTES", "%d" % len(pend))
    changed = False
    for it in pend[:MAX_PER_RUN]:
        mid = publish_one(it, token, uid)
        if mid:
            reg[it["job_id"]] = {"media_id": mid, "ts": datetime.datetime.utcnow().isoformat(timespec="seconds")}
            json.dump(reg, open(PUBLISHED, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            changed = True
    log("FIN", "publicados esta corrida: %d" % sum(1 for _ in range(0)) or (len(reg)))
    # marca para el commit del workflow
    if changed and os.environ.get("GITHUB_OUTPUT"):
        open(os.environ["GITHUB_OUTPUT"], "a").write("changed=true\n")

if __name__ == "__main__":
    main()
