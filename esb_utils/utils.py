import json
import logging

from .sign import Signature

logger = logging.getLogger(__name__)


class ESBUtils(object):

    def __init__(self, response):
        self.response = response

    def get_body(self):
        return self.response

    def get_request_id(self):
        return self.response['data']['requestId']

    def get_esb_body(self):
        return self.response['data']['esbBody']

    def get_success_status(self):
        return self.response['data']['success']


def verify_govesb_body(esb_body, signature):
    """
    Serialize the provided body, log before/after verification, and
    return the GovESB signature verification result.
    """
    if not esb_body or not signature:
        logger.debug("Missing esb_body or signature; skipping GovESB verification (govesb=False)")
        return False

    try:
        serialized_body = json.dumps(esb_body, separators=(",", ":"))
    except (TypeError, ValueError):
        logger.exception("Unable to serialize GovESB body for signature verification (govesb=True)")
        return False

    logger.info("GovESB body detected (govesb=True) before verification: %s", esb_body)
    verified = Signature.verify_esb_signature(serialized_body, signature)
    logger.info(
        "GovESB body (govesb=True) after verification: verified=%s body=%s",
        verified,
        esb_body,
    )
    return verified
