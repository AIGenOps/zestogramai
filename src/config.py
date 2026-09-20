from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    telegram_bot_token: str
    allowed_user_ids: str = ""
    allowed_chat_ids: str = ""
    
    use_local_bot_api: bool = True
    telegram_api_id: Optional[int] = None
    telegram_api_hash: Optional[str] = None
    
    max_concurrent_downloads: int = 2
    max_retries: int = 2
    cookies_file_path: Optional[str] = "/data/cookies.txt"
    include_caption: bool = True
    keep_files_after_send: bool = False
    
    max_jobs_per_user_per_minute: int = 5
    
    log_level: str = "INFO"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    
    @field_validator('telegram_api_id', mode='before')
    def parse_api_id(cls, v):
        if v == '' or v is None:
            return None
        return int(v)

    @field_validator('telegram_api_hash', mode='before')
    def parse_api_hash(cls, v):
        if v == '' or v is None:
            return None
        return str(v)

    @property
    def parsed_allowed_user_ids(self) -> List[int]:
        if not self.allowed_user_ids:
            return []
        return [int(uid.strip()) for uid in self.allowed_user_ids.split(",") if uid.strip().lstrip("-").isdigit()]

    @property
    def parsed_allowed_chat_ids(self) -> List[int]:
        if not self.allowed_chat_ids:
            return []
        return [int(cid.strip()) for cid in self.allowed_chat_ids.split(",") if cid.strip().lstrip("-").isdigit()]

config = Settings()
