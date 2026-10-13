"""Zendesk OAuth endpoints. No credentials or vendor payloads reach the browser."""
import os
from uuid import UUID
from fastapi import Depends, HTTPException, Request, Response, Query
from fastapi.responses import RedirectResponse
from core.config import settings
from core.db import scoped
from core.connectors.zendesk_oauth import OAuthError, CALLBACK_PATH, callback_uri
from core.connectors import oauth_lifecycle as lifecycle
from apps.api.vendor_routes import owner


def enabled():
    return os.getenv('ZENDESK_OAUTH_ENABLED', 'false').lower() == 'true'


def install_oauth_routes(app, current_user, rate_limit):
    @app.post('/api/workspace/vendors/{installation_id}/oauth/start')
    def start(installation_id: UUID, request: Request, response: Response, user=Depends(current_user)):
        owner(user)
        if not enabled():
            raise HTTPException(503, 'Zendesk connection setup is not enabled yet. Contact your onboarding lead.')
        rate_limit('oauth-start:' + str(user['tenant_id']), 10, 3600)
        try:
            url, browser = lifecycle.begin(user['tenant_id'], installation_id, user['id'], request.cookies['wf_session'])
        except OAuthError as error:
            raise HTTPException(409, error.code) from None
        response.set_cookie('wf_oauth', browser, max_age=600, secure=True, httponly=True,
                            samesite='lax', path=CALLBACK_PATH)
        response.headers['Referrer-Policy'] = 'no-referrer'
        return {'authorization_url': url, 'scope': 'Read tickets and user information', 'write_actions_enabled': False}

    @app.get(CALLBACK_PATH)
    def callback(request: Request, state: str = Query(max_length=150), code: str | None = Query(default=None, max_length=4096),
                 error: str | None = Query(default=None, max_length=200)):
        if not enabled():
            raise HTTPException(503, 'Zendesk connection setup is disabled')
        rate_limit('oauth-callback:' + (request.client.host if request.client else 'unknown'), 30, 60)
        try:
            result = lifecycle.accept_callback(state, request.cookies.get('wf_oauth'), code, denied=error is not None)
        except OAuthError:
            result = 'authorization_incomplete'
        # Only the configured origin is a return destination. Never use query redirect targets.
        callback_uri(settings.web_origin)
        response = RedirectResponse(settings.web_origin.rstrip('/') + '/?zendesk_connection=' + result, status_code=303)
        response.delete_cookie('wf_oauth', path=CALLBACK_PATH, secure=True, httponly=True, samesite='lax')
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/workspace/vendors/{installation_id}/oauth')
    def status(installation_id: UUID, user=Depends(current_user)):
        owner(user)
        with scoped(user['tenant_id']) as conn:
            row = conn.execute('''SELECT c.status,c.expires_at,c.refresh_due_at,c.last_error
                FROM vendor_oauth_connections c WHERE c.installation_id=%s''', (installation_id,)).fetchone()
        return {'enabled': enabled(), 'connection': row, 'write_actions_enabled': False}

    @app.post('/api/workspace/vendors/{installation_id}/oauth/revoke')
    def revoke(installation_id: UUID, user=Depends(current_user)):
        owner(user)
        rate_limit('oauth-revoke:' + str(user['tenant_id']), 20, 3600)
        try:
            result = lifecycle.disconnect(user['tenant_id'], installation_id, user['id'])
        except OAuthError as error:
            raise HTTPException(409, error.code) from None
        return {'status': result, 'notice': ('Zendesk token revocation confirmed.' if result == 'revoked'
                else 'Platform access is blocked. Vendor revocation is not confirmed; your onboarding lead must check Zendesk.')}
