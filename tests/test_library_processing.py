import base64
import tempfile
import uuid
from io import BytesIO
from PIL import Image
from django.test import TestCase, override_settings
from workbench.services import library, library_processing


class ProcessingTests(TestCase):
    def setUp(self):
        self.owner='a'*24
        self.temp=tempfile.TemporaryDirectory()
        self.settings=override_settings(MEDIA_ROOT=self.temp.name)
        self.settings.enable()
        stream=BytesIO()
        Image.new('RGB',(30,30),'white').save(stream,format='PNG')
        self.asset={'name':'原图.png','mime':'image/png','data':base64.b64encode(stream.getvalue()).decode()}

    def tearDown(self):
        self.settings.disable()
        self.temp.cleanup()

    def test_saved_parsed_and_indexed_are_separate_states(self):
        item=library.collect(self.owner,assets=[self.asset],operation_key=str(uuid.uuid4()))
        self.assertEqual(item['parse_state'],'pending')
        self.assertEqual(item['index_state'],'pending')
        library_processing.process_entry(self.owner,item['id'],ocr=lambda path:'独处有助于独立思考。',embed=lambda text:[1.,0.])
        ready=library.detail(self.owner,item['id'])
        self.assertEqual(ready['parse_state'],'ready')
        self.assertEqual(ready['index_state'],'ready')
        self.assertIn('独处',ready['transcript'])
        self.assertEqual(len(ready['assets']),1)

    def test_failed_ocr_retains_original_and_does_not_claim_indexed(self):
        item=library.collect(self.owner,assets=[self.asset],operation_key=str(uuid.uuid4()))
        library_processing.process_entry(self.owner,item['id'],ocr=lambda path:'',embed=lambda text:[1.,0.])
        failed=library.detail(self.owner,item['id'])
        self.assertEqual(failed['parse_state'],'failed')
        self.assertNotEqual(failed['index_state'],'ready')
        self.assertEqual(failed['assets'][0]['sha256'],item['assets'][0]['sha256'])
