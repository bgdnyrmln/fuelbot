# Private Fuel Prices API

    pip install -r requirements.txt
    export API_KEYS="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    echo $API_KEYS
    uvicorn main:app --host 127.0.0.1 --port 8000

    curl -H "X-API-Key: <key>" localhost:8000/prices
    curl -H "X-API-Key: <key>" "localhost:8000/prices?fuel=95"
    curl -H "X-API-Key: <key>" localhost:8000/prices/circlek

Env vars: `API_KEYS` (comma-separated, required), `CACHE_TTL` (seconds, default 600).
# fuelbot
