"""Per-user saved-session recovery; no fresh OTP for ordinary disconnections."""
import asyncio
import time
from telethon.errors import AuthKeyUnregisteredError, AuthKeyDuplicatedError, SessionRevokedError, UserDeactivatedError, UserDeactivatedBanError, FloodWaitError

class LoginRequired(ValueError):
    pass

class SessionUnavailable(ValueError):
    pass

AUTH_ERRORS=(AuthKeyUnregisteredError,AuthKeyDuplicatedError,SessionRevokedError,UserDeactivatedError,UserDeactivatedBanError)

class SessionRecovery:
    def __init__(self,store,registry,connect):
        self.store=store;self.registry=registry;self.connect=connect
        self.locks={};self.state={}
    def status(self,uid):
        return self.state.get(uid,{'state':'connected' if uid in self.registry else 'saved' if self.store.get(uid,'session') else 'not logged in','error':''})
    async def ensure(self,uid,optional=False,force=False):
        async with self.locks.setdefault(uid,asyncio.Lock()):
            cl=self.registry.get(uid)
            state=self.state.get(uid,{})
            if state.get('state')=='revoked' and not force:
                if optional:return None
                raise LoginRequired('Telegram session expired/revoked. /login is required; settings/routes were retained.')
            if state.get('retry_at',0)>time.time() and not force and not (cl and cl.is_connected()):
                raise SessionUnavailable('Saved login is temporarily unavailable; automatic reconnect will retry. No OTP needed unless Telegram revoked the session.')
            if force and state.get('state')=='revoked' and cl:
                self.registry.pop(uid,None)
                await cl.disconnect()
                cl=None
            saved=self.store.get(uid,'session')
            if not cl and not saved:
                if optional:return None
                raise LoginRequired('Connect your own Telegram account once with /login. Saved logins reconnect automatically.')
            try:
                if cl:
                    if not cl.is_connected():
                        await asyncio.wait_for(cl.connect(),40)
                        if not await cl.is_user_authorized():
                            raise LoginRequired('Telegram revoked the saved session')
                    self.state[uid]={'state':'connected','error':'','attempts':0}
                    return cl
                cl=await asyncio.wait_for(self.connect(uid,saved),90)
                self.state[uid]={'state':'connected','error':'','attempts':0}
                return cl
            except (LoginRequired,)+AUTH_ERRORS as exc:
                self.state[uid]={'state':'revoked','error':type(exc).__name__,'attempts':0}
                if optional:return None
                raise LoginRequired('Telegram invalidated this session. /login again; saved settings/routes are still retained.') from exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempts=state.get('attempts',0)+1
                delay=min(300,5*2**min(attempts,6))
                if isinstance(exc,FloodWaitError):delay=max(delay,exc.seconds+1)
                self.state[uid]={'state':'retrying','error':type(exc).__name__,'attempts':attempts,'retry_at':time.time()+delay}
                if optional:return None
                raise SessionUnavailable('Saved login retained. Temporary connection/capacity issue; automatic reconnect will retry. Do not repeat OTP login.') from exc
    def connected(self,uid):
        self.state[uid]={'state':'connected','error':'','attempts':0}
    def forget(self,uid):
        self.state.pop(uid,None)
