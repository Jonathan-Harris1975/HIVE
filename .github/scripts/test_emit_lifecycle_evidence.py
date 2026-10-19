import runpy
import json, subprocess, sys, tempfile, unittest
from pathlib import Path
SCRIPT=Path(__file__).with_name("emit_lifecycle_evidence.py")
class EvidenceTests(unittest.TestCase):
 def test_unknowns_are_not_green_and_no_secret_values_are_emitted(self):
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/"evidence.json"
   subprocess.run([sys.executable,str(SCRIPT),"--repository","Example","--head-sha","a"*40,"--trigger-actor","bot","--output",str(out)],check=True,capture_output=True,text=True)
   row=json.loads(out.read_text())
   self.assertEqual(row["final_state"],"UNKNOWN")
   self.assertEqual(row["ci_gate"],"unknown")
   helpers = runpy.run_path(str(SCRIPT))
   self.assertEqual(helpers["result_status"]({"status": "completed", "conclusion": "skipped"}), "unknown")
   self.assertEqual(helpers["result_status"]({"status": "completed", "conclusion": "failure"}), "fail")
   self.assertEqual(helpers["combine_statuses"](["pass", "unknown"]), "unknown")
   self.assertEqual(helpers["combine_statuses"](["pass", "fail"]), "fail")
   self.assertEqual(row["repair_attempts"],0)
   self.assertNotIn("token",json.dumps(row).lower())
   schema=json.loads((SCRIPT.parent.parent/"schemas"/"lifecycle-evidence.schema.json").read_text())
   self.assertEqual(set(row),set(schema["required"]))
 def test_invalid_sha_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   result=subprocess.run([sys.executable,str(SCRIPT),"--repository","Example","--head-sha","bad","--output",str(Path(d)/"e.json")],capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0)
if __name__=="__main__": unittest.main()
