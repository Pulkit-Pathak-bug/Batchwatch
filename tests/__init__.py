import os

# Tests must never reach the real Gemini API, even if a key is set in the developer's shell.
# AI tests switch it back on explicitly and pass fake clients.
os.environ["BATCHWATCH_AI"] = "off"
