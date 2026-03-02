import base64
import json
import logging
from enum import Enum

import requests
from dotenv import dotenv_values

from .sign import Signature
from .utils import ESBUtils
from .xml_handler import XMLHandler

handler = XMLHandler()
dotenv_values('api_requests/.env')
logger = logging.getLogger(__name__)


class DataFormat(Enum):
    JSON = "json"
    XML = "xml"


class ESBRequestType(Enum):
    normal = "/request"
    nida = "/nida-request"
    push = "/push-request"
    async_request='/async-response'


class ESB(object):
    """
        Class for exchanging data to/from Gov-ESB
        i.e method 'request_data' for consuming data & method 'post_data' for sending data
    """

    def __init__(self, auth_url: str, request_url: str, grant_type: str, client_id: str, client_secret: str):
        self.auth_url = auth_url
        self.request_url = request_url
        self.grant_type = grant_type
        self.client_id = client_id
        self.client_secret = client_secret

    def get_esb_access_token(self):
        try:
            payload = {'client_id': self.client_id, 'client_secret': self.client_secret, 'grant_type': self.grant_type}

            client_credentials = str(str(self.client_id) + ":" + str(self.client_secret)).encode("ascii")
            base64_bytes_credentials = base64.b64encode(client_credentials)
            headers = {'Authorization': 'Basic ' + base64_bytes_credentials.decode("ascii")}

            response = requests.request("POST", self.auth_url, headers=headers, data=payload)
            
            if response.status_code == 200:
                json_response = json.loads(response.text)
                return json_response['access_token'], True
            else:
                return '', False
        except Exception as e:
            logging.warning(str(e))
            return '', False

    def request_nida_data(self, api_code: str, req_body, data_format: DataFormat, user_id=None):
        token, success = self.get_esb_access_token()
        if not success:
            raise Exception("Invalid client credentials")

        if api_code is None or req_body is None or data_format is None:
            raise ValueError('api_code, req_body and format can not be empty')

        if data_format == DataFormat.XML:  # if data is XML
            esb_request_body = handler.create_xml_request(dict_data=req_body, is_push=False, user_id=user_id)
        else:  # if data is JSON
            esb_request_body = handler.create_json_request(api_code=api_code, dict_data=req_body, is_push=False, user_id=user_id)

        req_headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/' + str(data_format.value)}

        response = requests.request("POST", self.request_url + ESBRequestType.nida.value, headers=req_headers, data=esb_request_body)
        if response.status_code == 200:  # if status code is 200
            if data_format == DataFormat.XML:  # if data is XML
                return ESBUtils(response=response.text), True
            elif data_format == DataFormat.JSON:  # if data is JSON
                json_response = json.loads(response.text)

                verified = Signature.verify_esb_signature(content=handler.json_encode(json_response['data']), signature=json_response['signature'])
                if not verified:
                    return ESBUtils(response=json_response), False
        else:  # if status code != 200
            return ESBUtils(response=response.text), False

    def request_data(self, api_code: str, req_body, data_format: DataFormat):
        token, success = self.get_esb_access_token()
        
        if not success:
            raise Exception("Invalid client credentials")

        if api_code is None or req_body is None or data_format is None:
            raise ValueError('api_code, req_body and format can not be empty')

        if data_format == DataFormat.XML:  # if data is XML
            esb_request_body = handler.create_xml_request(dict_data=req_body, is_push=False, user_id=None)
        else:  # if data is JSON
            esb_request_body = handler.create_json_request(api_code=api_code, dict_data=req_body, is_push=False, user_id=None)

        logger.info(f"json payload sent=======> {esb_request_body}")
        
        req_headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/' + str(data_format.value)}

        response = requests.request("POST", self.request_url + ESBRequestType.normal.value, headers=req_headers, data=esb_request_body)
        
        if response.status_code == 200:  # if status code is 200
            if data_format == DataFormat.XML:  # if data is XML
                return ESBUtils(response=response.text), True
            elif data_format == DataFormat.JSON:  # if data is JSON
                json_response = json.loads(response.text)
                verified = Signature.verify_esb_signature(content=handler.json_encode(json_response['data']), signature=json_response['signature'])

                if not verified:
                    return ESBUtils(response=json_response), False
                return ESBUtils(response=json_response), True
        else:  # if status code != 200
            return ESBUtils(response=response.text), False
        
    def send_async_response(self, request_id: str,success: bool, req_body, data_format: DataFormat):
        token, success = self.get_esb_access_token()
        
        if not success:
            raise Exception("Invalid client credentials")

        if data_format == DataFormat.XML:  # if data is XML
            esb_request_body = handler.create_xml_request(dict_data=req_body, is_push=False, user_id=None)
        else:  # if data is JSON
            esb_request_body = handler.create_json_request(request_id=request_id, success=success, dict_data=req_body, is_push=False, user_id=None)
        
        logger.info(f"json payload sent=======> {esb_request_body}")

        req_headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/' + str(data_format.value)}

        response = requests.request("POST", self.request_url + ESBRequestType.async_request.value, headers=req_headers, data=esb_request_body)
        
        returned_response=ESBUtils(response=response)
        logger.info(f"status code {response.status_code} returned response{returned_response}")
        
        if response.status_code == 200:  # if status code is 200
            if data_format == DataFormat.XML:  # if data is XML
                return ESBUtils(response=response.text), True
            elif data_format == DataFormat.JSON:  # if data is JSON
                json_response = json.loads(response.text)
                verified = Signature.verify_esb_signature(content=handler.json_encode(json_response['data']), signature=json_response['signature'])

                if not verified:
                    return ESBUtils(response=json_response), False
                return ESBUtils(response=json_response), True
        else:  # if status code != 200
            return ESBUtils(response=response.text), False

    def push_data(self, push_code: str, req_body, data_format: DataFormat):
        token, success = self.get_esb_access_token()
        
        if not success:
            raise Exception("Invalid client credentials")

        if push_code is None or req_body is None or data_format is None:
            raise ValueError('push_code, req_body and format can not be empty')

        if data_format == DataFormat.XML:  # if data is XML
            esb_request_body = handler.create_xml_request(dict_data=req_body, is_push=True, user_id=None)
        else:  # if data is JSON
            esb_request_body = handler.create_json_request(api_code=push_code, dict_data=req_body, is_push=True, user_id=None)
        
        req_headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/' + str(data_format.value)}
        
        response = requests.request("POST", self.request_url + ESBRequestType.push.value, headers=req_headers, data=esb_request_body)
        
        if response.status_code == 200:  # if status code is 200
            if data_format == DataFormat.XML:  # if data is XML
                return ESBUtils(response=response.text), True
            elif data_format == DataFormat.JSON:  # if data is JSON
                json_response = json.loads(response.text)
                print("Json response",json_response)
                verified = Signature.verify_esb_signature(content=handler.json_encode(json_response['data']), signature=json_response['signature'])
                if not verified:
                    return ESBUtils(response=json_response), False
                return json_response
        else:  # if status code != 200
            return ESBUtils(response=response.text), False
        
    def send_esb_response(self, success: bool, message: str, payload: object = None):
        esb_response = {
            'success': success,
            'message': message,
            'esbBody': payload
        }
        json_encoded_data = handler.json_encode(esb_response)
        json_encoded_data = json.dumps(esb_response, ensure_ascii=False)
        logger.debug(f"GovESB response payload before verification/signing after encode: {json_encoded_data}")
        signature = Signature.sign_content(json_encoded_data)
        verified = Signature.verify_client_signature(json_encoded_data, signature)
        esb_request = {"data": handler.json_decode(esb_response), "signature": signature}
        return esb_request
