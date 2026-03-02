import base64
import logging
import os
from pathlib import Path

from dotenv import dotenv_values
from ellipticcurve import PublicKey, File, Ecdsa, PrivateKey
from ellipticcurve import Signature as Psignature

config = dotenv_values("api_requests/.env")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PRIVATE_KEY = str(BASE_DIR / "esb_utils" / "signatures" / "privateKey.pem")
DEFAULT_PUBLIC_KEY = str(BASE_DIR / "esb_utils" / "signatures" / "publicKey.pem")


def _cfg(name, default=""):
    value = os.getenv(name)
    if value:
        return value
    return config.get(name, default)


class Signature(object):
    def generate_pair_keys(private_key_file=None, public_key_file=None):
        """ generate ECC keys (curve: secp256k1) for ESB integration at the base dir path"""
        private_key_file = private_key_file or _cfg("CLIENT_PRIVATE_KEY", DEFAULT_PRIVATE_KEY)
        public_key_file = public_key_file or _cfg("CLIENT_PUBLIC_KEY", DEFAULT_PUBLIC_KEY)

        # Generate new Keys
        private_key = PrivateKey()
        public_key = private_key.publicKey()

        prkf = open(private_key_file, 'wt')
        prkf.write(private_key.toPem())
        prkf.close()

        pbkf = open(public_key_file, 'wt')
        pbkf.write(public_key.toPem())
        pbkf.close()

        return True

    def sign_content(content: object):
        """
        Get Signature verification for Outgoing Message for ESB using ECC Algorithm.
        @param payload : Message payload to be signed
        @return: base64 encoded signature
        :param private_key:
        """
        private_key_path = _cfg("CLIENT_PRIVATE_KEY", DEFAULT_PRIVATE_KEY)
        key = File.read(private_key_path)
        
        private_key = PrivateKey.fromPem(key)
        
        signature = Ecdsa.sign(content, private_key)
        return signature.toBase64()

    def verify_esb_signature(content, signature):
        try:
            public_key_path = _cfg("GOV_ESB_PUBLIC_KEY", DEFAULT_PUBLIC_KEY)
            public_key = PublicKey.fromPem(File.read(public_key_path))
            return Ecdsa.verify(content, Psignature.fromBase64(signature), public_key)
        except Exception:
            logger.exception("GovESB signature verification failed")
            return False

    def verify_client_signature(content, signature):
        try:
            public_key_path = _cfg("CLIENT_PUBLIC_KEY", DEFAULT_PUBLIC_KEY)
            public_key = PublicKey.fromPem(File.read(public_key_path))
            return Ecdsa.verify(content, Psignature.fromBase64(signature), public_key)
        except Exception:
            logger.exception("Client signature verification failed")
            return False
