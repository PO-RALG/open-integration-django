import logging
import unittest

from dotenv import dotenv_values

from .esb import ESB, DataFormat, ESBRequestType
from .sign import Signature
from .xml_handler import XMLHandler

config = dotenv_values('api_requests/.env')
logger = logging.getLogger(__name__)
handler = XMLHandler()


class EsbConnector(unittest.TestCase):

    @classmethod
    def nida_verification(self, nin, rqCode=None, qNANSW=None):
        if rqCode is not None:
            payload = {
                'requestdata': {
                    "Payload": {
                        "NIN": nin,
                        "RQCode": rqCode,
                        "QNANSW": qNANSW
                    }
                }
            }
        else:
            payload = {
                'requestdata': {
                    "Payload": {
                        "NIN": nin,
                    }
                }
            }

        return self.relay_data(request_type=ESBRequestType.nida, api_code=config['NIDA_API_CODE'], payload=payload, user_id="EGA")

    @classmethod
    def send_complaint_to_back_office_system(self, complaint: object, api_code: str):
        payload = {
            'requestdata': {
                'Payload': {
                    "complaint": complaint,
                }
            }
        }

        return self.relay_data(request_type=ESBRequestType.normal, api_code=api_code, payload=payload, user_id=None)

    @classmethod
    def send_complaint_tracking_to_back_office_system(self, complaint: object, api_code: str):
        payload = {
            'requestdata': {
                "Payload": complaint,
            }
        }

        return self.relay_data(request_type=ESBRequestType.normal, api_code=api_code, payload=payload, user_id=None)

    @classmethod
    def send_esb_response(self, success: bool, message: str, payload: object = None):
        esb_response = {
            'success': success,
            'message': message,
            'esbBody': payload
        }

        json_encoded_data = handler.json_encode(esb_response)
        signature = Signature.sign_content(json_encoded_data)
        esb_request = {"data": handler.json_decode(esb_response), "signature": signature}
        return esb_request

    @classmethod
    def relay_data(self, request_type: ESBRequestType, api_code: str, payload: object, user_id: str = None):
        esb_connector = ESB(
            auth_url=config['GOVESB_TOKEN_URL'],
            request_url=config['GOVESB_ENGINE_URL'],
            grant_type=config['GOVESB_GRANT_TYPE'],
            client_id=config['ESB_CLIENT_ID'],
            client_secret=config['ESB_CLIENT_SECRET']
        )

        response = None
        success = False

        if request_type == ESBRequestType.normal:
            response, success = esb_connector.request_data(api_code=api_code, req_body=payload, data_format=DataFormat.JSON)
        elif request_type == ESBRequestType.nida:
            response, success = esb_connector.request_nida_data(api_code=api_code, req_body=payload, data_format=DataFormat.JSON, user_id=user_id)
        elif request_type == ESBRequestType.push:
            response, success = esb_connector.push_data(push_code=api_code, req_body=payload, data_format=DataFormat.JSON)

        if not success:
            logger.error(f"Failed to relay data :: {payload} to/from {api_code}")
            return None, False

        return response, success
