from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    keitaro_base_url: str = "https://tlgk.host"
    keitaro_api_key: str = ""
    database_url: str = "sqlite:///./adrobot.db"
    campaign_prefix: str = "test_surname_"
    google_redirect_url: str = "https://www.google.com/"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
