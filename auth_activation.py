"""Single-use invitation redemption; passwords and tokens are never persisted."""
import time
import hashlib
import hmac
import secrets
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
import cloud_api
import storage

router = APIRouter()
ACTIVATION_COOKIE = 'dose_atlas_activation'
BINDING_COOKIE = 'dose_atlas_activation_binding'
ACTIVATION_TTL = 600
_BINDING_KEY = secrets.token_bytes(32)


def _binding(token, token_hash, kind, expires):
    payload = '\0'.join((token, token_hash, kind, str(expires))).encode()
    return hmac.new(_BINDING_KEY, payload, hashlib.sha256).hexdigest()


def _retry_token(request, token_hash, kind):
    token = request.cookies.get(ACTIVATION_COOKIE, '')
    binding = request.cookies.get(BINDING_COOKIE, '')
    try:
        expires, signature = binding.split('.', 1)
        expires = int(expires)
        if not token or not time.time() < expires <= time.time()+ACTIVATION_TTL+1:
            return None
        if hmac.compare_digest(signature, _binding(token, token_hash, kind, expires)):
            return token
    except (ValueError, TypeError):
        pass
    return None


def _activation_cookie_options():
    return dict(cloud_api.cookie_options(), path='/api/auth/activate')



def pending_response(token, token_hash, kind, expires, detail, status_code):
    response = JSONResponse({'detail': detail}, status_code=status_code,
                            headers={'Cache-Control':'no-store'})
    remaining = max(0, int(expires-time.time()))
    response.set_cookie(ACTIVATION_COOKIE, token, max_age=remaining, **_activation_cookie_options())
    response.set_cookie(BINDING_COOKIE, str(expires)+'.'+_binding(token,token_hash,kind,expires),
                        max_age=remaining, **_activation_cookie_options())
    return response


def redeem(token_hash, kind):
    session = storage._parse(storage._request('POST', '/auth/v1/verify',
        payload={'token_hash': token_hash, 'type': kind}))
    if not isinstance(session, dict) or not isinstance(session.get('access_token'),str) or not session['access_token']:
        raise storage.StorageError('Aktivierungslink ungültig oder abgelaufen.', 401)
    return session


def update_password(token, password):
    user = storage._parse(storage._request('PUT', '/auth/v1/user', token=token,
                     payload={'password': password}))
    return user if isinstance(user,dict) else {}


@router.post('/api/auth/activate')
async def activate(request: Request):
    cloud_api.same_origin(request)
    if not storage.config_status()['enabled']:
        raise HTTPException(404, 'Anmeldung ist hier nicht eingerichtet.')
    address = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with cloud_api.CACHE_LOCK:
        for ip in list(cloud_api.LOGIN_ATTEMPTS):
            cloud_api.LOGIN_ATTEMPTS[ip] = [t for t in cloud_api.LOGIN_ATTEMPTS[ip] if t > now - 60]
            if not cloud_api.LOGIN_ATTEMPTS[ip]: cloud_api.LOGIN_ATTEMPTS.pop(ip)
        if len(cloud_api.LOGIN_ATTEMPTS.get(address, [])) >= 5 or len(cloud_api.LOGIN_ATTEMPTS) > 1000:
            raise HTTPException(429, 'Zu viele Versuche. Eine Minute warten.')
        cloud_api.LOGIN_ATTEMPTS.setdefault(address, []).append(now)
    try: body = await request.json()
    except ValueError: raise HTTPException(422, 'Ungültige Aktivierungsdaten.') from None
    if not isinstance(body, dict): raise HTTPException(422, 'Ungültige Aktivierungsdaten.')
    token_hash = body.get('token_hash'); kind = body.get('type'); password = body.get('password')
    if (not isinstance(token_hash, str) or not 20 <= len(token_hash) <= 512 or
            not token_hash.isalnum() or kind not in ('invite', 'recovery') or
            not isinstance(password, str) or not 12 <= len(password) <= 1024):
        raise HTTPException(422, 'Aktivierungslink prüfen; Passwort mindestens 12 Zeichen.')
    email = body.get('email')
    if email is not None and (not isinstance(email,str) or not 3 <= len(email.strip()) <= 320 or '@' not in email):
        raise HTTPException(422, 'Ungültige Aktivierungsdaten.')
    token = _retry_token(request, token_hash, kind)
    expires = None
    data = {}
    if token:
        # Keep the original deadline; failed retries never extend it.
        expires = int(request.cookies[BINDING_COOKIE].split('.',1)[0])
    else:
        data = await run_in_threadpool(redeem, token_hash, kind)
        token = data['access_token']
        expires = int(time.time()) + ACTIVATION_TTL
    verified = await run_in_threadpool(storage.auth_get_user, token)
    if email is not None and email.strip().lower() != str(verified.get('email','')).strip().lower():
        return pending_response(token, token_hash, kind, expires, 'Zugang nicht freigeschaltet.', 403)
    try:
        user = await run_in_threadpool(update_password, token, password)
    except storage.StorageError as error:
        return pending_response(token, token_hash, kind, expires,
            'Passwort konnte nicht gesetzt werden. Bitte innerhalb von 10 Minuten erneut versuchen.', error.status_code)
    response = JSONResponse(dict(enabled=True, authenticated=True,
        email=user.get('email', data.get('user',{}).get('email','')), storage_mode='metadata'),
        headers={'Cache-Control':'no-store'})
    response.set_cookie(cloud_api.COOKIE, token,
        max_age=min(int(data.get('expires_in', 3600)), 86400), **cloud_api.cookie_options())
    response.delete_cookie(ACTIVATION_COOKIE, **_activation_cookie_options())
    response.delete_cookie(BINDING_COOKIE, **_activation_cookie_options())
    return response
