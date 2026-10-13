"""Durable OAuth transitions; a consumed rotating grant is never retried.

Installation locks serialize OAuth, reads and local revocation. An intent is
committed before contacting Zendesk, so process death fails closed on recovery.
"""
from datetime import datetime, timedelta, timezone
import hmac
import logging
import secrets
from uuid import UUID, uuid4
from core.config import settings
from core.db import scoped, auth_db
from core.runtime.audit import audit
from core.security.oauth_vault import OAuthVault, base_path, token_path
from core.connectors import zendesk_oauth as zd


def now():
    return datetime.now(timezone.utc)


def installation(conn, installation_id, *, allow_revoked=False):
    row = conn.execute('SELECT * FROM vendor_installations WHERE id=%s FOR UPDATE', (installation_id,)).fetchone()
    if not row or row['vendor'] != 'zendesk':
        raise zd.OAuthError('oauth_installation_not_found')
    if row['revoked_at'] and not allow_revoked:
        raise zd.OAuthError('oauth_installation_revoked')
    conn.execute("SELECT set_config('app.agent_id',%s,true)", (str(row['agent_id']),))
    return row


def active_agent(conn, row):
    agent = conn.execute('SELECT status FROM agents WHERE id=%s FOR SHARE', (row['agent_id'],)).fetchone()
    if not agent or agent['status'] != 'active':
        raise zd.OAuthError('oauth_agent_inactive')


def emit(conn, row, result, actor='system'):
    audit(conn, row['tenant_id'], row['agent_id'], 'vendor.oauth', result, actor=actor,
          details={'installation_id': str(row['id']), 'vendor': 'zendesk'})


def begin(tenant, installation_id, user_id, session_token):
    redirect = zd.callback_uri(settings.web_origin)
    verifier, challenge = zd.pkce()
    attempt_id, generation = uuid4(), uuid4()
    state = f'{tenant}.{attempt_id}.{secrets.token_urlsafe(32)}'
    browser = secrets.token_urlsafe(32)
    vault = OAuthVault()
    with scoped(tenant) as conn:
        row = installation(conn, installation_id)
        active_agent(conn, row)
        connection = conn.execute('SELECT * FROM vendor_oauth_connections WHERE installation_id=%s FOR UPDATE', (installation_id,)).fetchone()
        # An uncertain grant may have issued a token: disconnect it before authorizing again.
        if connection:
            raise zd.OAuthError('oauth_disconnect_before_reconnecting')
        client = zd.client_config(vault.read(base_path(row) + '/oauth-client'))
        vault.create(base_path(row) + '/oauth-attempts/' + str(attempt_id), {'verifier': verifier})
        conn.execute('''INSERT INTO vendor_oauth_connections(installation_id,tenant_id,agent_id,generation,status)
            VALUES(%s,%s,%s,%s,'pending')''', (installation_id, tenant, row['agent_id'], generation))
        conn.execute('''INSERT INTO vendor_oauth_attempts(id,installation_id,tenant_id,agent_id,user_id,state_hash,
            browser_hash,session_hash,account,client_id,redirect_uri,generation,status,expires_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s)''',
            (attempt_id, installation_id, tenant, row['agent_id'], user_id, zd.digest(state), zd.digest(browser),
             zd.digest(session_token), row['account'], client['client_id'], redirect, generation, now()+timedelta(minutes=10)))
        emit(conn, row, 'authorization_started', str(user_id))
    return zd.authorization_url(row['account'], client['client_id'], redirect, state, challenge), browser


def state_ids(state):
    try:
        tenant, attempt, nonce = state.split('.')
        if len(nonce) != 43 or len(state) > 150:
            raise ValueError()
        return UUID(tenant), UUID(attempt)
    except (ValueError, AttributeError):
        raise zd.OAuthError('oauth_state_invalid') from None


def accept_callback(state, browser, code, denied=False):
    tenant, attempt_id = state_ids(state)
    # Cross-site callback uses a short-lived Lax cookie. The original login session
    # remains Strict and is independently revalidated rather than weakening it.
    with scoped(tenant) as conn:
        attempt = conn.execute('SELECT * FROM vendor_oauth_attempts WHERE id=%s', (attempt_id,)).fetchone()
        if not attempt:
            raise zd.OAuthError('oauth_state_invalid')
        row = installation(conn, attempt['installation_id'])
        attempt = conn.execute('SELECT * FROM vendor_oauth_attempts WHERE id=%s FOR UPDATE', (attempt_id,)).fetchone()
        if (attempt['status'] != 'pending' or attempt['expires_at'] <= now()
                or not hmac.compare_digest(attempt['state_hash'], zd.digest(state))
                or not browser or not hmac.compare_digest(attempt['browser_hash'], zd.digest(browser))):
            raise zd.OAuthError('oauth_state_invalid')
        with auth_db() as auth:
            owner = auth.execute('''SELECT u.id FROM auth_private.sessions s
                JOIN auth_private.users u ON u.id=s.user_id AND u.tenant_id=s.tenant_id
                WHERE s.token_hash=%s AND s.user_id=%s AND s.tenant_id=%s AND s.expires_at>now()
                AND u.active AND u.role='owner' ''', (attempt['session_hash'], attempt['user_id'], tenant)).fetchone()
        if not owner:
            raise zd.OAuthError('oauth_session_expired')
        active_agent(conn, row)
        if row['account'] != attempt['account'] or attempt['redirect_uri'] != zd.callback_uri(settings.web_origin):
            raise zd.OAuthError('oauth_binding_changed')
        if denied or not code:
            conn.execute("UPDATE vendor_oauth_attempts SET status='failed' WHERE id=%s", (attempt_id,))
            conn.execute("UPDATE vendor_oauth_connections SET status='reauth_required',last_error='oauth_denied',updated_at=now() WHERE installation_id=%s", (row['id'],))
            emit(conn, row, 'authorization_denied', str(attempt['user_id']))
            try:
                OAuthVault().remove(base_path(row)+'/oauth-attempts/'+str(attempt_id))
            except zd.OAuthError:
                pass
            return 'authorization_denied'
        conn.execute("UPDATE vendor_oauth_attempts SET status='exchanging' WHERE id=%s", (attempt_id,))
        conn.execute('UPDATE vendor_oauth_connections SET updated_at=now() WHERE installation_id=%s', (row['id'],))
    return complete_exchange(tenant, row['id'], attempt_id, code)


def save_bundle(conn, row, connection, bundle, generation, received_at):
    expires = received_at + timedelta(seconds=bundle['expires_in'])
    refresh_expires = received_at + timedelta(seconds=bundle['refresh_token_expires_in'])
    value = {**bundle, 'value': bundle['access_token'], 'account': row['account'],
             'expires_at': expires.isoformat(), 'generation': str(generation)}
    OAuthVault().create(token_path({**row, 'generation': generation}), value)
    conn.execute('''UPDATE vendor_oauth_connections SET generation=%s,pending_generation=NULL,status='active',expires_at=%s,
        refresh_expires_at=%s,refresh_due_at=%s,last_error=NULL,updated_at=now() WHERE installation_id=%s''',
        (generation, expires, refresh_expires, expires-timedelta(seconds=120), row['id']))
    emit(conn, row, 'authorized' if connection['status'] == 'pending' else 'refreshed')


def fail(conn, row, code):
    conn.execute("UPDATE vendor_oauth_connections SET status='reauth_required',last_error=%s,updated_at=now() WHERE installation_id=%s", (code, row['id']))
    emit(conn, row, code)


def complete_exchange(tenant, installation_id, attempt_id, code):
    vault = OAuthVault()
    with scoped(tenant) as conn:
        row = installation(conn, installation_id)
        connection = conn.execute('SELECT * FROM vendor_oauth_connections WHERE installation_id=%s FOR UPDATE', (installation_id,)).fetchone()
        attempt = conn.execute('SELECT * FROM vendor_oauth_attempts WHERE id=%s FOR UPDATE', (attempt_id,)).fetchone()
        if not connection or connection['status'] != 'pending' or attempt['status'] != 'exchanging' or connection['generation'] != attempt['generation']:
            raise zd.OAuthError('oauth_state_invalid')
        try:
            active_agent(conn, row)
            client = zd.client_config(vault.read(base_path(row) + '/oauth-client'))
            if client['client_id'] != attempt['client_id']:
                raise zd.OAuthError('oauth_binding_changed')
            verifier = vault.read(base_path(row) + '/oauth-attempts/' + str(attempt_id)).get('verifier')
            if not isinstance(verifier,str) or not 43<=len(verifier)<=128:
                raise zd.OAuthError('oauth_vault_unavailable')
            received_at = now()  # conservative lifetime includes request latency
            bundle = zd.exchange(row['account'], client, code=code, verifier=verifier, redirect_uri=attempt['redirect_uri'])
            save_bundle(conn, row, connection, bundle, connection['generation'], received_at)
            conn.execute("UPDATE vendor_oauth_attempts SET status='completed' WHERE id=%s", (attempt_id,))
            result = 'connected_read_only'
        except zd.OAuthError as error:
            fail(conn, row, error.code)
            conn.execute("UPDATE vendor_oauth_attempts SET status='failed' WHERE id=%s", (attempt_id,))
            result = 'authorization_incomplete'
    # Best-effort secret cleanup; failure cannot make a consumed state usable again.
    try:
        vault.remove(base_path(row) + '/oauth-attempts/' + str(attempt_id))
    except zd.OAuthError:
        pass
    return result


def refresh_due(tenant, installation_id):
    retired_path = None
    with scoped(tenant) as conn:
        row = installation(conn, installation_id, allow_revoked=True)
        connection = conn.execute('SELECT * FROM vendor_oauth_connections WHERE installation_id=%s FOR UPDATE', (installation_id,)).fetchone()
        if not connection:
            return
        if connection['status'] in ('pending', 'refreshing'):
            if connection['updated_at'] < now()-timedelta(minutes=15):
                fail(conn, row, 'oauth_interrupted_requires_reauthorization')
            return
        if connection['status'] == 'revoking':
            return  # a separate revocation call can retry safely; do not refresh
        if connection['status'] != 'active' or connection['refresh_due_at'] > now() or row['revoked_at']:
            return
        if connection['refresh_expires_at'] <= now():
            fail(conn, row, 'oauth_refresh_expired')
            return
        try:
            active_agent(conn, row)
        except zd.OAuthError:
            fail(conn, row, 'oauth_agent_inactive')
            return
        generation = uuid4()
        conn.execute("UPDATE vendor_oauth_connections SET status='refreshing',pending_generation=%s,updated_at=now() WHERE installation_id=%s", (generation, installation_id))
    with scoped(tenant) as conn:
        row = installation(conn, installation_id, allow_revoked=True)
        connection = conn.execute('SELECT * FROM vendor_oauth_connections WHERE installation_id=%s FOR UPDATE', (installation_id,)).fetchone()
        if row['revoked_at'] or connection['status'] != 'refreshing' or connection['pending_generation'] != generation:
            return
        try:
            active_agent(conn, row)
            vault = OAuthVault()
            previous = vault.read(token_path({**row, **connection}))
            if not isinstance(previous.get('refresh_token'),str) or not previous['refresh_token']:
                raise zd.OAuthError('oauth_vault_unavailable')
            client = zd.client_config(vault.read(base_path(row) + '/oauth-client'))
            received_at = now()
            bundle = zd.exchange(row['account'], client, refresh_token=previous['refresh_token'])
            save_bundle(conn, row, connection, bundle, generation, received_at)
            retired_path = token_path({**row, **connection})
        except zd.OAuthError as error:
            fail(conn, row, error.code)
    # Delete the previous generation only after its replacement committed.
    if retired_path:
        try:
            OAuthVault().remove(retired_path)
        except zd.OAuthError:
            logging.warning('oauth_retired_secret_cleanup_failed installation=%s', installation_id)


def disconnect(tenant, installation_id, actor):
    # Commit local denial first. It survives unavailable Vault or Zendesk.
    with scoped(tenant) as conn:
        row = installation(conn, installation_id, allow_revoked=True)
        connection = conn.execute('SELECT * FROM vendor_oauth_connections WHERE installation_id=%s FOR UPDATE', (installation_id,)).fetchone()
        if not connection:
            raise zd.OAuthError('oauth_connection_not_found')
        if connection['status'] == 'revoked':
            return 'revoked'
        conn.execute("UPDATE vendor_installations SET status='revoked',revoked_at=COALESCE(revoked_at,now()) WHERE id=%s", (installation_id,))
        conn.execute("UPDATE integrations SET status='revoked' WHERE id=%s", (installation_id,))
        conn.execute("UPDATE vendor_oauth_connections SET status='revoking',updated_at=now() WHERE installation_id=%s", (installation_id,))
        conn.execute("UPDATE vendor_oauth_attempts SET status='failed' WHERE installation_id=%s AND status IN ('pending','exchanging')", (installation_id,))
        emit(conn, row, 'local_access_revoked', str(actor))
    with scoped(tenant) as conn:
        row = installation(conn, installation_id, allow_revoked=True)
        connection = conn.execute('SELECT * FROM vendor_oauth_connections WHERE installation_id=%s FOR UPDATE', (installation_id,)).fetchone()
        status = 'revoked'
        vault = OAuthVault()
        for generation in filter(None, (connection['generation'], connection['pending_generation'])):
            path = token_path({**row, 'generation': generation})
            try:
                bundle = vault.read(path)
                access_token = bundle.get('access_token')
                if not isinstance(access_token, str) or not access_token:
                    raise zd.OAuthError('oauth_vault_unavailable')
                zd.revoke_token(row['account'], access_token)
                vault.remove(path)
            except zd.OAuthError:
                status = 'revocation_unconfirmed'
        conn.execute('UPDATE vendor_oauth_connections SET status=%s,last_error=%s,updated_at=now() WHERE installation_id=%s',
                     (status, None if status == 'revoked' else 'oauth_revocation_unconfirmed', installation_id))
        emit(conn, row, status, str(actor))
    return status
