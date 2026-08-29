import logging

from telegram import Update

from . import config

logger = logging.getLogger(__name__)


def is_allowed(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    if not config.ALLOWED_USER_IDS:
        logger.error("ALLOWED_USER_IDS no configurado: bloqueando acceso por seguridad (user %s)", user_id)
        return False
    if user_id in config.ALLOWED_USER_IDS:
        return True
    logger.warning("Acceso denegado a user %s", user_id)
    return False
