from typing import Any, Iterable, Protocol, TypedDict, Callable
from oauth2client.client import OAuth2Credentials, Storage
from threading import Lock


DELETE_FIELD = object()


class DatastoreFactory:
    def __init__(self) -> None:
        self._factory_fn = None

    def set(self, factory_fn: Callable[[], "Datastore"]) -> None:
        self._factory_fn = factory_fn

    def create(self) -> "Datastore":
        if self._factory_fn is None:
            raise DataError("Datastore factory not set")
        return self._factory_fn()


datastore = DatastoreFactory()


class User(Protocol):
    id: str | None
    exists: bool
    _data: dict[str, Any]

    def get(self, key: str) -> Any: ...


class UserReference(Protocol):
    def set(self, data: dict, merge: bool = False) -> None: ...
    def update(self, fields: dict) -> None: ...
    def get(self) -> User: ...


class OauthSecrets(TypedDict):
    client_id: str
    client_secret: str


class Datastore(Protocol):
    def google_maps_api_key(self) -> str: ...

    def open_weather_api_key(self) -> str: ...

    def google_calendar_secrets(self) -> OauthSecrets: ...

    def google_calendar_credentials(self, key: str) -> OAuth2Credentials | None: ...

    def update_google_calendar_credentials(
        self, key: str, credentials: OAuth2Credentials
    ) -> None: ...

    def delete_google_calendar_credentials(self, key: str) -> None: ...

    def user(self, key: str) -> UserReference: ...

    def users(self) -> Iterable[User]: ...

    def set_user(self, key: str, data: dict) -> None: ...

    def update_user(self, key: str, fields: dict) -> None: ...


class DataError(Exception):
    """An error indicating issues retrieving data."""

    pass


class GoogleCalendarStorage(Storage):
    """Credentials storage for the Google Calendar API using Firestore."""

    def __init__(self, key):
        super(GoogleCalendarStorage, self).__init__(lock=Lock())
        self._firestore = datastore.create()
        self._key = key

    def locked_get(self):
        """Loads credentials from Firestore and attaches this storage."""

        credentials = self._firestore.google_calendar_credentials(self._key)
        if not credentials:
            return None
        credentials.set_store(self)
        return credentials

    def locked_put(self, credentials):
        """Saves credentials to Firestore."""

        self._firestore.update_google_calendar_credentials(self._key, credentials)

    def locked_delete(self):
        """Deletes credentials from Firestore."""

        self._firestore.delete_google_calendar_credentials(self._key)
