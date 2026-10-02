"""Schlanker Client für die offizielle Blizzard Game Data / Profile API."""
import re
import threading
import time

import requests

TOKEN_URL = "https://oauth.battle.net/token"


class ApiError(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, per_second):
        self.interval = 1.0 / max(1, per_second)
        self.lock = threading.Lock()
        self.next_time = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if self.next_time < now:
                self.next_time = now
            delay = self.next_time - now
            self.next_time += self.interval
        if delay > 0:
            time.sleep(delay)


class BlizzardAPI:
    def __init__(self, cfg):
        b = cfg["blizzard"]
        if not b.get("client_id") or not b.get("client_secret"):
            raise ApiError(
                "client_id / client_secret fehlen in config.toml. "
                "Lege sie unter https://develop.battle.net/access/clients an."
            )
        self.client_id = b["client_id"]
        self.client_secret = b["client_secret"]
        self.region = b.get("region", "eu").lower()
        self.locale = b.get("locale", "de_DE")
        self.base = f"https://{self.region}.api.blizzard.com"
        self.limiter = RateLimiter(int(b.get("requests_per_second", 25)))
        self._local = threading.local()
        self._token = None
        self._token_exp = 0.0
        self._token_lock = threading.Lock()

    # --- Grundlagen -------------------------------------------------------
    def _session(self):
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def token(self, force=False):
        with self._token_lock:
            if not force and self._token and time.time() < self._token_exp - 60:
                return self._token
            r = requests.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                timeout=30,
            )
            if r.status_code in (400, 401):
                raise ApiError("Anmeldung bei Blizzard fehlgeschlagen – client_id / client_secret prüfen.")
            r.raise_for_status()
            data = r.json()
            self._token = data["access_token"]
            self._token_exp = time.time() + int(data.get("expires_in", 3600))
            return self._token

    def get(self, path, namespace, params=None, raw=False, timeout=60):
        url = self.base + path
        query = {"namespace": f"{namespace}-{self.region}", "locale": self.locale}
        if params:
            query.update(params)
        last_status = None
        for attempt in range(6):
            self.limiter.wait()
            try:
                r = self._session().get(
                    url,
                    params=query,
                    headers={"Authorization": f"Bearer {self.token()}"},
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                last_status = str(exc)
                time.sleep(2 ** attempt)
                continue
            last_status = r.status_code
            if r.status_code == 401:
                self.token(force=True)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code in (403, 404):
                return None
            r.raise_for_status()
            return r if raw else r.json()
        raise ApiError(f"API-Anfrage fehlgeschlagen ({last_status}): {path}")

    # --- Realm & Auktionen ------------------------------------------------
    def connected_realm_id(self, realm_slug):
        data = self.get(f"/data/wow/realm/{realm_slug}", "dynamic")
        if not data:
            raise ApiError(f"Realm '{realm_slug}' nicht gefunden. Slug prüfen (z. B. 'blackmoore', 'die-aldor').")
        match = re.search(r"connected-realm/(\d+)", data["connected_realm"]["href"])
        return int(match.group(1))

    def commodities(self):
        """Regionsweite Rohstoff-Auktionen (Erze, Kräuter, Leder, Reagenzien ...)."""
        return self.get("/data/wow/auctions/commodities", "dynamic", raw=True, timeout=180)

    def realm_auctions(self, connected_realm_id):
        """Realm-gebundene Auktionen (Ausrüstung, Deko u. a. Nicht-Rohstoffe)."""
        return self.get(f"/data/wow/connected-realm/{connected_realm_id}/auctions", "dynamic", raw=True, timeout=180)

    # --- Berufe & Rezepte -------------------------------------------------
    def profession_index(self):
        return self.get("/data/wow/profession/index", "static")

    def profession(self, profession_id):
        return self.get(f"/data/wow/profession/{profession_id}", "static")

    def skill_tier(self, profession_id, tier_id):
        return self.get(f"/data/wow/profession/{profession_id}/skill-tier/{tier_id}", "static")

    def recipe(self, recipe_id):
        return self.get(f"/data/wow/recipe/{recipe_id}", "static")

    def character_professions(self, realm_slug, name):
        return self.get(f"/profile/wow/character/{realm_slug}/{name.lower()}/professions", "profile")
