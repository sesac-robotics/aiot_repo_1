import re
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

DIR=Path(__file__).resolve().parents[1]
TABLES={'persons','patient_profiles','face_templates','rfid_cards','cameras',
        'fall_events','alerts','identification_events','access_events','doors','door_commands'}

class TestArtifacts(unittest.TestCase):
    def test_all_tables_appear_in_sql(self):
        sql=(DIR/'schema.sql').read_text()
        names=set(re.findall(r'CREATE TABLE IF NOT EXISTS hcc\.([a-z_]+)',sql))
        self.assertEqual(names,TABLES)
        self.assertIn('vector(512)',sql)
        self.assertIn('CREATE EXTENSION IF NOT EXISTS vector',sql)

    def test_diagram_parses_and_contains_all_tables(self):
        tree=ET.parse(DIR/'HCC_ERD_v0.1.drawio')
        self.assertEqual(len(tree.findall('./diagram')),3)
        diagrams=tree.findall('./diagram')
        ids={c.get('id') for c in diagrams[0].findall('.//mxCell')}
        for t in TABLES:self.assertIn('p1-'+t,ids)
        self.assertGreater(len([x for x in ids if 'edge' in x]),10)

if __name__=='__main__':unittest.main()
