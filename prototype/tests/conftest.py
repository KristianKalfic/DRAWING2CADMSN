import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# server.py legt OUT beim Import an; Tests schreiben nie in das echte out/
os.environ.setdefault("D2C_OUT", tempfile.mkdtemp(prefix="d2c_test_"))
