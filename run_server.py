"""Run the Flask server for CCE Audio (Bootsafe).

Reads ./meta.json for preferred port.
"""
import os
from app import create_app, load_meta

if __name__ == "__main__":
    meta = load_meta()
    port = int(os.environ.get("PORT", meta.get("port", 5204)))
    create_app().run(host="127.0.0.1", port=port, debug=False)
