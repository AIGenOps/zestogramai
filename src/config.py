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
    owner_telegram_id: Optional[int] = Field(default=None, validation_alias="OWNER_TELEGRAM_ID")
    redis_url: str = Field(default="", validation_alias="REDIS_URL")
    webhook_url: str = Field(default="", validation_alias="WEBHOOK_URL")
    webhook_port: int = Field(default=8443, validation_alias="WEBHOOK_PORT")
    free_limit: int = Field(default=100, validation_alias="FREE_LIMIT")
    pro_limit: int = Field(default=500, validation_alias="PRO_LIMIT")
    pro_duration_days: int = Field(default=30, validation_alias="PRO_DURATION_DAYS")
    quota_window_hours: int = Field(default=24, validation_alias="QUOTA_WINDOW_HOURS")
    
    port: int = Field(default=10000, validation_alias="PORT")
    data_dir: str = Field(default="/data", validation_alias="DATA_DIR")

    payment_email: str = Field(default="theastralx@gmail.com", validation_alias="PAYMENT_EMAIL")
    pro_price_usd: int = Field(default=1, validation_alias="PRO_PRICE_USD")
    unlimited_price_usd: int = Field(default=20, validation_alias="UNLIMITED_PRICE_USD")
    payment_qr_path: Optional[str] = Field(default=None, validation_alias="PAYMENT_QR_PATH")
    admin_forum_group_id: Optional[int] = Field(default=-1004452680578, validation_alias="ADMIN_FORUM_GROUP_ID")
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def validate_payment_config(self) -> bool:
        import logging
        logger = logging.getLogger(__name__)
        is_valid = True
        if not self.payment_email or "@" not in str(self.payment_email):
            logger.error(f"Invalid PAYMENT_EMAIL configured: {self.payment_email}")
            is_valid = False
        try:
            if int(self.pro_price_usd) <= 0:
                logger.error(f"Invalid PRO_PRICE_USD configured: {self.pro_price_usd}")
                is_valid = False
        except (ValueError, TypeError):
            logger.error(f"Invalid PRO_PRICE_USD configured: {self.pro_price_usd}")
            is_valid = False
        try:
            if int(self.unlimited_price_usd) <= 0:
                logger.error(f"Invalid UNLIMITED_PRICE_USD configured: {self.unlimited_price_usd}")
                is_valid = False
        except (ValueError, TypeError):
            logger.error(f"Invalid UNLIMITED_PRICE_USD configured: {self.unlimited_price_usd}")
            is_valid = False
        return is_valid
    
    @field_validator('owner_telegram_id', mode='before')
    def parse_owner_id(cls, v):
        if v == '' or v is None:
            return None
        try:
            return int(v)
        except Exception:
            return None

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

    @field_validator('admin_forum_group_id', mode='before')
    def parse_admin_forum_group_id(cls, v):
        if v == '' or v is None:
            return None
        try:
            return int(v)
        except Exception:
            return None

    @property
    def owner_id(self) -> Optional[int]:
        if self.owner_telegram_id is not None:
            return self.owner_telegram_id
        if self.parsed_admin_user_ids:
            return self.parsed_admin_user_ids[0]
        return None

    @property
    def parsed_admin_user_ids(self) -> List[int]:
        if not self.admin_user_ids:
            return []
        return [int(uid.strip()) for uid in self.admin_user_ids.split(",") if uid.strip().lstrip("-").isdigit()]


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

FREE_LIMIT = config.free_limit
PRO_LIMIT = config.pro_limit
PRO_DURATION_DAYS = config.pro_duration_days
QUOTA_WINDOW_HOURS = config.quota_window_hours
