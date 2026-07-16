"""
photo_linking.py — link chat-attached photos to just-logged health records.

Fork-additive helpers extracted from routes/chat_routes.py. When Iris logs a
meal or training session in a chat turn that carried image attachment(s), these
associate the attached photo with the new record AND auto-file it into the
matching Gallery album ("Food Journal" / "Training Journal"). To avoid
mis-linking an unrelated image, both link only when EXACTLY ONE image is
attached and the record has no photo yet. Best-effort; import src.health_store
and src.gallery_ingest lazily.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _link_meal_photo(owner, meal, att_ids, upload_handler):
    """Associate a just-logged meal with the food photo the user attached.

    Called when Iris logs a meal in a chat turn that carried image attachment(s).
    The image is already a durable upload (att_ids), and the manage_health tool
    returns the new meal — so we link them after the fact. To avoid mis-linking an
    unrelated image, we only link when EXACTLY ONE image is attached and the meal
    has no photo yet. Returns the linked upload id, or None. Best-effort.
    """
    try:
        if not meal or not att_ids or meal.get("photo_upload_id"):
            return None
        meal_id = meal.get("id")
        if not meal_id or upload_handler is None:
            return None
        image_ids = []
        for att_id in att_ids:
            try:
                info = upload_handler.resolve_upload(att_id, owner=owner)
            except Exception:
                info = None
            if info and upload_handler.is_image_file(info.get("name") or "", info.get("mime")):
                image_ids.append(info.get("id") or att_id)
        if len(image_ids) != 1:  # 0 or ambiguous → skip; user can attach via the panel
            return None
        from src import health_store as hs
        if not hs.update_meal(owner, int(meal_id), photo_upload_id=image_ids[0]):
            return None
        # Auto-file the same photo into the "Food Journal" gallery album so it
        # lands there from a single log action — no fragile second tool call by
        # the model (which was mis-passing upload_ids / breaking images).
        try:
            from src.gallery_ingest import ingest_upload
            ingest_upload(owner, image_ids[0], album="Food Journal")
        except Exception:
            logger.debug("Food Journal auto-file skipped", exc_info=True)
        return image_ids[0]
    except Exception:
        logger.exception("Failed to link meal photo")
        return None


def _link_training_photo(owner, session, att_ids, upload_handler):
    """Associate a just-logged training session with the attached photo AND file
    it into the "Training Journal" album. Mirrors _link_meal_photo: link only
    when EXACTLY ONE image is attached and the session has no photo yet."""
    try:
        if not session or not att_ids or session.get("photo_upload_id"):
            return None
        sid = session.get("id")
        if not sid or upload_handler is None:
            return None
        image_ids = []
        for att_id in att_ids:
            try:
                info = upload_handler.resolve_upload(att_id, owner=owner)
            except Exception:
                info = None
            if info and upload_handler.is_image_file(info.get("name") or "", info.get("mime")):
                image_ids.append(info.get("id") or att_id)
        if len(image_ids) != 1:  # 0 or ambiguous → skip
            return None
        from src import health_store as hs
        if not hs.update_training(owner, int(sid), photo_upload_id=image_ids[0]):
            return None
        try:
            from src.gallery_ingest import ingest_upload
            ingest_upload(owner, image_ids[0], album="Training Journal")
        except Exception:
            logger.debug("Training Journal auto-file skipped", exc_info=True)
        return image_ids[0]
    except Exception:
        logger.exception("Failed to link training photo")
        return None
