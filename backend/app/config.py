from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    asset_root: str
    shared_password_hash: str

    # All nullable — the app runs with zero Etsy connection at every stage until these
    # are set. etsy_client_id/secret come from a developer app registered at
    # developers.etsy.com; public_base_url is this backend's externally-reachable URL,
    # used to build the OAuth redirect_uri.
    etsy_client_id: str | None = None
    etsy_client_secret: str | None = None
    public_base_url: str | None = None
    etsy_poll_interval_minutes: int = 15

    # eBay integration, same optional/nullable convention as the Etsy fields above —
    # register a developer app at developer.ebay.com to get these.
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    # eBay's OAuth redirect_uri is NOT a literal URL like Etsy's — it's an opaque
    # "RuName" identifier eBay assigns when you register a redirect configuration in
    # the dev portal (the real callback URL is entered once into that portal's "Auth
    # accepted URL" field, not passed dynamically here). See routers/platforms.py's
    # _redirect_uri, which returns this value for eBay instead of building a URL.
    ebay_ru_name: str | None = None

    # Symmetric key used to encrypt platform OAuth tokens at rest (see
    # app/services/crypto.py). Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Required before an Etsy connection can be created or read — nullable here only so
    # the app can still boot with zero platform integrations configured.
    token_encryption_key: str | None = None


settings = Settings()
