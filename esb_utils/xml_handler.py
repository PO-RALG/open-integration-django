import json
import xml.etree.ElementTree as ET
from .sign import Signature
from xml.dom import minidom


class XMLHandler:
    def create_xml_request(self, dict_data, is_push=False, user_id=None):

        dict_data = self.json_decode(dict_data)

        doc = ET.Element("esbrequest")
        data = ET.SubElement(doc, "data")

        if is_push:
            ET.SubElement(data, "pushCode").text = dict_data['api_code']
        else:
            ET.SubElement(data, "apiCode").text = dict_data['api_code']

        if user_id:
            ET.SubElement(data, "userId").text = str(user_id)

        if dict_data['request_body']:
            if not self.is_valid_xml(dict_data['request_body']):
                raise Exception("Invalid xml")

            dict_data['request_body'] = self.format_xml(dict_data['request_body'])

            if user_id:
                dict_data['request_body'] = "<Payload>" + dict_data['request_body'] + "</Payload>"

            xml_string = str(dict_data['request_body']).replace('<?xml version="1.0" ?>', '')

            esb_body = ET.fromstring("<esbBody>" + xml_string + "</esbBody>")
            data.append(esb_body)

        data_string = ET.tostring(data, encoding="unicode", method="xml", xml_declaration=True)
        signature = Signature.sign_content(data_string)
        ET.SubElement(doc, "signature").text = signature

        return ET.tostring(doc, encoding="unicode", method="xml", xml_declaration=True)

    def is_valid_xml(self, xml):
        try:
            ET.fromstring(xml)
            return True
        except ET.ParseError:
            return False

    def format_xml(self, xml):
        sxe = ET.fromstring(xml)
        dom_element = minidom.parseString(ET.tostring(sxe, encoding="utf-8"))
        return dom_element.toprettyxml(indent="")

    def create_json_request(self, dict_data, api_code=None, is_push=False, user_id=None,request_id=None,success=None):
        data = {}
        if not isinstance(dict_data['requestdata'], dict):
            raise ValueError("Invalid esbBody")

        dict_data = self.json_decode(dict_data)

        if is_push:
            data["pushCode"] = api_code
        elif api_code is not None:
            data["apiCode"] = api_code
        
        
        if success:
            data["success"]=success
        
        if request_id is not None:
            data["requestId"]=request_id

        if user_id:
            data["userId"] = user_id

            if "Payload" in dict_data['requestdata']:
                data["esbBody"] = dict_data['requestdata']
            else:
                data["esbBody"] = {"Payload": dict_data['requestdata']}
        else:
            data["esbBody"] = dict_data['requestdata']

        enc_data = self.json_encode(data)
        print(enc_data)
        signature = Signature.sign_content(content=enc_data)
        esb_request = {"data": data, "signature": signature}
        return self.json_encode(esb_request)

    def json_encode(self, data):
        return json.dumps(data, ensure_ascii=False, separators=(',', ':'))

    def json_decode(self, data):
        return json.loads(json.dumps(data))
