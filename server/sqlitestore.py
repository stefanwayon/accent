from dataclasses import dataclass
from logging import error, info, warning
from os import PathLike
from sqlite3 import connect
from typing import Any, Iterable, Literal

from googleapiclient.http import build_http
from oauth2client.client import (
    HttpAccessTokenRefreshError,
    OAuth2Credentials,
)
from pydantic import TypeAdapter, validate_call

from datastore import DataError, Datastore, OauthSecrets, User, UserReference


DELETE_FIELD = object()


APIService = Literal["google_maps", "open_weather"]


@dataclass
class SQLiteUser(User):
    exists: bool
    _key: str | None
    _data: dict[str, Any]

    @property
    def id(self) -> str | None:
        return self._key

    def get(self, key: str) -> Any:
        return self._data[key]


class SQLiteUserReference(UserReference):
    key: str

    def __init__(self, key: str, db_path: str | PathLike) -> None:
        self.key = key
        self._db_path = db_path

    def set(self, data: dict, merge: bool = False) -> None:
        if merge:
            user_data = self.get()._data
            for k, v in data.items():
                if v is DELETE_FIELD and k in user_data:
                    del user_data[k]
                else:
                    user_data[k] = v
        else:
            user_data = data

        try:
            with connect(self._db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT OR REPLACE INTO users (key, user_data_json) VALUES (?, ?)",
                    (self.key, TypeAdapter(dict[str, Any]).dump_json(user_data)),
                )
        except Exception as e:
            raise DataError(f"Failed to set user data: {e}")

    def update(self, fields: dict) -> None:
        if self.get().exists:
            self.set(fields, merge=True)
        else:
            raise DataError(f"User not found: {self.key}")

    def get(self) -> SQLiteUser:
        with connect(self._db_path) as conn:
            cur = conn.cursor()
            res = cur.execute(
                "SELECT key, user_data_json FROM users WHERE key = ?", (self.key,)
            )

            if (user := res.fetchone()) is None:
                return SQLiteUser(False, None, {})

            key, user_data_json = user
            user_data = TypeAdapter(dict[str, Any]).validate_json(user_data_json)
            assert key == self.key

            return SQLiteUser(True, key, user_data)


class SQLiteStore(Datastore):
    def __init__(self, path: str | PathLike) -> None:
        self._db_path = path

    @validate_call(validate_return=True)
    def _api_key(self, service: APIService) -> str:
        with connect(self._db_path) as conn:
            cur = conn.cursor()
            res = cur.execute(
                "SELECT api_key FROM api_keys WHERE service = ?", (service,)
            )
            if (api_key := res.fetchone()) is None:
                raise DataError(f"Missing API key for: {service}")

            if not isinstance(api_key[0], str):
                raise DataError(f"Invalid API key for: {service}")

            return api_key[0]

    def google_maps_api_key(self) -> str:
        return self._api_key("google_maps")

    def open_weather_api_key(self) -> str:
        return self._api_key("open_weather")

    def google_calendar_secrets(self) -> OauthSecrets:
        with connect(self._db_path) as conn:
            cur = conn.cursor()
            res = cur.execute(
                "SELECT client_id, client_secret FROM oauth_clients WHERE service = 'google_calendar'"
            )

            if (secrets := res.fetchone()) is None:
                raise DataError("Missing Google Calendar secrets")

            client_id, client_secret = secrets

            return {"client_id": client_id, "client_secret": client_secret}

    def google_calendar_credentials(self, key) -> OAuth2Credentials | None:
        """Loads and refreshes Google Calendar API credentials."""

        # Look up the user from the key.
        user = self.user(key)
        if not user:
            return None

        # Load the credentials from storage.
        try:
            json = user.get("google_calendar_credentials")
        except KeyError:
            warning("Failed to load Google Calendar credentials.")
            return None

        # Use the valid credentials.
        credentials = OAuth2Credentials.from_json(json)
        if credentials and not credentials.invalid:
            return credentials

        # Handle invalidation and expiration.
        if credentials and credentials.access_token_expired:
            try:
                info("Refreshing Google Calendar credentials.")
                credentials.refresh(build_http())
                return credentials
            except HttpAccessTokenRefreshError as e:
                warning("Google Calendar refresh failed: %s" % e)

        # Credentials are missing or refresh failed.
        warning("Deleting Google Calendar credentials.")
        self.delete_google_calendar_credentials(key)
        return None

    def update_google_calendar_credentials(
        self, key: str, credentials: OAuth2Credentials
    ):
        """Updates the users's Google Calendar credentials."""

        self.update_user(key, {"google_calendar_credentials": credentials.to_json()})

    def delete_google_calendar_credentials(self, key: str):
        """Deletes the users's Google Calendar credentials."""

        self.update_user(key, {"google_calendar_credentials": DELETE_FIELD})

    def _user_reference(self, key) -> SQLiteUserReference:
        return SQLiteUserReference(key, self._db_path)

    def user(self, key: str) -> SQLiteUser | None:
        if not (user := self._user_reference(key).get()).exists:
            warning(f"User not found: {key}")
            return None

        return user

    def users(self) -> Iterable[SQLiteUser]:
        with connect(self._db_path) as conn:
            cur = conn.cursor()
            res = cur.execute("SELECT key FROM users")

            for (key,) in res:
                yield self._user_reference(key).get()

    def set_user(self, key: str, data: dict[str, Any]) -> None:
        self._user_reference(key).set(data, merge=True)

    def update_user(self, key: str, fields: dict[str, Any]) -> None:
        user = self._user_reference(key)
        if not user.get().exists:
            error(f"User not found for update: {key}")
            return

        user.update(fields)
